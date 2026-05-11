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
import random
import faiss
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon

from sqlalchemy import create_engine, Column, Integer, LargeBinary, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# Pipeline Imports
from src.engine.tiling2cp import load_frozen_blob, build_crease_pattern, add_hinges
from src.engine.cp225 import canonicalize, unfreeze
from src.engine.fold225 import cp_to_fold
from src.engine.tree import extract_eigenvalues, get_proportional_tree_pos, EIG_COUNT, RESOLUTION
from database.tilings.build_tilings import decompress_edges, Topology, Tiling
from database.tilings.faiss_cache import get_t_scales, compute_hkt_signature, DIMENSION

# =============================================================================
# 2. RAW CACHE & EPHEMERAL FAISS MANAGEMENT
# =============================================================================

# def sync_raw_cache(session, N, symmetry):
#     """
#     Syncs the database to a raw .npy file. 
#     Loading .npy is >100x faster than querying SQLite, allowing us to 
#     dynamically weight and rebuild the FAISS index in RAM instantly at query time.
#     """
#     npy_path = f"database/tilings/storage/raw_embeddings_{N}_{symmetry}.npy"
#     map_path = f"database/tilings/storage/faiss_map_{N}_{symmetry}.pkl"
    
#     # 1. Load existing cache
#     if os.path.exists(npy_path) and os.path.exists(map_path):
#         raw_matrix = np.load(npy_path)
#         with open(map_path, 'rb') as f:
#             faiss_map = pickle.load(f)
#     else:
#         raw_matrix = np.empty((0, DIMENSION), dtype=np.float32)
#         faiss_map = []

#     last_indexed_id = faiss_map[-1] if faiss_map else -1
    
#     # 2. Fetch missing embeddings from DB
#     new_records = session.query(Tiling.id, Tiling.embedding).filter(Tiling.id > last_indexed_id).all()
    
#     # 3. Update and Save if new data exists
#     if new_records:
#         print(f"Syncing {len(new_records)} new embeddings from DB to local raw cache...")
#         new_embeddings = np.vstack([np.frombuffer(r.embedding, dtype=np.float32) for r in new_records])
#         raw_matrix = np.vstack([raw_matrix, new_embeddings])
#         faiss_map.extend([r.id for r in new_records])
        
#         np.save(npy_path, raw_matrix)
#         with open(map_path, 'wb') as f:
#             pickle.dump(faiss_map, f, protocol=pickle.HIGHEST_PROTOCOL)
            
#     return raw_matrix, faiss_map

# =============================================================================
# 3. QUERY PIPELINE
# =============================================================================

# def load_db_cache(N, symmetry):
#     """
#     Loads the SQLite session, NumPy cache, and FAISS map for a specific database.
#     If the cache doesn't exist, it builds it.
#     """
#     db_uri = f'sqlite:///database/tilings/storage/tilings_{N}_{symmetry}.db'
#     cache_file = f'database/tilings/storage/cache_{N}_{symmetry}.npy'
#     map_file = f'database/tilings/storage/map_{N}_{symmetry}.pkl'
    
#     engine = create_engine(db_uri)
#     Session = sessionmaker(bind=engine)
#     session = Session()
    
#     # Check if cache needs building
#     if not os.path.exists(cache_file) or not os.path.exists(map_file):
#         print(f"Building cache for N={N}, Sym={symmetry}...")
#         all_tilings = session.query(Tiling.id, Tiling.embedding).all()
        
#         embeddings = []
#         faiss_map = {}
#         for idx, (t_id, emb_bytes) in enumerate(all_tilings):
#             emb_array = np.frombuffer(emb_bytes, dtype=np.float32)
#             embeddings.append(emb_array)
#             faiss_map[idx] = t_id
            
#         embeddings_np = np.array(embeddings, dtype=np.float32)
#         np.save(cache_file, embeddings_np)
#         with open(map_file, 'wb') as f:
#             pickle.dump(faiss_map, f)
            
#     # Load Cache
#     embeddings_np = np.load(cache_file)
#     with open(map_file, 'rb') as f:
#         faiss_map = pickle.load(f)
        
#     return session, embeddings_np, faiss_map


