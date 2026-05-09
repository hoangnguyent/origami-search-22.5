"""
Query Pipeline: Tree -> FAISS -> Megaplot

Searches the exact tiling database for the closest matching trees using FAISS. 
Maintains a raw NumPy cache of the database to allow for instant, query-time 
weighting of the eigenvalues (e.g. exponential decay) without permanent DB mutation.
"""

import os
import math
import pickle
import numpy as np
import networkx as nx
import faiss
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon

from sqlalchemy import create_engine, Column, Integer, LargeBinary, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# Pipeline Imports
from src.engine.tiling2cp import load_frozen_blob, build_crease_pattern, add_hinges
from src.engine.cp225 import canonicalize, unfreeze
from src.engine.fold225 import cp_to_fold
from src.engine.tree import extract_eigenvalues, get_proportional_tree_pos
from database.tilings.build_tilings import decompress_edges, Topology, Tiling, DestBase

DIMENSION = 32

# =============================================================================
# 1. EIGENVALUE TUNING FUNCTION
# =============================================================================

def apply_weights(eigenvalues, method, t):
    """
    Applies custom weighting to raw eigenvalues to emphasize global layout (low freq)
    over local tips/forks (high freq). 
    
    This function handles both 1D arrays (a single query tree) and 
    2D arrays (the entire database matrix) dynamically.
    """
    if method == "value_decay":
        # Weight drops exponentially as the eigenvalue itself gets larger
        # multiply by eigenvalues to low frequencies to 0
        weights = np.exp(-t * eigenvalues)
        
    elif method == "index_decay":
        # Weight drops strictly based on the order (1st, 2nd, 3rd eigenvalue...)
        if eigenvalues.ndim == 1:
            indices = np.arange(1, len(eigenvalues) + 1)
        else:
            indices = np.arange(1, eigenvalues.shape[1] + 1)
        weights = np.exp(-t * indices)
        
    elif method == "inverse":
        # Aggressive physical penalty: 1 / lambda
        # (Add a tiny epsilon to avoid div-by-zero if a 0 sneaks in)
        weights = 1.0 / (eigenvalues + 1e-7)
        
    else:
        # Unweighted (Raw Euclidean)
        weights = 1.0
        
    return eigenvalues * weights
def compute_hkt_signature(eigenvalues, dim=32, t_min=0.05, t_max=5.0):
    """
    Converts raw, zero-padded eigenvalues into a Heat Kernel Trace signature.
    Handles both 1D arrays (a single query tree) and 2D arrays (database matrix).
    """
    # 1. Define logarithmically spaced time scales
    t_scales = np.geomspace(t_min, t_max, num=dim)
    
    # 2. Identify actual structure vs padding
    # Any exact 0.0 is artificial padding (since we drop the true 0 eigenvalue)
    valid_mask = eigenvalues > 1e-6 
    
    is_1d = eigenvalues.ndim == 1
    if is_1d:
        eigenvalues = eigenvalues.reshape(1, -1)
        valid_mask = valid_mask.reshape(1, -1)
        
    N_samples = eigenvalues.shape[0]
    signatures = np.zeros((N_samples, dim), dtype=np.float32)
    
    # 3. Compute H(t) = sum( exp(-t * lambda) )
    for j, t in enumerate(t_scales):
        decayed = np.exp(-t * eigenvalues)
        
        # PREVENT THE ALIGNMENT SHIFT PROBLEM:
        # Zero-padding evaluates to exp(0) = 1.0. We must force it to 0.0 
        # so missing frequencies don't artificially bloat the trace.
        decayed[~valid_mask] = 0.0
        
        # Sum across the eigenvalues for this specific time step
        signatures[:, j] = np.sum(decayed, axis=1)
        
    if is_1d:
        return signatures[0]
    return signatures

            
# =============================================================================
# 2. RAW CACHE & EPHEMERAL FAISS MANAGEMENT
# =============================================================================

