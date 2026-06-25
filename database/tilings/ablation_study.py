import os
import sqlite3
import pickle
import copy
import numpy as np
import faiss
import csv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import time

# --- Import Core Engine & DB logic ---
from database.tilings.build_tilings import Tiling 
from src.engine.tree import extract_eigenvalues, EIG_COUNT, RESOLUTION

# --- Import parameters from your existing cache files ---
from database.tilings.faiss_cache_hkt import get_t_scales
from database.tilings.faiss_cache import DIMENSION, E_SWEEP, TWO_VARIANCE, compute_wks_signature
import pandas as pd
import matplotlib.pyplot as plt

from sklearn.decomposition import PCA
from sklearn.manifold import TSNE
import plotly.express as px
# =============================================================================
# 1. CORE EMBEDDING FUNCTIONS (Cleanly separated)
# =============================================================================

def compute_hkt_base(eigenvalues, dim=DIMENSION):
    """ Heat Kernel Trace (Normalized to start near 1.0) """
    t_scales = get_t_scales(dim)
    is_1d = eigenvalues.ndim == 1
    if is_1d: eigenvalues = eigenvalues.reshape(1, -1)
        
    N_samples = eigenvalues.shape[0]
    signatures = np.zeros((N_samples, dim), dtype=np.float32)
    
    for j, t in enumerate(t_scales):
        decayed = np.exp(-t * eigenvalues)
        signatures[:, j] = np.sum(decayed, axis=1)
        
    norms = signatures[:, 0:1]
    norms[norms == 0] = 1.0
    signatures = signatures / norms
    
    if is_1d: return signatures[0]
    return signatures

def compute_wks_base(eigenvalues, dim=DIMENSION):
    """ Wave Kernel Signature (Raw Probability Density Function) """
    is_1d = eigenvalues.ndim == 1
    if is_1d: eigenvalues = eigenvalues.reshape(1, -1)
        
    N_samples = eigenvalues.shape[0]
    signatures = np.zeros((N_samples, dim), dtype=np.float32)
    
    valid_mask = eigenvalues > 1e-6
    safe_eigs = np.where(valid_mask, eigenvalues, 1.0) 
    log_eigs = np.log(safe_eigs)
    
    for j, e in enumerate(E_SWEEP):
        squared_diff = (e - log_eigs) ** 2
        band_pass = np.exp(-squared_diff / TWO_VARIANCE)
        band_pass = np.where(valid_mask, band_pass, 0.0)
        signatures[:, j] = np.sum(band_pass, axis=1)
        
    if is_1d: return signatures[0]
    return signatures

def compute_cwks_base(eigenvalues, dim=DIMENSION):
    """ Cumulative Wave Kernel Signature (Mass-Normalized CDF) """
    # Get the raw PDF from the function above
    signatures = compute_wks_base(eigenvalues, dim)
    is_1d = signatures.ndim == 1
    if is_1d: signatures = signatures.reshape(1, -1)
    
    # Convert PDF to CDF
    cumulative = np.cumsum(signatures, axis=1)
    
    # Normalize by Total Spectral Mass
    row_max = cumulative[:, -1:]
    row_max[row_max == 0] = 1.0
    cdf = cumulative / row_max
    
    if is_1d: return cdf[0]
    return cdf

# =============================================================================
# 2. IN-MEMORY CACHE BUILDER
# =============================================================================

