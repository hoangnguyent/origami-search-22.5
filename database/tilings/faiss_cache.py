# =============================================================================
# build_faiss_cache.py (Run whenever updating databases or query parameters)
# =============================================================================
import os
import pickle
import sqlite3
import numpy as np
import faiss
import matplotlib.pyplot as plt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database.tilings.build_tilings import Tiling # Import your actual model
from src.engine.tree import extract_eigenvalues, EIG_COUNT, RESOLUTION

DIMENSION = 64
E_MIN = 3.0
E_MAX = 10.0
E_SWEEP = np.linspace(E_MIN, E_MAX, DIMENSION)
TWO_VARIANCE = 2.0 * ( (2*((E_MAX - E_MIN) / (DIMENSION - 1))) ** 2)

def compute_wks_signature(eigenvalues, dim=DIMENSION):
    """ 
    Converts raw eigenvalues to a Mass-Normalized Cumulative Wave Kernel Signature (CWKS CDF). 
    """
    is_1d = eigenvalues.ndim == 1
    if is_1d:
        eigenvalues = eigenvalues.reshape(1, -1)
        
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
    
    # Convert PDF to CDF
    cumulative = np.cumsum(signatures, axis=1)
    
    # Normalize by Total Spectral Mass (Forces the final bucket to 1.0)
    # This isolates the proportional shape of the tree and ignores raw node count
    row_max = cumulative[:, -1:]
    row_max[row_max == 0] = 1.0
    cdf = cumulative / row_max
    
    if is_1d: return cdf[0]
    return cdf

def build_wks_index_for_db(N, symmetry):
    """
    Reads the SQLite DB, extracts eigenvalues, computes normalized CWKS, 
    applies Z-score stretching, and caches assets.
    """
    print(f"Building FAISS Z-Scored WKS Index for N={N}, Sym={symmetry}...")
    db_uri = f'sqlite:///database/tilings/storage/tilings_{N}_{symmetry}.db'
    
    engine = create_engine(db_uri)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    all_tilings = session.query(Tiling.id, Tiling.embedding).all()
    session.close()
    
    if not all_tilings:
        print("Database is empty. Skipping.")
        return
        
    # 1. Load Trees and Extract Eigenvalues
    raw_eigs = []
    faiss_map = {}
    
    print(f"Extracting eigenvalues from {len(all_tilings)} cached trees...")
    for idx, (t_id, tree_bytes) in enumerate(all_tilings):
        tree = pickle.loads(tree_bytes)
        eigenvalues = extract_eigenvalues(tree, eig_count=EIG_COUNT, resolution=RESOLUTION)
        
        raw_eigs.append(eigenvalues)
        faiss_map[idx] = t_id
        
        if (idx + 1) % 50000 == 0:
            print(f"  Processed {idx + 1} / {len(all_tilings)} trees...")
            
    raw_eigs_np = np.array(raw_eigs, dtype=np.float32)
    
    # 2. Compute Base CWKS CDF Signatures
    print("Computing Normalized CWKS signatures...")
    cdf_matrix = compute_wks_signature(raw_eigs_np, dim=DIMENSION)
    
    # 3. Statistical Normalization (Z-Score)
    # This stretches the S-curve so dimensions with low variance (where all trees look the same)
    # are amplified to fill the L2 hypercube.
    print("Applying Z-Score vector expansion...")
    mu = np.mean(cdf_matrix, axis=0)
    sigma = np.std(cdf_matrix, axis=0)
    
    # We use 1e-5 instead of 1e-8 to prevent blowing up pure floating point noise
    # at the extreme tails of the CDF where variance is near 0.
    sigma[sigma < 1e-5] = 1e-5 
    
    z_matrix = (cdf_matrix - mu) / sigma
    z_matrix = z_matrix.astype(np.float32)
    
    # 4. Build L2 Index
    index = faiss.IndexFlatL2(DIMENSION)
    index.add(z_matrix)
    
    # 5. Save all assets to disk
    os.makedirs("database/tilings/faiss_cache", exist_ok=True)
    prefix = f"database/tilings/faiss_cache/db_{N}_{symmetry}"
    
    cache_data = {
        'faiss_map': faiss_map,
        'mu': mu,
        'sigma': sigma
    }
    with open(f"{prefix}_data.pkl", 'wb') as f:
        pickle.dump(cache_data, f)
        
    faiss.write_index(index, f"{prefix}_l2.index")
    
    print(f"Success. Cached {len(raw_eigs)} Z-scored vectors.")

def plot_random_tree_embeddings(N=5, symmetry="diag", sample_size=100):
    """
    Connects to the database, extracts random trees, computes their Z-Scored CWKS,
    and plots them to visualize the stretched embedding space.
    """
    db_path = f"database/tilings/storage/tilings_{N}_{symmetry}.db"
    cache_path = f"database/tilings/faiss_cache/db_{N}_{symmetry}_data.pkl"
    
    # 1. Load the Database mu and sigma to apply the correct stretch
    try:
        with open(cache_path, 'rb') as f:
            cache_data = pickle.load(f)
            mu = cache_data['mu']
            sigma = cache_data['sigma']
    except FileNotFoundError:
        print(f"Error: Missing cache file for {N}_{symmetry}. Run build_wks_index_for_db first.")
        return

    # 2. Sample random pickled trees
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f"SELECT embedding FROM tiling ORDER BY RANDOM() LIMIT {sample_size}")
        rows = cursor.fetchall()
        conn.close()
    except sqlite3.OperationalError:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(f"SELECT embedding FROM tilings ORDER BY RANDOM() LIMIT {sample_size}")
        rows = cursor.fetchall()
        conn.close()

    if not rows:
        print("Error: No trees found in database.")
        return

    # 3. Setup Plot
    plt.figure(figsize=(12, 7))
    
    # 4. Process and Plot each tree
    for row in rows:
        tree_bytes = row[0]
        tree = pickle.loads(tree_bytes)
        eigs = extract_eigenvalues(tree, eig_count=EIG_COUNT, resolution=RESOLUTION)
        
        # compute_wks_signature now natively returns the mass-normalized CDF
        cdf = compute_wks_signature(eigs, dim=DIMENSION, e_min=E_MIN, e_max=E_MAX)
        
        # Apply the Database-specific Z-score stretch
        z_score_vector = (cdf - mu) / sigma
        
        plt.plot(E_SWEEP, z_score_vector, alpha=0.01, linewidth=0.5, color = '#1f77b4')

    # 5. Formatting
    plt.title(f"Z-Scored CWKS Embeddings of {sample_size} Trees (N={N}, {symmetry})", fontsize=14)
    plt.xlabel("Log-Energy Level (e)", fontsize=12)
    plt.ylabel("Z-Score Deviations from Mean", fontsize=12)
    
    plt.minorticks_on()
    

    plt.title(f"Z-Scored CWKS Embeddings of {sample_size} Trees (N={N}, {symmetry})", color='white')
    plt.xlabel("Log-Energy Level (e)")
    plt.ylabel("Z-Score Deviations from Mean")
    plt.tick_params(colors='#aaaaaa', which='both')
    
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    configs = [
        (3, 'diag'), 
        (3, 'none'),
        (3, 'book'),
        (4, 'diag'), 
        (4, 'book'),
        (4, 'none'), 
        (5, 'diag'),
        (6, 'book')
    ]
    # To test, uncomment build_wks_index_for_db to rebuild caches first
    # for N, sym in configs:
    #     build_wks_index_for_db(N, sym)
    
    plot_random_tree_embeddings(N=5, symmetry='diag', sample_size=10000)