def sync_raw_cache(session, N, symmetry):
    """
    Syncs the database to a raw .npy file. 
    Loading .npy is >100x faster than querying SQLite, allowing us to 
    dynamically weight and rebuild the FAISS index in RAM instantly at query time.
    """
    npy_path = f"database/tilings/storage/raw_embeddings_{N}_{symmetry}.npy"
    map_path = f"database/tilings/storage/faiss_map_{N}_{symmetry}.pkl"
    
    # 1. Load existing cache
    if os.path.exists(npy_path) and os.path.exists(map_path):
        raw_matrix = np.load(npy_path)
        with open(map_path, 'rb') as f:
            faiss_map = pickle.load(f)
    else:
        raw_matrix = np.empty((0, DIMENSION), dtype=np.float32)
        faiss_map = []

    last_indexed_id = faiss_map[-1] if faiss_map else -1
    
    # 2. Fetch missing embeddings from DB
    new_records = session.query(Tiling.id, Tiling.embedding).filter(Tiling.id > last_indexed_id).all()
    
    # 3. Update and Save if new data exists
    if new_records:
        print(f"Syncing {len(new_records)} new embeddings from DB to local raw cache...")
        new_embeddings = np.vstack([np.frombuffer(r.embedding, dtype=np.float32) for r in new_records])
        raw_matrix = np.vstack([raw_matrix, new_embeddings])
        faiss_map.extend([r.id for r in new_records])
        
        np.save(npy_path, raw_matrix)
        with open(map_path, 'wb') as f:
            pickle.dump(faiss_map, f, protocol=pickle.HIGHEST_PROTOCOL)
            
    return raw_matrix, faiss_map

# =============================================================================
# 3. QUERY PIPELINE
# =============================================================================

def load_db_cache(N, symmetry):
    """
    Loads the SQLite session, NumPy cache, and FAISS map for a specific database.
    If the cache doesn't exist, it builds it.
    """
    db_uri = f'sqlite:///database/tilings/storage/tilings_{N}_{symmetry}.db'
    cache_file = f'database/tilings/storage/cache_{N}_{symmetry}.npy'
    map_file = f'database/tilings/storage/map_{N}_{symmetry}.pkl'
    
    engine = create_engine(db_uri)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # Check if cache needs building
    if not os.path.exists(cache_file) or not os.path.exists(map_file):
        print(f"Building cache for N={N}, Sym={symmetry}...")
        all_tilings = session.query(Tiling.id, Tiling.embedding).all()
        
        embeddings = []
        faiss_map = {}
        for idx, (t_id, emb_bytes) in enumerate(all_tilings):
            emb_array = np.frombuffer(emb_bytes, dtype=np.float32)
            embeddings.append(emb_array)
            faiss_map[idx] = t_id
            
        embeddings_np = np.array(embeddings, dtype=np.float32)
        np.save(cache_file, embeddings_np)
        with open(map_file, 'wb') as f:
            pickle.dump(faiss_map, f)
            
    # Load Cache
    embeddings_np = np.load(cache_file)
    with open(map_file, 'rb') as f:
        faiss_map = pickle.load(f)
        
    return session, embeddings_np, faiss_map


# =============================================================================
# FEDERATED QUERY FUNCTION
# =============================================================================

