import sqlite3
import pickle
import copy
import numpy as np
import faiss
import matplotlib.pyplot as plt

# --- Import your engine functions ---
from src.engine.tree import extract_eigenvalues, EIG_COUNT, RESOLUTION

from database.tilings.faiss_cache_hkt import compute_hkt_signature
from database.tilings.faiss_cache import compute_wks_signature, DIMENSION, E_MIN, E_MAX

def load_faiss_cache(prefix):
    """Loads the FAISS index and the associated normalization data."""
    with open(f"{prefix}_data.pkl", 'rb') as f:
        cache_data = pickle.load(f)
        
    index = faiss.read_index(f"{prefix}_l2.index")
    return index, cache_data['faiss_map'], cache_data['mu'], cache_data['sigma']

def perturb_tree(tree, epsilon):
    """
    Creates a deep copy of the tree and scales every edge length 
    by a uniform random factor between (1 - epsilon, 1 + epsilon).
    """
    new_tree = copy.deepcopy(tree)
    if epsilon == 0.0:
        return new_tree
        
    for u, v, data in new_tree.edges(data=True):
        scale = np.random.uniform(1.0 - epsilon, 1.0 + epsilon)
        # Apply distortion
        new_length = data.get('length', 1.0) * scale
        data['length'] = new_length
        # Update the weight (Laplacian adjacency uses 1/length)
        data['weight'] = 1.0 / max(new_length, 1e-8) 
        
    return new_tree

def run_ablation_study(N=5, symmetry="diag", n_samples=200, k=5, max_epsilon=0.5, eps_steps=10):
    print(f"Starting Ablation Study (N={N}, Sym={symmetry}) with {n_samples} samples...")
    
    # 1. Load the FAISS indexes and normalization data
    print("Loading FAISS indexes...")
    hkt_prefix = f"database/tilings/faiss_cache_hkt/db_{N}_{symmetry}"
    cwks_prefix = f"database/tilings/faiss_cache_cwks/db_{N}_{symmetry}"
    
    try:
        hkt_idx, hkt_map, hkt_mu, hkt_sigma = load_faiss_cache(hkt_prefix)
        cwks_idx, cwks_map, cwks_mu, cwks_sigma = load_faiss_cache(cwks_prefix)
    except Exception as e:
        print(f"Error loading FAISS caches: {e}")
        print("Make sure you have built both indexes in their respective folders.")
        return

    # 2. Sample random ground-truth trees from the database
    db_path = f"database/tilings/storage/tilings_{N}_{symmetry}.db"
    print(f"Sampling {n_samples} ground-truth trees from {db_path}...")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        # Fetching tiling_id and the tree embedding
        cursor.execute(f"SELECT id, embedding FROM tiling ORDER BY RANDOM() LIMIT {n_samples}")
        rows = cursor.fetchall()
        conn.close()
    except sqlite3.OperationalError:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f"SELECT id, embedding FROM tilings ORDER BY RANDOM() LIMIT {n_samples}")
        rows = cursor.fetchall()
        conn.close()

    if not rows:
        print("No trees found in the database.")
        return

    ground_truth_trees = [(row[0], pickle.loads(row[1])) for row in rows]

    # 3. Setup the Sweep
    epsilons = np.linspace(0.0, max_epsilon, eps_steps) # Sweep from 0 to max_epsilon in steps of (max_epsilon / eps_steps)
    recall_hkt = []
    recall_cwks = []

    # 4. Evaluation Loop
    for eps in epsilons:
        print(f"Evaluating Noise Amplitude: epsilon = {eps:.2f}")
        hkt_success = 0
        cwks_success = 0
        
        for true_id, original_tree in ground_truth_trees:
            # Apply structural noise
            noisy_tree = perturb_tree(original_tree, eps)
            
            # Extract new Laplacian eigenvalues
            eigs = extract_eigenvalues(noisy_tree, eig_count=EIG_COUNT, resolution=RESOLUTION)
            
            # --- HKT SEARCH ---
            sig_hkt = compute_hkt_signature(eigs, dim=DIMENSION)
            z_hkt = ((sig_hkt - hkt_mu) / hkt_sigma).astype(np.float32)
            if z_hkt.ndim == 1: z_hkt = z_hkt.reshape(1, -1)
            
            distances_hkt, indices_hkt = hkt_idx.search(z_hkt, k)
            top_k_hkt_ids = [hkt_map.get(idx) for idx in indices_hkt[0]]
            
            if true_id in top_k_hkt_ids:
                hkt_success += 1
                
            # --- CWKS SEARCH ---
            sig_cwks = compute_wks_signature(eigs, dim=DIMENSION)
            z_cwks = ((sig_cwks - cwks_mu) / cwks_sigma).astype(np.float32)
            if z_cwks.ndim == 1: z_cwks = z_cwks.reshape(1, -1)
            
            distances_cwks, indices_cwks = cwks_idx.search(z_cwks, k)
            top_k_cwks_ids = [cwks_map.get(idx) for idx in indices_cwks[0]]
            
            if true_id in top_k_cwks_ids:
                cwks_success += 1
                
        # Calculate percentage success
        recall_hkt.append((hkt_success / n_samples) * 100.0)
        recall_cwks.append((cwks_success / n_samples) * 100.0)

    # 5. Plot the Results
    plt.figure(figsize=(10, 6))
    
    plt.plot(epsilons, recall_cwks, marker='o', color='#4dabf7', linewidth=2.5, label='CWKS (Wave Kernel)')
    plt.plot(epsilons, recall_hkt, marker='s', color='#ff6b6b', linewidth=2.5, label='HKT (Heat Kernel)')
    
    plt.title(f"Embedding Robustness against Structural Noise (Recall@{k})", fontsize=14, color='white')
    plt.xlabel("Distortion Amplitude ε (Edge lengths scaled by 1±ε)", fontsize=12, color='#aaaaaa')
    plt.ylabel(f"Recall@{k} (%) - True tree found in Top {k}", fontsize=12, color='#aaaaaa')
    
    # UI Styling
    plt.gca().set_facecolor('#2b2b2b')
    plt.gcf().patch.set_facecolor('#1e1e1e')
    plt.tick_params(colors='#aaaaaa', which='both')
    # plt.ylim(-5, 105)
    # plt.xlim(-0.02, 0.52)
    
    plt.grid(True, which='major', color='#444444', linestyle='-')
    plt.legend(facecolor='#1e1e1e', edgecolor='#444444', labelcolor='white', fontsize=11)
    
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    run_ablation_study(N=5, symmetry="diag", n_samples=500, k=10, max_epsilon=0.8, eps_steps=20)