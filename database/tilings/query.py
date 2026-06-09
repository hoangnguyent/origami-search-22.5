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
from src.engine.fold225 import cp_to_fold, fold_to_cp
from src.engine.tree import extract_eigenvalues, get_proportional_tree_pos, EIG_COUNT, RESOLUTION
from database.tilings.build_tilings import decompress_edges, Topology, Tiling
# from database.tilings.faiss_cache_hkt import get_t_scales, compute_hkt_signature, DIMENSION
from database.tilings.faiss_cache import compute_wks_signature, E_SWEEP

# =============================================================================
# FEDERATED QUERY FUNCTION
# =============================================================================

def query_tilings(query_tree, db_configs=[(4, 'none'), (4, 'diag'), (3, 'none')], n=5):
    """
    Lightning fast federated search using cached Z-Scores.
    Dynamically connects to SQLite and filters for CP uniqueness.
    """
    raw_query_eig = extract_eigenvalues(query_tree, eig_count=EIG_COUNT, resolution=RESOLUTION)
    base_hkt = compute_wks_signature(raw_query_eig).astype('float32')
    base_hkt_2d = np.array([base_hkt])

    all_raw_hits = []
    search_buffer = n * 10 
    
    print(f"\nFederated Search across {len(db_configs)} databases...")
    
    for N, sym in db_configs:
        print("hi")
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
            
            # Freeze the hashable Vertex4 dictionary alongside the binary edge states
            cp_hash = (topo.binary_state, frozenset(loaded_pos.items()))
            
            if cp_hash in seen_cps:
                continue
                
            seen_cps.add(cp_hash)
            
            # Only do the heavy math if we know for a fact it is a unique result
            G_raw = nx.Graph()
            G_raw.add_edges_from(decompress_edges(topo.binary_state, N))
            nx.set_node_attributes(G_raw, {node: node for node in G_raw.nodes()}, 'pos')
            cp = build_crease_pattern(loaded_G, loaded_pos, loaded_faces, N=N)
            cp = add_hinges(cp)
            
            fold = cp_to_fold(cp)
            res_tree, packing = fold.get_tree_and_packing(include_packing=True)
            res_packing = fold_to_cp(packing[0], inst_graph=packing[1], mv_reference=cp)
            
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
                'tree': res_tree,
                'packing': res_packing
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
        query_hkt = compute_wks_signature(raw_query_eig)
    t_scales = E_SWEEP
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
        res_hkt = compute_wks_signature(res_eig)
        normalized_res_hkt = (res_hkt - mu) / sigma
        ax_hkt.plot(t_scales, normalized_res_hkt, 'b-', label="Database Result")
        # ax_hkt.set_xscale('log')
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
        # (4, 'diag'), 
        # (4, 'none'),
        # (3, 'none'),
        # (5, 'diag')
        (3,"diag")
        ], 
    n=5)
    plot_query_megaplot(results, query_tree = tree)