def query_tilings(query_tree, db_configs=[(4, 'none'), (4, 'diag'), (3, 'none')], n=5, t_min=0.05, t_max=5.0):
    """
    Executes a federated FAISS search across multiple databases using the Heat Kernel Trace.
    Only reconstructs the geometry for the global top 'n' results to save compute time.
    """
    # 1. Prepare Query Embedding
    raw_query_eig = extract_eigenvalues(query_tree, dim=DIMENSION)
    query_embedding = compute_hkt_signature(raw_query_eig, dim=DIMENSION, t_min=t_min, t_max=t_max)
    query_vector = np.array([query_embedding], dtype=np.float32) 

    all_raw_hits = []
    sessions = {}

    print(f"Federated Search across {len(db_configs)} databases...")
    
    # 2. Search Each Database
    for N, sym in db_configs:
        try:
            session, db_embeddings, faiss_map = load_db_cache(N, sym)
            sessions[(N, sym)] = session
            
            # Apply HKT to this specific DB's raw eigenvalues dynamically
            weighted_db = compute_hkt_signature(db_embeddings, dim=DIMENSION, t_min=t_min, t_max=t_max)
            
            # Build Ephemeral FAISS Index
            index = faiss.IndexFlatL2(DIMENSION)
            index.add(weighted_db)
            
            # Request 'n' results from EACH database
            D, I = index.search(query_vector, n)
            
            for dist, idx in zip(D[0], I[0]):
                if idx != -1: 
                    all_raw_hits.append({
                        'distance': dist,
                        'tiling_id': faiss_map[idx],
                        'N': N,
                        'symmetry': sym
                    })
        except Exception as e:
            print(f"Warning: Failed to search DB {N}_{sym}. Error: {e}")

    # 3. Sort Globally and Slice
    all_raw_hits.sort(key=lambda x: x['distance'])
    global_top_hits = all_raw_hits[:n]
    
    print(f"Found Top {n} hits. Reconstructing geometry...")

    # 4. Late Reconstruction (Only process the absolute winners)
    results = []
    for rank, hit in enumerate(global_top_hits):
        N, sym = hit['N'], hit['symmetry']
        session = sessions[(N, sym)]
        tiling = session.get(Tiling, hit['tiling_id'])
        topo = session.get(Topology, tiling.topology_id)
        
        # A. Reconstruct Raw Topology
        G_raw = nx.Graph()
        G_raw.add_edges_from(decompress_edges(topo.binary_state, N))
        nx.set_node_attributes(G_raw, {node: node for node in G_raw.nodes()}, 'pos')

        # Deserialize Blob
        blob = pickle.loads(tiling.tiling_blob)
        loaded_G, loaded_pos, loaded_faces = load_frozen_blob(blob)
        
        # Build CP (Canonicalized for clean visual output)
        cp = build_crease_pattern(loaded_G, loaded_pos, loaded_faces, N=N)
        cp = add_hinges(cp)
        cp_frozen = canonicalize(cp)
        cp = unfreeze(cp_frozen)
        
        # Fold and Extract Tree
        fold = cp_to_fold(cp)
        res_tree = fold.get_tree_and_packing()[0]
        
        results.append({
            'rank': rank + 1,
            'distance': hit['distance'],
            'N': N,
            'symmetry': sym,
            'topology_id': topo.id,
            'tiling_id': tiling.id,
            'G_solved': loaded_G,
            'G_raw': G_raw,
            'pos_solved': loaded_pos,
            'cp': cp,
            'fold': fold,
            'tree': res_tree
        })
        
    # Cleanup Sessions
    for session in sessions.values():
        session.close()
        
    return results
# =============================================================================
# 4. VISUALIZATION & MEGAPLOT
# =============================================================================
COLORS = {
    'rm': 'red', 'rv': 'blue', 'av': 'blue', 'hm': 'red', 
    'hv': 'blue', 'h': 'grey', 'v': 'blue', 'm': 'red', 'b': 'black'
}

def draw_cp_ax(ax, cp):
    """Draws the Crease Pattern on a Matplotlib axis"""
    for t, x1, y1, x2, y2 in cp.render():
        ax.plot([x1, x2], [y1, y2], color=COLORS.get(t, 'grey'), 
                lw=(1 if t in {'h','hv','hm'} else 2), zorder=2, alpha=0.7)
    ax.set_aspect('equal')
    ax.axis('off')

def draw_fold_ax(ax, fold, alpha_base=0.05):
    """Draws the transparent, centered Folded State on a Matplotlib axis"""
    rendered_faces, multiplicities = fold.render()
    xs, ys = [], []
    
    for j, face in enumerate(rendered_faces):
        polygon = Polygon(face, closed=True, 
                          alpha=1 - ((1 - alpha_base) ** multiplicities[j]), 
                          facecolor='blue', edgecolor='black', linewidth=0.5)
        ax.add_patch(polygon)
        for p in face:
            xs.append(p[0])
            ys.append(p[1])
            
    if xs and ys:
        # Center the fold based on its bounding box
        cx = (max(xs) + min(xs)) / 2
        cy = (max(ys) + min(ys)) / 2
        span = max(max(xs) - min(xs), max(ys) - min(ys)) / 2
        
        ax.set_xlim(cx - span - 0.1, cx + span + 0.1)
        ax.set_ylim(cy - span - 0.1, cy + span + 0.1)
        
    ax.set_aspect('equal')
    ax.axis('off')

