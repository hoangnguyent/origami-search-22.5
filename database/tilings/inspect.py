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
from database.tilings.faiss_cache_hkt import get_t_scales, compute_hkt_signature, DIMENSION
from database.tilings.query import draw_cp_ax, draw_fold_ax

def pull_specific_tiling(tiling_id, N, symmetry):
    """
    Directly fetches a specific tiling from the SQLite database, 
    reconstructs it, and returns the result bundle in the same 
    dictionary format as query_tilings().
    """
    print(f"Pulling Tiling ID {tiling_id} from db_{N}_{symmetry}...")
    
    db_uri = f'sqlite:///database/tilings/storage/tilings_{N}_{symmetry}.db'
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(db_uri)
    session = sessionmaker(bind=engine)()
    
    try:
        tiling = session.query(Tiling).filter_by(id=tiling_id).first()
        if not tiling:
            print(f"Error: Tiling ID {tiling_id} not found.")
            return []
            
        topo = session.query(Topology).filter_by(id=tiling.topology_id).first()
        
        # 1. Reconstruct exact tiling geometry
        blob = pickle.loads(tiling.tiling_blob)
        loaded_G, loaded_pos, loaded_faces = load_frozen_blob(blob)
        G_raw = nx.Graph()
        G_raw.add_edges_from(decompress_edges(topo.binary_state, N))
        nx.set_node_attributes(G_raw, {node: node for node in G_raw.nodes()}, 'pos')
        
        # 2. Reconstruct CP and Fold
        cp = build_crease_pattern(loaded_G, loaded_pos, loaded_faces, N=N, verbose=True)
        cp = add_hinges(cp)
        fold = cp_to_fold(cp)
        
        # 3. Extract Tree and Packing strictly from the Folded State
        res_tree, packing = fold.get_tree_and_packing(include_packing=True)
        res_packing = fold_to_cp(packing[0], inst_graph=packing[1], mv_reference=cp)
        return [{
            'rank': 1,
            'distance': 0.0,
            'N': N,
            'symmetry': symmetry,
            'topology_id': topo.id if topo else None,
            'tiling_id': tiling.id,
            'G_raw': G_raw,
            'G_solved': loaded_G,
            'pos_solved': loaded_pos,
            'cp': cp,
            'fold': fold,
            'tree': res_tree,
            'packing': res_packing
        }]
    finally:
        session.close()


def plot_specific_tiling_megaplot(results):
    """
    Plots a 7-column megaplot for a specifically pulled tiling, 
    adding the 'Packing' visualization between the CP and Folded State.
    """
    if not results:
        return
        
    res = results[0] # Operates on a single pulled result
    fig, axes = plt.subplots(1, 7, figsize=(24, 4))
    
    col_titles = ["1. Topology", "2. Exact Tiling", "3. Crease Pattern", 
                  "4. Packing", "5. Folded State", "6. Resulting Tree", "7. Profile Match"]
    
    for ax, title in zip(axes, col_titles):
        ax.set_title(title, fontsize=12, fontweight='bold')
        
    ax_topo, ax_tile, ax_cp, ax_pack, ax_fold, ax_tree, ax_hkt = axes
    
    # 1. Topology
    ax_topo.text(0.05, 0.95, f"Topology ID: {res['topology_id']}\nTiling ID: {res['tiling_id']}\nN={res['N']}, Sym={res['symmetry']}",
                 transform=ax_topo.transAxes, fontsize=10, verticalalignment='top', color='red')
    nx.draw(res['G_raw'], pos=nx.get_node_attributes(res['G_raw'], 'pos'), 
            ax=ax_topo, node_size=0, node_color='black', edge_color='gray')
    ax_topo.set_aspect('equal')
    
    # 2. Exact Tiling (Float Projection)
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
    
    # 4. Packing (Using the same cp render loop)
    draw_cp_ax(ax_pack, res['packing'])
    
    # 5. Folded State
    draw_fold_ax(ax_fold, res['fold'])
    
    # 6. Output Tree
    pos_out = get_proportional_tree_pos(res['tree'])
    nx.draw(res['tree'], pos_out, with_labels=False, node_color='green', edge_color='gray', ax=ax_tree, node_size=20)
    ax_tree.set_aspect('equal')

    # 7. Heat Profile
    try:
        prefix = f"database/tilings/faiss_cache/db_{res['N']}_{res['symmetry']}"
        with open(f"{prefix}_data.pkl", 'rb') as f:
            cache_data = pickle.load(f)
        mu = cache_data['mu']
        sigma = cache_data['sigma']
        t_scales = get_t_scales(dim=DIMENSION)
        
        res_eig = extract_eigenvalues(res['tree'], eig_count=EIG_COUNT, resolution=RESOLUTION)
        res_hkt = compute_hkt_signature(res_eig, dim=DIMENSION)
        normalized_res_hkt = (res_hkt - mu) / sigma
        ax_hkt.plot(t_scales, normalized_res_hkt, 'b-', label="Database Result")
        ax_hkt.set_xscale('log')
        ax_hkt.set_xlabel("Time (t)")
        ax_hkt.set_ylabel("Normalized Heat Z")
        ax_hkt.legend()
    except Exception as e:
        ax_hkt.text(0.5, 0.5, f"Cache Warning:\n{e}", ha='center', va='center', color='red')
        ax_hkt.axis('off')

    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    
    # results = pull_specific_tiling(346, 3, 'diag')
    # plot_specific_tiling_megaplot(results)

    # results = pull_specific_tiling(3342, 4, 'diag')
    # plot_specific_tiling_megaplot(results)

    # results = pull_specific_tiling(24603, 4, 'diag')
    # plot_specific_tiling_megaplot(results)

    results = pull_specific_tiling(20000, 4, 'none')
    # plot_specific_tiling_megaplot(results)
    