def build_in_memory_caches(N, symmetry):
    print(f"\n--- Loading Database N={N}, Sym={symmetry} ---")
    db_uri = f'sqlite:///database/tilings/storage/tilings_{N}_{symmetry}.db'
    engine = create_engine(db_uri)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    all_tilings = session.query(Tiling.id, Tiling.embedding).all()
    session.close()
    
    if not all_tilings:
        raise ValueError("Database is empty or not found.")
        
    raw_eigs = []
    faiss_map = {}
    
    print(f"Extracting {len(all_tilings)} tree eigenvalues...")
    for idx, (t_id, tree_bytes) in enumerate(all_tilings):
        tree = pickle.loads(tree_bytes)
        eigs = extract_eigenvalues(tree, eig_count=EIG_COUNT, resolution=RESOLUTION)
        raw_eigs.append(eigs)
        faiss_map[idx] = t_id
            
    raw_eigs_np = np.array(raw_eigs, dtype=np.float32)
    
    print("Computing Base Matrices (HKT, WKS, CWKS)...")
    matrices = {
        'hkt': compute_hkt_base(raw_eigs_np),
        'wks': compute_wks_base(raw_eigs_np),
        'cwks': compute_cwks_base(raw_eigs_np)
    }
    
    schemes = [
        ('hkt', False), ('hkt', True),
        ('wks', False), ('wks', True),
        ('cwks', False), ('cwks', True)
    ]
    
    caches = {}
    print("Building temporary FAISS indexes...")
    for base_name, use_zscore in schemes:
        mat = matrices[base_name]
        dim = mat.shape[1]
        
        if use_zscore:
            mu = np.mean(mat, axis=0)
            sigma = np.std(mat, axis=0)
            sigma[sigma < 1e-5] = 1e-5 
            z_mat = (mat - mu) / sigma
        else:
            mu = np.zeros(dim, dtype=np.float32)
            sigma = np.ones(dim, dtype=np.float32)
            z_mat = mat
            
        z_mat = z_mat.astype(np.float32)
        
        index = faiss.IndexFlatL2(dim)
        index.add(z_mat)
        
        scheme_id = f"{base_name}_{'zscore' if use_zscore else 'raw'}"
        caches[scheme_id] = {
            'index': index,
            'mu': mu,
            'sigma': sigma,
            'dim': dim,
            'base_fn': globals()[f"compute_{base_name}_base"]
        }
        print(f"  -> Built index for {scheme_id}")
        
    return caches, faiss_map

# =============================================================================
# 3. PERTURBATION & ABLATION RUNNER
# =============================================================================

def perturb_tree(tree, epsilon):
    new_tree = copy.deepcopy(tree)
    if epsilon == 0.0: return new_tree
        
    for u, v, data in new_tree.edges(data=True):
        scale = np.random.uniform(1.0 - epsilon, 1.0 + epsilon)
        new_length = data.get('length', 1.0) * scale
        data['length'] = new_length
        data['weight'] = 1.0 / max(new_length, 1e-8) 
    return new_tree