def plot_query_megaplot(query_tree, results):
    """
    Renders two figures:
    Figure 1: The Input Query Tree.
    Figure 2: A megaplot mapping (Topology -> Tiling -> CP -> Fold -> Output Tree) for each result.
    """
    # FIGURE 1: Input Tree
    # fig_input, ax_input = plt.subplots(figsize=(4, 4))
    # pos = get_proportional_tree_pos(query_tree)
    # nx.draw(query_tree, pos, with_labels=False, node_color='blue', edge_color='gray', ax=ax_input, node_size=20)
    # ax_input.set_title("Input Query Tree", fontsize=14, fontweight='bold')
    # ax_input.set_aspect('equal')
    
    # FIGURE 2: Megaplot
    n = len(results)
    if n == 0:
        plt.show()
        return

    fig, axes = plt.subplots(n, 5, figsize=(20, 4 * n))
    if n == 1: axes = [axes] # Ensure 2D indexing works for n=1
    
    col_titles = ["1. Topology", "2. Exact Tiling", "3. Crease Pattern", "4. Folded State", "5. Resulting Tree"]
    
    for i, res in enumerate(results):
        ax_topo, ax_tile, ax_cp, ax_fold, ax_tree = axes[i]
        
        # Set Titles on Top Row
        if i == 0:
            for ax, title in zip(axes[i], col_titles):
                ax.set_title(title, fontsize=14, fontweight='bold')
                
        # 1. Topology
        ax_topo.text(0.05, 0.95, f"Weighted L2 Dist: {res['distance']:.5f}\nTopology ID: {res['topology_id']}\nTiling ID: {res['tiling_id']}\nN={res['N']}, Sym={res['symmetry']}", 
                     transform=ax_topo.transAxes, fontsize=10, verticalalignment='top', color='red')
        nx.draw(res['G_raw'], pos=nx.get_node_attributes(res['G_raw'], 'pos'), 
                ax=ax_topo, node_size=0, node_color='black', edge_color='gray')
        ax_topo.set_aspect('equal')
        
        # 2. Exact Tiling (Float Projection for visual)
        S2 = math.sqrt(2) / 2.0
        pos_float = {u: (float(v.x) + S2*(float(v.y)-float(v.w)), 
                         float(v.z) + S2*(float(v.y)+float(v.w))) 
                     for u, v in res['pos_solved'].items()}
        
        for u, v in res['G_solved'].edges():
            ax_tile.plot([pos_float[u][0], pos_float[v][0]], 
                         [pos_float[u][1], pos_float[v][1]], 'b-', lw=1.5)
        for u in res['G_solved'].nodes():
            ax_tile.plot(pos_float[u][0], pos_float[u][1], 'ko', markersize=3)
        ax_tile.set_aspect('equal')
        ax_tile.axis('off')
        
        # 3. Crease Pattern
        draw_cp_ax(ax_cp, res['cp'])
        
        # 4. Folded State
        draw_fold_ax(ax_fold, res['fold'])
        
        # 5. Output Tree
        pos_out = get_proportional_tree_pos(res['tree'])
        nx.draw(res['tree'], pos_out, with_labels=False, node_color='green', edge_color='gray', ax=ax_tree, node_size=20)
        ax_tree.set_aspect('equal')
        
    plt.tight_layout()
    plt.show()

# =============================================================================
# USAGE EXAMPLE
# =============================================================================
if __name__ == "__main__":

    from src.engine.tree import random_tree
    
    # 1. Generate a mock target query tree
    q_tree = random_tree(20)
    
    # 2. Run Pipeline
    query_results = query_tilings(q_tree, N=4, symmetry="diag", n=5, weight_method="value_decay", weight_param=0.05)
    
    # 3. Render
    plot_query_megaplot(q_tree, query_results)