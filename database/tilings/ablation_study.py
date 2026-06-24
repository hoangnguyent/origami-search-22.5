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
from database.tilings.faiss_cache_hkt import DIMENSION as DIM_HKT, get_t_scales
from database.tilings.faiss_cache import DIMENSION as DIM_WKS, E_SWEEP, TWO_VARIANCE
import pandas as pd
import matplotlib.pyplot as plt
# =============================================================================
# 1. CORE EMBEDDING FUNCTIONS (Cleanly separated)
# =============================================================================

def compute_hkt_base(eigenvalues, dim=DIM_HKT):
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

def compute_wks_base(eigenvalues, dim=DIM_WKS):
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

def compute_cwks_base(eigenvalues, dim=DIM_WKS):
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


if __name__ == "__main__":
    # run_mega_ablation(N=4, symmetry="diag", n_samples=1000)
    plot_mega_ablation_results()