def run_mega_ablation(N=4, symmetry="diag", n_samples=200):
    # 1. Build RAM Caches
    caches, faiss_map = build_in_memory_caches(N, symmetry)
    
    # 2. Fetch Ground Truth Queries
    db_path = f"database/tilings/storage/tilings_{N}_{symmetry}.db"
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f"SELECT id, embedding FROM tiling ORDER BY RANDOM() LIMIT {n_samples}")
        rows = cursor.fetchall()
        if not rows: raise sqlite3.OperationalError
    except sqlite3.OperationalError:
        cursor.execute(f"SELECT id, embedding FROM tilings ORDER BY RANDOM() LIMIT {n_samples}")
        rows = cursor.fetchall()
    finally:
        conn.close()

    ground_truth_trees = [(row[0], pickle.loads(row[1])) for row in rows]
    
    # 3. Setup Sweep Parameters
    epsilons = np.linspace(0.0, 0.4, 41) # 0.0 to 1.0 inclusive
    k_values = [1, 5, 10, 20]
    
    results = [] # Will hold dicts for CSV export
    
    print("\n--- Starting Perturbation Sweep ---")
    start_time = time.time()
    
    for eps in epsilons:
        print(f"Evaluating ε = {eps:.2f}...")
        
        # Initialize success counters for this epsilon
        # Structure: success_counts[scheme_id][k] = count
        success_counts = {s_id: {k: 0 for k in k_values} for s_id in caches.keys()}
        
        for true_id, original_tree in ground_truth_trees:
            noisy_tree = perturb_tree(original_tree, eps)
            eigs = extract_eigenvalues(noisy_tree, eig_count=EIG_COUNT, resolution=RESOLUTION)
            
            for s_id, cache in caches.items():
                # Compute base signature
                sig = cache['base_fn'](eigs, dim=cache['dim'])
                
                # Apply scheme-specific normalization
                z_sig = ((sig - cache['mu']) / cache['sigma']).astype(np.float32)
                if z_sig.ndim == 1: z_sig = z_sig.reshape(1, -1)
                
                # Search up to max(k)
                max_k = max(k_values)
                distances, indices = cache['index'].search(z_sig, max_k)
                retrieved_ids = [faiss_map.get(idx) for idx in indices[0]]
                
                # Record hits for each K threshold
                for k in k_values:
                    if true_id in retrieved_ids[:k]:
                        success_counts[s_id][k] += 1
                        
        # Calculate percentages and store
        for s_id in caches.keys():
            row_data = {
                'epsilon': round(eps, 2),
                'scheme': s_id,
                'base_metric': s_id.split('_')[0].upper(),
                'z_scored': 'True' if 'zscore' in s_id else 'False'
            }
            for k in k_values:
                recall_rate = (success_counts[s_id][k] / n_samples) * 100.0
                row_data[f'recall_at_{k}'] = round(recall_rate, 2)
            results.append(row_data)
            
    print(f"\nCompleted in {time.time() - start_time:.1f} seconds.")
    
    # 4. Export to CSV
    csv_filename = f"ablation_results_N{N}_{symmetry}.csv"
    keys = ['epsilon', 'scheme', 'base_metric', 'z_scored'] + [f'recall_at_{k}' for k in k_values]
    
    with open(csv_filename, 'w', newline='') as output_file:
        dict_writer = csv.DictWriter(output_file, fieldnames=keys)
        dict_writer.writeheader()
        dict_writer.writerows(results)
        
    print(f"Data saved successfully to {csv_filename}")


def plot_mega_ablation_results(csv_filename="ablation_results_N4_diag.csv"):
    # 1. Load the data
    try:
        df = pd.read_csv(csv_filename)
    except FileNotFoundError:
        print(f"Error: Could not find '{csv_filename}'. Make sure the ablation script has finished running.")
        return

    # 2. Setup plotting parameters
    k_values = [1, 5, 15, 50]
    
    # Distinct colors for the 3 embedding algorithms
    color_map = {
        'HKT': '#ff6b6b',   # Red
        'WKS': '#feca57',   # Yellow
        'CWKS': '#4dabf7'   # Blue
    }
    
    # 3. Create a 2x2 grid for the subplots
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes = axes.flatten()
    
    schemes = df['scheme'].unique()

    # 4. Generate each subplot
    for idx, k in enumerate(k_values):
        ax = axes[idx]
        
        for scheme in schemes:
            # Filter and sort data to ensure lines draw cleanly from left to right
            scheme_data = df[df['scheme'] == scheme].sort_values(by='epsilon')
            
            # Extract metadata for styling
            base_metric = scheme_data['base_metric'].iloc[0]
            # Handle boolean conversion safely from the CSV string
            is_zscored = str(scheme_data['z_scored'].iloc[0]).strip().lower() == 'true'
            
            # Apply visual mappings
            color = color_map.get(base_metric, '#ffffff')
            linestyle = '-' if is_zscored else '--'
            linewidth = 2.5 if is_zscored else 1.5
            alpha = 0.9 if is_zscored else 0.6
            
            label = f"{base_metric} ({'Z-Scored' if is_zscored else 'Raw'})"
            
            # Plot the line
            ax.plot(
                scheme_data['epsilon'], 
                scheme_data[f'recall_at_{k}'], 
                color=color, 
                linestyle=linestyle, 
                linewidth=linewidth,
                alpha=alpha,
                label=label
            )

        # Formatting for individual subplots
        ax.set_title(f"Recall@{k}", fontsize=14, color='white', pad=10)
        ax.set_xlabel("Distortion Amplitude (ε)", fontsize=11, color='#aaaaaa')
        ax.set_ylabel("Recall (%)", fontsize=11, color='#aaaaaa')
        
        ax.set_ylim(-5, 105)
        ax.set_xlim(df['epsilon'].min() - 0.02, df['epsilon'].max() + 0.02)
        
        ax.set_facecolor('#2b2b2b')
        ax.tick_params(colors='#aaaaaa', which='both')
        ax.grid(True, which='major', color='#444444', linestyle='-')

    # 5. Global Figure Formatting
    fig.patch.set_facecolor('#1e1e1e')
    fig.suptitle(
        "Ablation Study: Embedding Robustness to Edge Length Distortion", 
        fontsize=18, color='white', y=0.96
    )
    
    # Extract handles from the first subplot to create a single, unified legend at the bottom
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles, labels, 
        loc='lower center', 
        ncol=3, 
        facecolor='#1e1e1e', 
        edgecolor='#444444', 
        labelcolor='white', 
        fontsize=12, 
        bbox_to_anchor=(0.5, 0.02)
    )
    
    # Adjust layout to prevent overlap and leave room for the bottom legend
    plt.tight_layout(rect=[0, 0.08, 1, 0.93]) 
    plt.show()

