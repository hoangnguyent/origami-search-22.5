"""
Query Pipeline: Tree -> FAISS -> Megaplot

Searches the exact tiling database for the closest matching trees using FAISS. 
Manages the FAISS index dynamically on disk for rapid <1s querying at scale.
Reconstructs and plots the exact geometry and folded states of the top matches.
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
from src.engine.fold225 import cp_to_fold
from src.engine.tree import extract_eigenvalues
from database.tilings.build_tilings import decompress_edges, Topology, Tiling, DestBase

# =============================================================================
# FAISS DISK MANAGEMENT
# =============================================================================
DIMENSION = 32

def sync_faiss_index(session, N, symmetry):
    """
    Loads the FAISS index from disk. If the database has new tilings, 
    it extracts their embeddings, updates the index, and saves it back to disk.
    """
    index_path = f"database/tilings/storage/faiss_{N}_{symmetry}.index"
    map_path = f"database/tilings/storage/faiss_map_{N}_{symmetry}.pkl"
    
    # 1. Load or Initialize FAISS Index
    if os.path.exists(index_path) and os.path.exists(map_path):
        index = faiss.read_index(index_path)
        with open(map_path, 'rb') as f:
            faiss_map = pickle.load(f)
    else:
        index = faiss.IndexFlatL2(DIMENSION)
        faiss_map = [] # Maps FAISS internal IDs (0, 1, 2...) to Database Tiling IDs

    last_indexed_id = faiss_map[-1] if faiss_map else -1
    
    # 2. Fetch missing embeddings from DB
    new_records = session.query(Tiling.id, Tiling.embedding).filter(Tiling.id > last_indexed_id).all()
    
    # 3. Update and Save if new data exists
    if new_records:
        print(f"Syncing {len(new_records)} new embeddings to FAISS index...")
        embeddings = np.vstack([np.frombuffer(r.embedding, dtype=np.float32) for r in new_records])
        index.add(embeddings)
        faiss_map.extend([r.id for r in new_records])
        
        faiss.write_index(index, index_path)
        with open(map_path, 'wb') as f:
            pickle.dump(faiss_map, f, protocol=pickle.HIGHEST_PROTOCOL)
            
    return index, faiss_map


# =============================================================================
# QUERY PIPELINE
# =============================================================================
def query_tilings(query_tree, N=4, symmetry="none", n=5):
    """
    Queries the database for the closest 'n' tilings to the provided query tree.
    Reconstructs the full pipeline (Graph -> Exact Tiling -> CP -> Folded -> Tree).
    """
    print(f"Executing FAISS query for top {n} matches...")
    
    # Connect to Database
    db_uri = f'sqlite:///database/tilings/storage/tilings_{N}_{symmetry}.db'
    engine = create_engine(db_uri)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # Get/Update FAISS
    index, faiss_map = sync_faiss_index(session, N, symmetry)
    if index.ntotal == 0:
        print("Database is empty. No results to return.")
        return []

    # Process Query Tree
    target_embedding = extract_eigenvalues(query_tree, dim=DIMENSION)
    query_vec = np.array([target_embedding], dtype=np.float32)
    
    # Execute Search
    n_search = min(n, index.ntotal)
    D, I = index.search(query_vec, n_search)
    
    results = []
    
    # Reconstruct the Pipeline for Top N Results
    for dist, idx in zip(D[0], I[0]):
        tiling_id = faiss_map[idx]
        tiling = session.query(Tiling).get(tiling_id)
        topo = session.query(Topology).get(tiling.topology_id)
        
        # 1. Reconstruct Raw Topology
        G_raw = nx.Graph()
        G_raw.add_edges_from(decompress_edges(topo.binary_state, N))
        nx.set_node_attributes(G_raw, {node: node for node in G_raw.nodes()}, 'pos')
        
        # 2. Reconstruct Exact Tiling
        blob_dict = pickle.loads(tiling.tiling_blob)
        loaded_G, loaded_pos, loaded_faces = load_frozen_blob(blob_dict)
        
        # 3. Post-Process (CP -> Fold -> Tree)
        cp = build_crease_pattern(loaded_G, loaded_pos, loaded_faces, N=N)
        cp = add_hinges(cp)
        fold = cp_to_fold(cp)
        res_tree = fold.get_tree_and_packing()[0]
        
        results.append({
            'topology_id': topo.id,
            'distance': dist,
            'G_raw': G_raw,
            'G_solved': loaded_G,
            'pos_solved': loaded_pos,
            'cp': cp,
            'fold': fold,
            'tree': res_tree
        })
        
    return results


# =============================================================================
# VISUALIZATION & MEGAPLOT
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
    fig_input, ax_input = plt.subplots(figsize=(4, 4))
    nx.draw_kamada_kawai(query_tree, ax=ax_input, node_size=50, node_color='purple', edge_color='black')
    ax_input.set_title("Input Query Tree", fontsize=14, fontweight='bold')
    ax_input.set_aspect('equal')
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
        ax_topo.text(0.05, 0.95, f"Dist: {res['distance']:.3f}\nTopo ID: {res['topology_id']}", 
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
        nx.draw_kamada_kawai(res['tree'], ax=ax_tree, node_size=30, node_color='green', edge_color='black')

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
    query_results = query_tilings(q_tree, N=4, symmetry="none", n=5)
    
    # 3. Render
    plot_query_megaplot(q_tree, query_results)