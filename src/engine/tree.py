"""
Helper functions related to tree handling
"""

import networkx as nx
import numpy as np
import os
import matplotlib.pyplot as plt
import math
from scipy.linalg import eigh

EIG_COUNT = 32
RESOLUTION = 0.02

def random_tree(n):
    tree = nx.random_labeled_tree(n)
    for edge in tree.edges():
        tree.edges[edge]["length"] = 1
        tree.edges[edge]["weight"] = 1.0 / tree.edges[edge]["length"]
    merge_edges(tree)[0]

    if n >=4 and len(tree.edges())==1:
        # want tree to be more than just 1 edge
        return random_tree(n)
    return tree

def merge_edges(tree):
    """
    Remove nodes of degree 2 by merging their two edges into one.
    Optimized to O(N) by calculating the merge list exactly once.
    """
    removed_nodes = set()
    
    # 1. Find all degree 2 nodes in a single pass
    to_merge = [n for n, d in tree.degree() if d == 2]
    
    # 2. Iterate through them without re-scanning the graph
    for n in to_merge:
        # Get neighbors before removing the node
        neighbors = list(tree.neighbors(n))
        
        # Safety check (optional but good practice)
        if len(neighbors) != 2:
            continue
            
        u, v = neighbors
        new_len = tree[u][n]["length"] + tree[n][v]["length"]
        
        # Mutate the graph
        tree.remove_node(n)
        tree.add_edge(u, v, length=new_len, weight=1.0 / new_len)
        
        removed_nodes.add(n)
        
    return tree, removed_nodes

def get_proportional_tree_pos(G):
    """
    Tree plot helper
    """
    if not G.nodes():
        return {}

    # 1. Calculate the full distance matrix for the tree
    full_dist_matrix = dict(nx.all_pairs_dijkstra_path_length(G, weight="length"))

    try:
        pos = nx.kamada_kawai_layout(G, dist=full_dist_matrix, scale=1.0)
    except:
        # Fallback to a basic tree layout if the matrix is problematic
        pos = nx.spring_layout(G, weight="weight", iterations=200)

    return pos

def normalize_weights(tree):
    """
    Normalize edge lengths so that the total tree efficiency is 1.
    """
    total_length = sum(nx.get_edge_attributes(tree, 'length').values())
    if total_length == 0:
        return tree  # avoid division by zero
    for u, v in tree.edges():
        # tree.edges[u, v]['length'] /= total_length
        tree.edges[u, v]['weight'] = total_length / tree.edges[u, v]['length']
    return tree

def resample_tree(G, max_norm_length=0.02):
    """
    Uniformly subdivides edges so no segment exceeds max_norm_length.
    Levels the finite-difference discretization error across all topologies.
    """
    # Get physical total length first to define the absolute target segment size
    total_length = sum(nx.get_edge_attributes(G, 'length').values())
    if total_length == 0: total_length = 1.0
    
    target_length = total_length * max_norm_length
    
    G_resampled = nx.Graph()
    node_counter = max(G.nodes) + 1 if G.nodes else 0
    
    for u, v, data in G.edges(data=True):
        l = data.get('length', 1.0)
        
        if l <= target_length:
            G_resampled.add_edge(u, v, length=l)
            continue
            
        # Calculate how many segments we need to break this edge into
        num_segments = math.ceil(l / target_length)
        segment_length = l / num_segments
        
        curr_node = u
        for _ in range(num_segments - 1):
            new_node = node_counter
            node_counter += 1
            G_resampled.add_edge(curr_node, new_node, length=segment_length)
            curr_node = new_node
            
        G_resampled.add_edge(curr_node, v, length=segment_length)
        
    return G_resampled


# ===== Main function: cleanup and extract laplacian eigenvalues =====


def extract_eigenvalues(G, eig_count=EIG_COUNT, resolution=RESOLUTION):
    """
    Extracts true metric-graph eigenvalues by combining Mesh Resampling 
    with the Generalized Mass Matrix.
    """
    # 1. Level the playing field by normalizing and discretizing the tree into uniform edge lengths
    G = resample_tree(G, max_norm_length=resolution)
    
    n = len(G.nodes)
    L = np.zeros((n, n))
    M = np.zeros((n, n))
    
    nodes = list(G.nodes())
    idx = {node: i for i, node in enumerate(nodes)}
    
    edges = []
    lengths = []
    for u, v, data in G.edges(data=True):
        l = data.get('length', 1.0) 
        edges.append((u, v))
        lengths.append(l)
        
    total_length = sum(lengths)
    if total_length == 0: total_length = 1.0
    
    for (u, v), raw_length in zip(edges, lengths):
        i, j = idx[u], idx[v]
        
        L_norm = raw_length / total_length
        conductance = 1.0 / max(L_norm, 1e-7) 
        mass = L_norm / 2.0
        
        L[i, j] -= conductance
        L[j, i] -= conductance
        L[i, i] += conductance
        L[j, j] += conductance
        
        M[i, i] += mass
        M[j, j] += mass

    eigenvalues = eigh(L, M, eigvals_only=True)
    eigenvalues = np.clip(eigenvalues, 0, None)
    eigenvalues = np.sort(eigenvalues)[1:]
    
    if len(eigenvalues) < eig_count:
        padded = np.zeros(eig_count)
        padded[:len(eigenvalues)] = eigenvalues
        return padded
    else:
        return eigenvalues[:eig_count]
# ===== Visualization helper =====
def plot_trees(trees):
    n = len(trees)
    rows = math.ceil(math.sqrt(n / 2))
    cols = math.ceil(n / rows)
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 5, rows * 5))
    axes = axes.flatten() if n > 1 else [axes]
    for i, ax in enumerate(axes):
        ax.axis("off")
    for i, tree in enumerate(trees):
        ax = axes[i]
        pos = get_proportional_tree_pos(tree)
        nx.draw(tree, pos, with_labels=True, node_color='lightblue', edge_color='gray', ax=ax)
        ax.set_title(f"Tree {i}")
        ax.axis('equal')


    renders_dir = "renders"
    os.makedirs(renders_dir, exist_ok=True)
    existing_files = [f for f in os.listdir(renders_dir) if f.endswith(".png")]
    file_count = len(existing_files)
    filename = f"trees_{file_count}.png"
    filepath = os.path.join(renders_dir, filename)
    plt.tight_layout(pad=0)
    plt.savefig(filepath)
    plt.close(fig)
    print(f"Saved render to {filepath}")