def visualize_embedding_space(N=4, symmetry="diag", sample_size=2000):
    print(f"Sampling {sample_size} trees from N={N}, Sym={symmetry}...")
    db_path = f"database/tilings/storage/tilings_{N}_{symmetry}.db"

    # 1. Fetch Random Sample
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f"SELECT id, embedding FROM tiling ORDER BY RANDOM() LIMIT {sample_size}")
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f"SELECT id, embedding FROM tilings ORDER BY RANDOM() LIMIT {sample_size}")
        rows = cursor.fetchall()
    finally:
        conn.close()

    if not rows:
        print("Error: No trees found in database.")
        return

    # 2. Compute 64-Dim Embeddings
    print("Computing CWKS embeddings...")
    embeddings = []
    tree_sizes = [] # We'll use this to color-code the points by complexity

    for idx, (tiling_id, tree_bytes) in enumerate(rows):
        tree = pickle.loads(tree_bytes)
        tree_sizes.append(len(tree.nodes()))
        
        eigs = extract_eigenvalues(tree, eig_count=EIG_COUNT, resolution=RESOLUTION)
        # Natively returns the 0-to-1 mass-normalized CDF
        cwks = compute_wks_signature(eigs, dim=DIMENSION) 
        embeddings.append(cwks)

    X = np.array(embeddings, dtype=np.float32)

    # 3. Dimensionality Reduction
    print("Running PCA (Global Structure)...")
    pca = PCA(n_components=2)
    X_pca = pca.fit_transform(X)
    
    print("Running t-SNE (Local Clusters)...")
    # Perplexity ~30-50 is standard. Lower = more fragmented clusters.
    tsne = TSNE(n_components=2, perplexity=40, random_state=42, init='pca', learning_rate='auto')
    X_tsne = tsne.fit_transform(X)

    # 4. Plotting
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))
    fig.patch.set_facecolor('#1e1e1e')
    
    # Scatter plot settings
    scatter_kwargs = {
        'c': tree_sizes,           # Color by number of nodes
        'cmap': 'viridis',         # Yellow = complex, Purple = simple
        'alpha': 0.6,
        's': 15,                   # Point size
        'edgecolors': 'none'
    }

    # Plot PCA
    sc1 = ax1.scatter(X_pca[:, 0], X_pca[:, 1], **scatter_kwargs)
    ax1.set_title(f"PCA: Global Structure (Var Explained: {sum(pca.explained_variance_ratio_):.2%})", color='white')
    ax1.set_xlabel("Principal Component 1 (Major Shape Shift)", color='#aaaaaa')
    ax1.set_ylabel("Principal Component 2 (Minor Shape Shift)", color='#aaaaaa')

    # Plot t-SNE
    sc2 = ax2.scatter(X_tsne[:, 0], X_tsne[:, 1], **scatter_kwargs)
    ax2.set_title("t-SNE: Local Clustering (Topological Neighborhoods)", color='white')
    ax2.set_xlabel("t-SNE Dimension 1", color='#aaaaaa')
    ax2.set_ylabel("t-SNE Dimension 2", color='#aaaaaa')

    # Formatting
    for ax in [ax1, ax2]:
        ax.set_facecolor('#2b2b2b')
        ax.tick_params(colors='#aaaaaa')
        ax.grid(True, color='#444444', linestyle='--', alpha=0.5)

    # Add Colorbar
    cbar = fig.colorbar(sc1, ax=[ax1, ax2], fraction=0.02, pad=0.04)
    cbar.set_label('Tree Complexity (Node Count)', color='white')
    cbar.ax.yaxis.set_tick_params(color='white')
    plt.setp(plt.getp(cbar.ax.axes, 'yticklabels'), color='white')

    fig.suptitle(f"CWKS Embedding Space Visualization ({sample_size} Trees | N={N}, Sym={symmetry})", 
                 color='white', fontsize=16)
    
    plt.show()

