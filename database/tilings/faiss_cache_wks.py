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
E_MIN = 3.0
E_MAX = 10.0
E_SWEEP = np.linspace(E_MIN, E_MAX, DIMENSION)
TWO_VARIANCE = 2.0 * ( (2*((E_MAX - E_MIN) / (DIMENSION - 1))) ** 2)

def compute_wks_signature(eigenvalues, dim=DIMENSION, e_min=E_MIN, e_max=E_MAX):
    """ Converts raw eigenvalues to an absolute Wave Kernel Signature (WKS). """
    
    is_1d = eigenvalues.ndim == 1
    if is_1d:
        eigenvalues = eigenvalues.reshape(1, -1)
        
    N_samples = eigenvalues.shape[0]
    signatures = np.zeros((N_samples, dim), dtype=np.float32)
    
    # Mask out zero/padded eigenvalues. Shouldn't be necessary because extract_eigenvalue already skips the 0 eigenvalue, and subidivion ensures the dimension of the laplacian is greater than the number of eigenvalues we collect (no padding), but just in case
    valid_mask = eigenvalues > 1e-6
    safe_eigs = np.where(valid_mask, eigenvalues, 1.0) 
    log_eigs = np.log(safe_eigs)
    
    for j, e in enumerate(E_SWEEP):
        # Evaluate the Gaussian band-pass filter at energy level 'e'
        squared_diff = (e - log_eigs) ** 2
        band_pass = np.exp(-squared_diff / TWO_VARIANCE)
        
        # Zero out the invalid (padded/zero) eigenvalues
        band_pass = np.where(valid_mask, band_pass, 0.0)
        
        # Trace is the sum across all active eigenvalues resonating at this energy bucket
        signatures[:, j] = np.sum(band_pass, axis=1)
    
    # breakpoint()
    cumulative = np.cumsum(signatures, axis=1)
    if is_1d: return cumulative[0]
    return cumulative
    # if is_1d: return signatures[0]
    # return signatures

def build_wks_index_for_db(N, symmetry):
    """
    Reads the SQLite DB, extracts eigenvalues from cached trees, computes WKS, and caches assets.
    """
    print(f"Building FAISS WKS Index for N={N}, Sym={symmetry}...")
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
    
    # 2. Compute Absolute WKS Signatures
    print("Computing Absolute WKS signatures...")
    wks_matrix = compute_wks_signature(raw_eigs_np, dim=DIMENSION)
    wks_matrix = wks_matrix.astype(np.float32)
    
    # 3. Build L2 Index (Using direct Euclidean distance on absolute embeddings)
    index = faiss.IndexFlatL2(DIMENSION)
    index.add(wks_matrix)
    
    # 4. Save all assets to disk
    os.makedirs("database/tilings/faiss_cache", exist_ok=True)
    prefix = f"database/tilings/faiss_cache/db_{N}_{symmetry}"
    
    # Supply dummy mu/sigma arrays so `server.py` unpacks them without crashing.
    cache_data = {
        'faiss_map': faiss_map,
        'mu': np.zeros(DIMENSION, dtype=np.float32),
        'sigma': np.ones(DIMENSION, dtype=np.float32)
    }
    with open(f"{prefix}_data.pkl", 'wb') as f:
        pickle.dump(cache_data, f)
        
    faiss.write_index(index, f"{prefix}_l2.index")
    
    print(f"Success. Cached {len(raw_eigs)} absolute WKS vectors.")


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
    for N, sym in configs:
        build_wks_index_for_db(N, sym)