# =============================================================================
# FEDERATED QUERY FUNCTION
# =============================================================================
def random_pull_from_db(N, symmetry, n=5):
    """Fetches a random tiling from the specified database for testing/debugging."""
    db_uri = f'sqlite:///database/tilings/storage/tilings_{N}_{symmetry}.db'
    engine = create_engine(db_uri)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    count = session.query(Tiling).count()
    print(f"Sampling {n} tilings from DB with N={N}, Symmetry={symmetry} (Total Tilings: {count})...")
    tiling_ids = random.sample(range(1, count), n)
    results = []
    for tiling_id in tiling_ids:
        tiling = session.get(Tiling, tiling_id)
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
            'N': N,
            'symmetry': symmetry,
            'topology_id': topo.id,
            'tiling_id': tiling.id,
            'G_solved': loaded_G,
            'G_raw': G_raw,
            'pos_solved': loaded_pos,
            'cp': cp,
            'fold': fold,
            'tree': res_tree
        })
    return results

# def query_tilings(query_tree, db_configs=[(4, 'none'), (4, 'diag'), (3, 'none')], n=5):
#     """
#     Lightning fast federated search using cached PCA Whitening.
#     Dynamically connects to SQLite only for the global top hits to reconstruct geometry.
#     """
#     # 1. Compute Base HKT Signature for the Query
#     raw_query_eig = extract_eigenvalues(query_tree, eig_count=EIG_COUNT, resolution=RESOLUTION)
#     base_hkt = compute_hkt_signature(raw_query_eig, dim=DIMENSION).astype('float32')
    
#     # FAISS expects 2D arrays
#     base_hkt_2d = np.array([base_hkt])

#     all_raw_hits = []
    
#     print(f"Federated Search across {len(db_configs)} databases...")
    
#     # 2. Federated Search (Pure FAISS, No SQLite)
#     # 2. Federated Search (Pure FAISS, No SQLite)
#     for N, sym in db_configs:
#         prefix = f"database/tilings/faiss_cache/db_{N}_{sym}"
#         try:
#             # Load cached map and Z-score parameters
#             with open(f"{prefix}_data.pkl", 'rb') as f:
#                 cache_data = pickle.load(f)
                
#             faiss_map = cache_data['faiss_map']
#             mu = cache_data['mu']
#             sigma = cache_data['sigma']
            
#             index = faiss.read_index(f"{prefix}_l2.index")
            
#             if index.ntotal == 0:
#                 continue

#             # Apply THIS database's Z-Score to the user's query
#             z_query = (base_hkt_2d - mu) / sigma
#             z_query = z_query.astype(np.float32)
            
#             # Query the index
#             D, I = index.search(z_query, n)
            
#             for dist, idx in zip(D[0], I[0]):
#                 if idx != -1:
#                     all_raw_hits.append({
#                         'distance': float(dist),
#                         'tiling_id': faiss_map[int(idx)],
#                         'N': N,
#                         'symmetry': sym
#                     })
#         except Exception as e:
#             print(f"Warning: Failed to search FAISS cache for DB {N}_{sym}. Error: {e}")

#     # 3. Sort Globally
#     all_raw_hits.sort(key=lambda x: x['distance'])
#     global_top_hits = all_raw_hits[:n]
    
#     print(f"Found Top {len(global_top_hits)} hits. Reconstructing geometry...")

#     # 4. Late Reconstruction (Connect to SQLite ONLY for the winners)
#     results = []
#     active_sessions = {}
#     try:
#         for rank, hit in enumerate(global_top_hits):
#             N, sym = hit['N'], hit['symmetry']
#             t_id = hit['tiling_id']
            
#             # Open a connection to this specific DB if we haven't already
#             if (N, sym) not in active_sessions:
#                 db_uri = f'sqlite:///database/tilings/storage/tilings_{N}_{sym}.db'
#                 engine = create_engine(db_uri)
#                 active_sessions[(N, sym)] = sessionmaker(bind=engine)()
                
#             session = active_sessions[(N, sym)]
            
#             # Fetch the actual blob
#             tiling = session.query(Tiling).filter_by(id=t_id).first()
#             if not tiling:
#                 continue
                
#             topo = session.query(Topology).filter_by(id=tiling.topology_id).first()
            
#             # Deserialize Blob
#             blob = pickle.loads(tiling.tiling_blob)
#             loaded_G, loaded_pos, loaded_faces = load_frozen_blob(blob)
#             G_raw = nx.Graph()
#             G_raw.add_edges_from(decompress_edges(topo.binary_state, N))
#             nx.set_node_attributes(G_raw, {node: node for node in G_raw.nodes()}, 'pos')
#             # Build CP
#             try:
#                 cp = build_crease_pattern(loaded_G, loaded_pos, loaded_faces, N=N)
#                 cp = add_hinges(cp)
#                 cp_frozen = canonicalize(cp)
#                 cp = unfreeze(cp_frozen)
#             except Exception as e:
#                 print(f"Error building crease pattern for tiling ID {t_id} in DB {N}_{sym}. Skipping this result.")
#                 continue
            