def visualize_interactive_pca(N=4, symmetry="diag", sample_size=2000):
    print(f"Sampling {sample_size} trees from N={N}, Sym={symmetry}...")
    db_path = f"database/tilings/storage/tilings_{N}_{symmetry}.db"

    # 1. Fetch Random Sample
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f"SELECT id, embedding FROM tiling ORDER BY RANDOM() LIMIT {sample_size}")
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f"SELECT id, embedding FROM tilings ORDER BY RANDOM() LIMIT {sample_size}")
        rows = cursor.fetchall()
    finally:
        conn.close()

    if not rows:
        print("Error: No trees found in database.")
        return

    # 2. Compute 64-Dim Embeddings
    print("Computing CWKS embeddings...")
    embeddings = []
    tree_sizes = [] 

    for idx, (tiling_id, tree_bytes) in enumerate(rows):
        tree = pickle.loads(tree_bytes)
        tree_sizes.append(len(tree.nodes()))
        
        eigs = extract_eigenvalues(tree, eig_count=EIG_COUNT, resolution=RESOLUTION)
        cwks = compute_wks_signature(eigs, dim=DIMENSION) 
        embeddings.append(cwks)

    X = np.array(embeddings, dtype=np.float32)

    # 3. PCA Dimensionality Reduction
    print("\nRunning PCA on all dimensions...")
    pca = PCA()  # By leaving n_components empty, it calculates all 64 dimensions
    X_pca = pca.fit_transform(X)

    # Print the "Usefulness" (Explained Variance Ratio)
    print("\n--- PCA Explained Variance Ratio ('Usefulness') ---")
    
    # Print the top 10 explicitly
    for i in range(min(10, DIMENSION)):
        print(f"PC{i+1}: {pca.explained_variance_ratio_[i]:.4%} of variance")
    
    # Summarize the tail
    tail_var = np.sum(pca.explained_variance_ratio_[10:])
    print(f"PC11 - PC{DIMENSION}: {tail_var:.4%} of variance combined")
    
    top4_var = np.sum(pca.explained_variance_ratio_[:4])
    print(f"\n--> The top 4 dimensions capture {top4_var:.2%} of total topological variance.")

    # 4. Interactive 3D Plotly Visualization
    print("\nGenerating Interactive 3D Plot in browser...")
    
    # Package into a Pandas DataFrame for Plotly
    df = pd.DataFrame({
        'PC1 (X)': X_pca[:, 0],
        'PC2 (Y)': X_pca[:, 1],
        'PC3 (Z)': X_pca[:, 2],
        'PC4 (Color)': X_pca[:, 3],
        'Node Count': tree_sizes
    })

    # Create the 3D Scatter Plot
    fig = px.scatter_3d(
        df, 
        x='PC1 (X)', 
        y='PC2 (Y)', 
        z='PC3 (Z)',
        color='PC4 (Color)',
        hover_data=['Node Count'], # Shows complexity when you hover your mouse
        color_continuous_scale='Turbo', # Great colormap for spotting continuous gradients
        title=f"PCA Embedding Space (Top 3 Dims, Colored by 4th) | N={N}, Sym={symmetry}"
    )

    # UI Styling for a darker, CAD-like aesthetic
    fig.update_layout(
        template="plotly_dark",
        margin=dict(l=0, r=0, b=0, t=40),
        scene=dict(
            xaxis=dict(showbackground=False, gridcolor='#444'),
            yaxis=dict(showbackground=False, gridcolor='#444'),
            zaxis=dict(showbackground=False, gridcolor='#444')
        )
    )

    # Opens the interactive graph in your default web browser
    fig.show()


