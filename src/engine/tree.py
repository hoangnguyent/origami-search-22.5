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
    Tree plot helper for python debugging, not meant for frontend use. 
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
    eigenvalues = np.sort(eigenvalues)[1:] #get rid of the zero eigenvalue
    
    if len(eigenvalues) < eig_count:
        padded = np.zeros(eig_count)
        padded[:len(eigenvalues)] = eigenvalues
        return padded
    else:
        return eigenvalues[:eig_count]


# from scipy.spatial.distance import cdist

# def get_macroscopic_laplacian(G):
#     """
#     Extracts the generalized Laplacian (L) and Mass (M) matrices 
#     for the raw macroscopic tree without needing edge subdivision.
#     """
#     n = len(G.nodes)
#     L = np.zeros((n, n))
#     M = np.zeros((n, n))
    
#     nodes = list(G.nodes())
#     idx = {node: i for i, node in enumerate(nodes)}
    
#     # Calculate total length to normalize conductance/mass
#     lengths = [data.get('length', 1.0) for u, v, data in G.edges(data=True)]
#     total_length = sum(lengths) if sum(lengths) > 0 else 1.0
    
#     for u, v, data in G.edges(data=True):
#         i, j = idx[u], idx[v]
        
#         L_norm = data.get('length', 1.0) / total_length
#         conductance = 1.0 / max(L_norm, 1e-7) 
#         mass = L_norm / 2.0
        
#         L[i, j] -= conductance
#         L[j, i] -= conductance
#         L[i, i] += conductance
#         L[j, j] += conductance
        
#         M[i, i] += mass
#         M[j, j] += mass
        
#     return L, M, nodes

# def compute_hks(L, M, t_scales):
#     """
#     Computes the Heat Kernel Signature using the generalized eigenvalue problem.
#     """
#     # eigh(L, M) solves L v = w M v. Eigenvectors v are M-orthogonal.
#     w, v = eigh(L, M)
#     w = np.clip(w, 0, None) # Ensure physical non-negative decay
    
#     n_nodes = L.shape[0]
#     n_scales = len(t_scales)
#     hks = np.zeros((n_nodes, n_scales))
    
#     for i in range(n_nodes):
#         for j, t in enumerate(t_scales):
#             # HKS(i, t) = sum_k e^{-t * w_k} * (v_{ik})^2
#             hks[i, j] = np.sum(np.exp(-t * w) * (v[i, :] ** 2))
            
#     # Scale invariance: Normalize so traces start near 1.0
#     norms = hks[:, 0:1]
#     norms[norms == 0] = 1.0
#     hks = hks / norms
    
#     return hks

# def sinkhorn_knopp(C, epsilon=0.05, max_iter=100):
#     """
#     Solves the Entropic Optimal Transport problem.
#     C: Cost matrix of shape (n, m)
#     epsilon: Blur parameter controlling the entropy (higher = more stub splitting)
#     """
#     n, m = C.shape
#     K = np.exp(-C / epsilon)
    
#     # Uniform mass distribution for nodes in both trees
#     a = np.ones(n) / n
#     b = np.ones(m) / m
    
#     # Sinkhorn iterations
#     v = np.ones(m)
#     for _ in range(max_iter):
#         u = a / (K @ v + 1e-10)
#         v = b / (K.T @ u + 1e-10)
        
#     # Soft correspondence matrix
#     Pi = np.diag(u) @ K @ np.diag(v)
#     return Pi

# def get_principal_axes(X):
#     """
#     Centers, scales, and extracts the 2D principal axes (eigenvectors) of a point cloud.
#     """
#     # 1. Center at origin
#     X_c = X - np.mean(X, axis=0)
    
#     # 2. Scale to unit bounding variance
#     scale = np.max(np.linalg.norm(X_c, axis=1)) + 1e-8
#     X_norm = X_c / scale
    
#     # 3. Covariance and Eigen-decomposition
#     cov = X_norm.T @ X_norm
#     w, v = np.linalg.eigh(cov)
    
#     # 4. Sort by eigenvalue descending (Primary axis first)
#     idx = w.argsort()[::-1]
#     v = v[:, idx]
    
#     return X_norm, v

# def align_queried_tree(T_input, T_query, t_scales = None, epsilon=0.05, beta=0.1, fabrik_iters=150):
#     """
#     Aligns the queried tree using a Hybrid Cost Matrix (HKS + Spatial Prior)
#     beta: Weight of the spatial symmetry breaker
#     """
#     if t_scales is None:
#         t_scales = np.logspace(-3, 1, num=16) # 16 scales from 0.001 to 10
#     # 1. Extract HKS
#     L1, M1, nodes1 = get_macroscopic_laplacian(T_input)
#     hks1 = compute_hks(L1, M1, t_scales)
    
#     L2, M2, nodes2 = get_macroscopic_laplacian(T_query)
#     hks2 = compute_hks(L2, M2, t_scales)
    