#             # Fold and Extract Tree
#             fold = cp_to_fold(cp)
#             try:
#                 res_tree = fold.get_tree_and_packing()[0]
#             except:
#                 print(f"Error extracting tree for tiling ID {t_id} in DB {N}_{sym}. Skipping this result.")
#                 continue
#             results.append({
#                 'rank': rank + 1,
#                 'distance': hit['distance'],
#                 'N': N,
#                 'symmetry': sym,
#                 'topology_id': topo.id if topo else None,
#                 'tiling_id': tiling.id,
#                 'G_raw': G_raw,
#                 'G_solved': loaded_G,
#                 'pos_solved': loaded_pos,
#                 'cp': cp,
#                 'fold': fold,
#                 'tree': res_tree
#             })
#     except Exception as e:
#         print(f"Error during late reconstruction: {e}")

#     finally:
#         # Guarantee all database connections are cleanly closed
#         for s in active_sessions.values():
#             s.close()
            
#     return results
def query_tilings(query_tree, db_configs=[(4, 'none'), (4, 'diag'), (3, 'none')], n=5):
    """
    Lightning fast federated search using cached Z-Scores.
    Dynamically connects to SQLite and filters for CP uniqueness.
    """
    raw_query_eig = extract_eigenvalues(query_tree, eig_count=EIG_COUNT, resolution=RESOLUTION)
    base_hkt = compute_hkt_signature(raw_query_eig, dim = DIMENSION).astype('float32')
    base_hkt_2d = np.array([base_hkt])

    all_raw_hits = []
    
    # We fetch significantly more hits from FAISS to act as a buffer 
    # in case many top results are duplicate CPs across different databases.
    search_buffer = n * 10 
    
    print(f"Federated Search across {len(db_configs)} databases...")
    
    for N, sym in db_configs:
        prefix = f"database/tilings/faiss_cache/db_{N}_{sym}"
        try:
            with open(f"{prefix}_data.pkl", 'rb') as f:
                cache_data = pickle.load(f)
                
            faiss_map = cache_data['faiss_map']
            mu = cache_data['mu']
            sigma = cache_data['sigma']
            
            index = faiss.read_index(f"{prefix}_l2.index")
            if index.ntotal == 0:
                continue

            z_query = ((base_hkt_2d - mu) / sigma).astype(np.float32)
            
            # Fetch the buffered amount
            D, I = index.search(z_query, search_buffer)
            
            for dist, idx in zip(D[0], I[0]):
                if idx != -1:
                    all_raw_hits.append({
                        'distance': float(dist),
                        'tiling_id': faiss_map[int(idx)],
                        'N': N,
                        'symmetry': sym
                    })
        except Exception as e:
            print(f"Warning: Failed to search FAISS cache for DB {N}_{sym}. Error: {e}")

    # Sort ALL hits globally across all databases
    all_raw_hits.sort(key=lambda x: x['distance'])
    
    print("Filtering top hits for unique Crease Patterns...")

    results = []
    seen_cps = set()
    active_sessions = {}
    
    try:
        for hit in all_raw_hits:
            # Stop exactly when we have enough unique results
            if len(results) >= n:
                break
                
            N, sym = hit['N'], hit['symmetry']
            t_id = hit['tiling_id']
            
            if (N, sym) not in active_sessions:
                from sqlalchemy import create_engine
                from sqlalchemy.orm import sessionmaker
                db_uri = f'sqlite:///database/tilings/storage/tilings_{N}_{sym}.db'
                engine = create_engine(db_uri)
                active_sessions[(N, sym)] = sessionmaker(bind=engine)()
                
            session = active_sessions[(N, sym)]
            
            tiling = session.query(Tiling).filter_by(id=t_id).first()
            if not tiling:
                continue
                
            topo = session.query(Topology).filter_by(id=tiling.topology_id).first()
            
            blob = pickle.loads(tiling.tiling_blob)
            loaded_G, loaded_pos, loaded_faces = load_frozen_blob(blob)
            G_raw = nx.Graph()
            G_raw.add_edges_from(decompress_edges(topo.binary_state, N))
            nx.set_node_attributes(G_raw, {node: node for node in G_raw.nodes()}, 'pos')
            cp = build_crease_pattern(loaded_G, loaded_pos, loaded_faces, N=N)
            cp = add_hinges(cp)
            
            # --- THE UNIQUENESS FILTER ---
            cp_frozen = canonicalize(cp)
            if cp_frozen in seen_cps:
                continue # Skip this hit, it's a duplicate CP!
                
            seen_cps.add(cp_frozen)
            cp = unfreeze(cp_frozen)
            # -----------------------------
            
            fold = cp_to_fold(cp)
            res_tree = fold.get_tree_and_packing()[0]
            
            results.append({
                'rank': len(results) + 1,
                'distance': hit['distance'],
                'N': N,
                'symmetry': sym,
                'topology_id': topo.id if topo else None,
                'tiling_id': tiling.id,
                'G_raw': G_raw,
                'G_solved': loaded_G,
                'pos_solved': loaded_pos,
                'cp': cp,
                'fold': fold,
                'tree': res_tree
            })
    finally:
        for s in active_sessions.values():
            s.close()
            
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