def plot_all_pca_loadings(N=4, symmetry="diag", sample_size=2000):
    print(f"Sampling {sample_size} trees to fit PCA...")
    db_path = f"database/tilings/storage/tilings_{N}_{symmetry}.db"

    # 1. Fetch Random Sample
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f"SELECT embedding FROM tiling ORDER BY RANDOM() LIMIT {sample_size}")
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f"SELECT embedding FROM tilings ORDER BY RANDOM() LIMIT {sample_size}")
        rows = cursor.fetchall()
    finally:
        conn.close()

    # 2. Compute 64-Dim Raw CWKS Embeddings
    print("Computing CWKS embeddings...")
    embeddings = []
    for (tree_bytes,) in rows:
        tree = pickle.loads(tree_bytes)
        eigs = extract_eigenvalues(tree, eig_count=EIG_COUNT, resolution=RESOLUTION)
        # Uses the raw mass-normalized CDF (no Z-score)
        cwks = compute_wks_signature(eigs, dim=DIMENSION) 
        embeddings.append(cwks)

    X = np.array(embeddings, dtype=np.float32)

    # 3. Fit PCA
    print("Running PCA...")
    pca = PCA()
    pca.fit(X)

    # 4. Plot Setup
    fig, ax = plt.subplots(figsize=(12, 7))
    fig.patch.set_facecolor('#1e1e1e')
    ax.set_facecolor('#2b2b2b')
    
    max_variance = pca.explained_variance_ratio_[0]

    # 5. Plot Loadings (Reversed so PC1 draws on top of the noise)
    print("Plotting eigenvectors...")
    for i in reversed(range(DIMENSION)):
        loading_vector = pca.components_[i]
        variance = pca.explained_variance_ratio_[i]
        
        # Scale opacity relative to PC1, with a minimum floor so the tail is barely visible
        alpha_val = max(0.02, variance / max_variance)
        
        # Use a bright cyan for high variance, fading into the dark background for low variance
        ax.plot(E_SWEEP, loading_vector, color='#4dabf7', alpha=alpha_val, 
                linewidth=1.5 if i > 3 else 2.5) # Top 4 get slightly thicker lines

    # Add a zero-baseline for reference
    ax.axhline(0, color='#ffffff', linestyle='--', linewidth=1.0, alpha=0.5)

    # 6. Formatting
    ax.set_title(f"All 64 PCA Loadings (Opacity ∝ Variance) | N={N}, Sym={symmetry}", color='white', fontsize=14)
    ax.set_xlabel("Log-Energy Level (e)", color='#aaaaaa', fontsize=12)
    ax.set_ylabel("Eigenvector Weight", color='#aaaaaa', fontsize=12)
    ax.tick_params(colors='#aaaaaa')
    ax.grid(True, color='#444444', linestyle=':', alpha=0.7)

    plt.tight_layout()
    plt.show()


    # =============================================================================
# 2. PCA-TRUNCATED CACHE BUILDER
# =============================================================================

