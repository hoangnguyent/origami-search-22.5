# =============================================================================
# build_faiss_cache.py (Run whenever updating databases or query parameters)
# =============================================================================
import os
import pickle
import numpy as np
import faiss
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database.tilings.build_tilings import Tiling # Import your actual model
from src.engine.tree import extract_eigenvalues, EIG_COUNT, RESOLUTION

DIMENSION = 64
# Can increase the resolution or range of time scale if it reaches the point there are too many good options and we want finer differentiation
def get_t_scales(dim=DIMENSION):
    """
    Creates a 64-dim array focused heavily on the 1e-3 to 1.0 action zone.
    """
    # 4 dimensions for the extreme microscopic noise
    t_micro = np.geomspace(1e-5, 1e-3, num=8, endpoint=False)
    # 24 dimensions focused purely on the main structural differentiation
    t_action = np.geomspace(1e-3, 1.0, num=dim - 16, endpoint=False)
    # 4 dimensions for the extreme macroscopic bounds
    t_macro = np.geomspace(1.0, 10.0, num=8)
    return np.concatenate([t_micro, t_action, t_macro])

def compute_hkt_signature(eigenvalues, dim=32):
    """ Converts raw padded eigenvalues to a normalized HKT signature. """
    t_scales = get_t_scales(dim)
    # valid_mask = eigenvalues > 1e-6 
    
    is_1d = eigenvalues.ndim == 1
    if is_1d:
        eigenvalues = eigenvalues.reshape(1, -1)
    #     valid_mask = valid_mask.reshape(1, -1)
        
    N_samples = eigenvalues.shape[0]
    signatures = np.zeros((N_samples, dim), dtype=np.float32)
    
    for j, t in enumerate(t_scales):
        decayed = np.exp(-t * eigenvalues)
        # decayed[~valid_mask] = 0.0 # Prevent padding from artificially bloating trace
        signatures[:, j] = np.sum(decayed, axis=1)
        
    # Normalize curves so they all start near 1.0 (Scale invariance)
    norms = signatures[:, 0:1]
    norms[norms == 0] = 1.0
    signatures = signatures / norms
    
    if is_1d: return signatures[0]
    return signatures
def build_zscore_index_for_db(N, symmetry):
    """
    Reads the SQLite DB, extracts eigenvalues from cached trees, computes HKT, normalizes via Z-Score, and caches assets.
    """
    print(f"Building FAISS Z-Score Index for N={N}, Sym={symmetry}...")
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
        # Unpickle the raw NetworkX tree
        tree = pickle.loads(tree_bytes)
        
        # Extract the subdivision-invariant eigenvalues
        eigenvalues = extract_eigenvalues(tree, eig_count=EIG_COUNT, resolution=RESOLUTION)
        
        raw_eigs.append(eigenvalues)
        faiss_map[idx] = t_id
        
        # Lightweight progress tracker
        if (idx + 1) % 50000 == 0:
            print(f"  Processed {idx + 1} / {len(all_tilings)} trees...")
            
    raw_eigs_np = np.array(raw_eigs, dtype=np.float32)
    
    # 2. Compute HKT Signatures
    print("Computing HKT signatures and Z-Scores...")
    hkt_matrix = compute_hkt_signature(raw_eigs_np, dim=DIMENSION)
    
    # 3. Z-Score Normalization (Mathematically safe amplification)
    mu = np.mean(hkt_matrix, axis=0)
    sigma = np.std(hkt_matrix, axis=0)
    
    # Prevent division by zero if a dimension has zero variance
    sigma[sigma < 1e-8] = 1e-8 
    
    z_matrix = (hkt_matrix - mu) / sigma
    z_matrix = z_matrix.astype(np.float32) # Ensure FAISS typing
    
    # 4. Build L2 Index
    index = faiss.IndexFlatL2(DIMENSION)
    index.add(z_matrix)
    
    # 5. Save all assets to disk
    os.makedirs("database/tilings/faiss_cache", exist_ok=True)
    prefix = f"database/tilings/faiss_cache/db_{N}_{symmetry}"
    
    # Bundle the map, mean, and std into one dictionary
    cache_data = {
        'faiss_map': faiss_map,
        'mu': mu,
        'sigma': sigma
    }
    with open(f"{prefix}_data.pkl", 'wb') as f:
        pickle.dump(cache_data, f)
        
    faiss.write_index(index, f"{prefix}_l2.index")
    
    print(f"Success. Cached {len(raw_eigs)} vectors.")


if __name__ == "__main__":
    # Build caches for all DB configurations
    configs = [
        (3, 'diag'), 
        (3, 'none'),
        (4, 'diag'), 
        
        (4, 'none'), (4, 'book'),
        (5, 'diag'),
    ]
    for N, sym in configs:
        build_zscore_index_for_db(N, sym)

"""
Most recent cache:
3 diag: 1417
3 none: 174,790
4 diag: 206,660
4 none: 173,038 (incomplete)
4 book: 173
5 diag: 68,614
"""