#     # 2. Extract and Align Spatial Priors
#     # Input tree has the user's explicit coordinates
#     pos1 = np.array([T_input.nodes[n].get('pos', (0,0)) for n in nodes1])
    
#     # Query tree uses the backend's default force-directed untangled layout
#     default_pos2_dict = get_proportional_tree_pos(T_query)
#     pos2 = np.array([default_pos2_dict[n] for n in nodes2])
    
#     # Align the default layout to the user's layout
#     # pos2_aligned, pos1_norm = procrustes_align_2d(pos2, pos1)
#     pos1_norm, V1 = get_principal_axes(pos1)
#     pos2_norm, V2 = get_principal_axes(pos2)
#     # 3. Base Topological Cost (Does not change with rotation)
#     C_hks = cdist(hks1, hks2, metric='sqeuclidean')
#     C_hks = C_hks / (C_hks.max() + 1e-10)
    
#     # 4. Test all 4 PCA Orientations to solve sign ambiguity
#     best_cost = float('inf')
#     best_Pi = None
    
#     flips = [
#         np.array([[1, 0], [0, 1]]),
#         np.array([[-1, 0], [0, 1]]),
#         np.array([[1, 0], [0, -1]]),
#         np.array([[-1, 0], [0, -1]])
#     ]
    
#     for flip in flips:
#         # Construct rotation matrix aligning V2 to V1 with the given flip
#         R = V2 @ flip @ V1.T
#         pos2_aligned = pos2_norm @ R
        
#         # Spatial Cost for this specific orientation
#         C_space = cdist(pos1_norm, pos2_aligned, metric='sqeuclidean')
#         C_space = C_space / (C_space.max() + 1e-10)
        
#         # Hybrid Cost
#         C_total = C_hks + (beta * C_space)
#         Pi = sinkhorn_knopp(C_total, epsilon=epsilon)
        
#         # The transport cost evaluates how well both Topology and Space match
#         cost = np.sum(Pi * C_total)
        
#         if cost < best_cost:
#             best_cost = cost
#             best_Pi = Pi
            
#     # 5. Calculate Target Coordinates (Using the ACTUAL user coordinates)
#     col_sums = best_Pi.sum(axis=0) + 1e-10
#     Y_target = (best_Pi.T @ pos1) / col_sums[:, None]
    
#     # 6. Iterative Kinematic Resolution (Verlet with Repulsion)
#     Y = Y_target.copy()
#     edges_query = list(T_query.edges(data=True))
#     idx2 = {n: i for i, n in enumerate(nodes2)}
    
#     for _ in range(fabrik_iters):
#         # A. Tiny repulsive force to prevent 1D straight-line collapse
#         for i in range(len(Y)):
#             for j in range(i + 1, len(Y)):
#                 diff = Y[i] - Y[j]
#                 dist = np.linalg.norm(diff)
#                 if dist < 0.1:
#                     repulsion = (diff / (dist + 1e-5)) * 0.01
#                     Y[i] += repulsion
#                     Y[j] -= repulsion

#         # B. Enforce Rigid Edge Lengths
#         for u, v, data in edges_query:
#             i, j = idx2[u], idx2[v]
#             L_req = data.get('length', 1.0) 
            
#             diff = Y[j] - Y[i]
#             dist = np.linalg.norm(diff)
#             if dist < 1e-7:
#                 diff = np.array([1e-7, 0.0]); dist = 1e-7
                
#             correction = (dist - L_req) / dist * 0.5 * diff
#             Y[i] += correction
#             Y[j] -= correction
            
#     # Apply computed positions back to the queried graph
#     T_query_aligned = T_query.copy()
#     for i, n2 in enumerate(nodes2):
#         T_query_aligned.nodes[n2]['pos'] = Y[i].tolist()
        
#     return T_query_aligned, best_Pi


# # ===== Visualization helper =====
# def plot_trees(trees):
#     n = len(trees)
#     rows = math.ceil(math.sqrt(n / 2))
#     cols = math.ceil(n / rows)
#     fig, axes = plt.subplots(rows, cols, figsize=(cols * 5, rows * 5))
#     axes = axes.flatten() if n > 1 else [axes]
#     for i, ax in enumerate(axes):
#         ax.axis("off")
#     for i, tree in enumerate(trees):
#         ax = axes[i]
#         pos = get_proportional_tree_pos(tree)
#         nx.draw(tree, pos, with_labels=True, node_color='lightblue', edge_color='gray', ax=ax)
#         ax.set_title(f"Tree {i}")
#         ax.axis('equal')


#     renders_dir = "renders"
#     os.makedirs(renders_dir, exist_ok=True)
#     existing_files = [f for f in os.listdir(renders_dir) if f.endswith(".png")]
#     file_count = len(existing_files)
#     filename = f"trees_{file_count}.png"
#     filepath = os.path.join(renders_dir, filename)
#     plt.tight_layout(pad=0)
#     plt.savefig(filepath)
#     plt.close(fig)
#     print(f"Saved render to {filepath}")
# if __name__ == "__main__":
#     pass