def build_pca_caches(N, symmetry, truncations=[64, 10, 5, 3, 2, 1]):
    print(f"\n--- Loading Database N={N}, Sym={symmetry} ---")
    db_uri = f'sqlite:///database/tilings/storage/tilings_{N}_{symmetry}.db'
    engine = create_engine(db_uri)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    all_tilings = session.query(Tiling.id, Tiling.embedding).all()
    session.close()
    
    if not all_tilings:
        raise ValueError("Database is empty or not found.")
        
    raw_eigs = []
    faiss_map = {}
    
    print(f"Extracting {len(all_tilings)} tree eigenvalues...")
    for idx, (t_id, tree_bytes) in enumerate(all_tilings):
        tree = pickle.loads(tree_bytes)
        eigs = extract_eigenvalues(tree, eig_count=EIG_COUNT, resolution=RESOLUTION)
        raw_eigs.append(eigs)
        faiss_map[idx] = t_id
            
    raw_eigs_np = np.array(raw_eigs, dtype=np.float32)
    
    print("Computing Raw CWKS Matrix...")
    X_cwks = compute_cwks_base(raw_eigs_np)
    
    print("Fitting PCA Model...")
    pca = PCA() # Fits all dimensions
    X_pca = pca.fit_transform(X_cwks)
    
    caches = {}
    print("Building FAISS indexes for truncations:", truncations)
    for t in truncations:
        # Slice the PCA transformed data to the top 't' dimensions
        X_trunc = X_pca[:, :t].astype(np.float32)
        
        index = faiss.IndexFlatL2(t)
        index.add(X_trunc)
        
        caches[t] = index
        print(f"  -> Built index for top {t} components (Var: {np.sum(pca.explained_variance_ratio_[:t]):.1%})")
        
    return caches, faiss_map, pca

# =============================================================================
# 3. PERTURBATION & ABLATION RUNNER
# =============================================================================

def perturb_tree(tree, epsilon):
    new_tree = copy.deepcopy(tree)
    if epsilon == 0.0: return new_tree
        
    for u, v, data in new_tree.edges(data=True):
        scale = np.random.uniform(1.0 - epsilon, 1.0 + epsilon)
        new_length = data.get('length', 1.0) * scale
        data['length'] = new_length
        data['weight'] = 1.0 / max(new_length, 1e-8) 
    return new_tree

def run_pca_ablation(N=4, symmetry="diag", n_samples=200):
    truncations = [64, 10, 5, 3, 2, 1]
    
    # 1. Build RAM Caches & Fit PCA
    caches, faiss_map, pca_model = build_pca_caches(N, symmetry, truncations)
    
    # 2. Fetch Ground Truth Queries
    db_path = f"database/tilings/storage/tilings_{N}_{symmetry}.db"
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f"SELECT id, embedding FROM tiling ORDER BY RANDOM() LIMIT {n_samples}")
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f"SELECT id, embedding FROM tilings ORDER BY RANDOM() LIMIT {n_samples}")
        rows = cursor.fetchall()
    finally:
        conn.close()

    ground_truth_trees = [(row[0], pickle.loads(row[1])) for row in rows]
    
    # 3. Setup Sweep Parameters
    epsilons = np.linspace(0.0, 0.4, 41) 
    k_recall = 5 # Strict Recall@5 Requirement
    
    results = [] 
    
    print(f"\n--- Starting PCA Perturbation Sweep (Recall@{k_recall}) ---")
    start_time = time.time()
    
    for eps in epsilons:
        if eps > 0 and int(eps * 100) % 10 == 0:
            print(f"Evaluating ε = {eps:.2f}...")
            
        success_counts = {t: 0 for t in truncations}
        
        for true_id, original_tree in ground_truth_trees:
            noisy_tree = perturb_tree(original_tree, eps)
            eigs = extract_eigenvalues(noisy_tree, eig_count=EIG_COUNT, resolution=RESOLUTION)
            
            # 1. Get raw CWKS for the noisy tree
            raw_cwks = compute_cwks_base(eigs, dim=DIMENSION)
            if raw_cwks.ndim == 1: raw_cwks = raw_cwks.reshape(1, -1)
            
            # 2. Transform into the fitted PCA space
            pca_cwks = pca_model.transform(raw_cwks)
            
            # 3. Search across all truncation levels
            for t in truncations:
                q_trunc = pca_cwks[:, :t].astype(np.float32)
                distances, indices = caches[t].search(q_trunc, k_recall)
                retrieved_ids = [faiss_map.get(idx) for idx in indices[0]]
                
                if true_id in retrieved_ids:
                    success_counts[t] += 1
                        
        # Store results for this epsilon
        for t in truncations:
            recall_rate = (success_counts[t] / n_samples) * 100.0
            results.append({
                'epsilon': round(eps, 2),
                'components': t,
                'variance_explained': round(np.sum(pca_model.explained_variance_ratio_[:t]) * 100, 2),
                f'recall_at_{k_recall}': round(recall_rate, 2)
            })
            
    print(f"\nCompleted in {time.time() - start_time:.1f} seconds.")
    
    # 4. Export to CSV
    csv_filename = f"pca_ablation_results_N{N}_{symmetry}.csv"
    keys = ['epsilon', 'components', 'variance_explained', f'recall_at_{k_recall}']
    
    with open(csv_filename, 'w', newline='') as output_file:
        dict_writer = csv.DictWriter(output_file, fieldnames=keys)
        dict_writer.writeheader()
        dict_writer.writerows(results)
        
    print(f"Data saved successfully to {csv_filename}")
    return csv_filename