def plot_query_megaplot(results, query_tree = None):
    """
    A megaplot mapping (Topology -> Tiling -> CP -> Fold -> Output Tree) for each result.
    """
    n = len(results)
    if n == 0:
        plt.show()
        return

    fig, axes = plt.subplots(n, 6, figsize=(20, 4 * n))
    if n == 1: axes = [axes] # Ensure 2D indexing works for n=1
    
    col_titles = ["1. Topology", "2. Exact Tiling", "3. Crease Pattern", "4. Folded State", "5. Resulting Tree","6. Profile match"]
    if query_tree is not None:
        raw_query_eig = extract_eigenvalues(query_tree, eig_count=EIG_COUNT, resolution=RESOLUTION)
        query_hkt = compute_hkt_signature(raw_query_eig, dim=DIMENSION)
    t_scales = get_t_scales(dim=DIMENSION)
    for i, res in enumerate(results):
        ax_topo, ax_tile, ax_cp, ax_fold, ax_tree, ax_hkt = axes[i]
        
        # Set Titles on Top Row
        if i == 0:
            for ax, title in zip(axes[i], col_titles):
                ax.set_title(title, fontsize=14, fontweight='bold')
                
        # 1. Topology
        if query_tree is not None: # is a query, not a random db sample
            ax_topo.text(0.05, 0.95, f"Weighted L2 Dist: {res['distance']:.5f}\nTopology ID: {res['topology_id']}\nTiling ID: {res['tiling_id']}\nN={res['N']}, Sym={res['symmetry']}",transform=ax_topo.transAxes, fontsize=10, verticalalignment='top', color='red')
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

        # 6. Plot heat profile
        
        prefix = f"database/tilings/faiss_cache/db_{res['N']}_{res['symmetry']}"
            # Load cached Z-score parameters
        with open(f"{prefix}_data.pkl", 'rb') as f:
            cache_data = pickle.load(f)
        mu = cache_data['mu']
        sigma = cache_data['sigma']

        if query_tree is not None:
            normalized_query_hkt = (query_hkt - mu) / sigma
            ax_hkt.plot(t_scales, normalized_query_hkt, 'k--', label="User Query", alpha=0.7)
        res_eig = extract_eigenvalues(res['tree'], eig_count=EIG_COUNT, resolution=RESOLUTION)
        res_hkt = compute_hkt_signature(res_eig, dim=DIMENSION)
        normalized_res_hkt = (res_hkt - mu) / sigma
        ax_hkt.plot(t_scales, normalized_res_hkt, 'b-', label="Database Result")
        ax_hkt.set_xscale('log')
        # ax_hkt.set_title("Heat Kernel Trace Z(t)")
        ax_hkt.set_xlabel("Time (t)")
        ax_hkt.set_ylabel("Normalized Heat Z")
        if i == 0: ax_hkt.legend()
        

    plt.tight_layout()
    plt.show()


# =============================================================================
# USAGE EXAMPLE
# =============================================================================
if __name__ == "__main__":
    # Example: Random Pull from DB
    from src.engine.tree import random_tree
    tree = random_tree(n=20)
    # random_results = random_pull_from_db(N=4, symmetry='diag', n=5)
    results = query_tilings(tree, db_configs=[
        (4, 'diag'), 
        (4, 'none'),
        (3, 'none'),
        (5, 'diag')
        ], 
    n=5)
    plot_query_megaplot(results, query_tree = tree)