def plot_pca_ablation_results(csv_filename="pca_ablation_results_N4_diag.csv"):
    try:
        df = pd.read_csv(csv_filename)
    except FileNotFoundError:
        print(f"Error: Could not find '{csv_filename}'.")
        return

    plt.figure(figsize=(10, 7))
    plt.gcf().patch.set_facecolor('#1e1e1e')
    ax = plt.gca()
    ax.set_facecolor('#2b2b2b')
    
    components = sorted(df['components'].unique(), reverse=True)
    
    # Custom color palette (from cool to warm based on component count)
    colors = {
        64: '#ffffff', # White (Baseline)
        10: '#4dabf7', # Blue
        5:  '#1dd1a1', # Green
        3:  '#feca57', # Yellow
        2:  '#ff9f43', # Orange
        1:  '#ff6b6b'  # Red
    }
    
    for t in components:
        data = df[df['components'] == t].sort_values(by='epsilon')
        var = data['variance_explained'].iloc[0]
        
        label = f"All 64 Dims ({var:.1f}%)" if t == 64 else f"Top {t} Dims ({var:.1f}%)"
        
        ax.plot(
            data['epsilon'], 
            data['recall_at_5'], 
            color=colors.get(t, '#aaaaaa'),
            linewidth=2.5 if t == 64 else 2.0,
            linestyle='-' if t == 64 else '--',
            alpha=0.9,
            label=label
        )

    # Formatting
    ax.set_title("PCA Truncation Robustness to Structural Noise (Recall@5)", fontsize=16, color='white', pad=15)
    ax.set_xlabel("Distortion Amplitude (ε)", fontsize=12, color='#aaaaaa')
    ax.set_ylabel("Recall@5 (%)", fontsize=12, color='#aaaaaa')
    
    ax.set_ylim(-5, 105)
    ax.set_xlim(df['epsilon'].min() - 0.01, df['epsilon'].max() + 0.01)
    ax.tick_params(colors='#aaaaaa', which='both')
    ax.grid(True, which='major', color='#444444', linestyle='-')
    
    ax.legend(
        loc='lower left',
        facecolor='#1e1e1e', 
        edgecolor='#444444', 
        labelcolor='white', 
        fontsize=11
    )
    
    plt.tight_layout()
    plt.show()
if __name__ == "__main__":
    # run_mega_ablation(N=4, symmetry="diag", n_samples=1000)
    # plot_mega_ablation_results()
    # visualize_interactive_pca(N=5, symmetry="diag", sample_size=1000)
    # visualize_interactive_pca(N=4, symmetry="book", sample_size=1000)
    # visualize_interactive_pca(N=3, symmetry="none", sample_size=1000)
    # plot_all_pca_loadings(N=4, symmetry="diag", sample_size=2000)

    # 1. Run the Ablation (Takes a few minutes depending on sample size)
    csv_file = run_pca_ablation(N=4, symmetry="diag", n_samples=300)
    
    # 2. Plot the results
    plot_pca_ablation_results(csv_file)