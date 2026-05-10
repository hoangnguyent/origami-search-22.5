"""
Core Crease Pattern Module: Frozen Tiling Blob -> Exact Cp225
Trimmed for speed and database integration. 
Reads the serialized exact 4D states, calculates the straight skeleton using a quantized float bandaid, and routes the creases on an exact 22.5 grid.
"""

import math
import networkx as nx

from src.engine.math225_core import Vertex4D, Fraction
from src.engine.cp225 import Cp225, intersection, vertex_kawasaki, freeze, unfreeze, point_on_line
from py_straight_skeleton import compute_skeleton

# =============================================================================
# CONFIGURATION & TUNING
# =============================================================================

class SkeletonTuning:
    """
    Tunable parameters for the floating-point straight skeleton bandaid.
    """
    # Rounding threshold to force float-noise near-misses into exact collisions 
    # for the C++ sweep-line algorithm.
    QUANTIZATION_DECIMALS = 3 
    
    # Capture radius for mapping the float boundary nodes back to exact 4D nodes
    SNAP_TOLERANCE = 1e-3


# =============================================================================
# 1. DESERIALIZATION (Blob -> Exact State)
# =============================================================================

def load_frozen_blob(blob):
    """
    Deserializes the database blob back into exact mathematical objects and topologies.
    """
    # Reconstruct integer-indexed topology
    nodes = list(blob["vertices"].keys())
    
    G = nx.Graph()
    G.add_nodes_from(nodes)
    G.add_edges_from(blob["edges"])
    
    faces = blob["faces"]
    
    # Reconstruct Exact 4D Vertices
    pos_solved_exact = {}
    for n_idx, frac_tuple in blob["pos_4d"].items():
        n1, d1, n2, d2, n3, d3, n4, d4 = frac_tuple
        pos_solved_exact[n_idx] = Vertex4D(
            Fraction(n1, d1), Fraction(n2, d2), 
            Fraction(n3, d3), Fraction(n4, d4)
        )
        
    return G, pos_solved_exact, faces


# =============================================================================
# 2. STRAIGHT SKELETON BANDAID
# =============================================================================

def dedupe_exterior(exterior):
    """Removes overlapping vertices and antiparallel spikes caused by exact solver merges."""
    if not exterior: return []
    
    cleaned = [exterior[0]]
    for p in exterior[1:]:
        if math.hypot(p[0]-cleaned[-1][0], p[1]-cleaned[-1][1]) > 1e-5:
            cleaned.append(p)
    if len(cleaned) > 1 and math.hypot(cleaned[0][0]-cleaned[-1][0], cleaned[0][1]-cleaned[-1][1]) < 1e-5:
        cleaned.pop()
        
    while len(cleaned) >= 3:
        spike_found = False
        for i in range(len(cleaned)):
            prev, curr, nxt = cleaned[i-1], cleaned[i], cleaned[(i+1)%len(cleaned)]
            dx1, dy1 = curr[0] - prev[0], curr[1] - prev[1]
            dx2, dy2 = nxt[0] - curr[0], nxt[1] - curr[1]
            
            L1, L2 = math.hypot(dx1, dy1), math.hypot(dx2, dy2)
            if L1 > 1e-5 and L2 > 1e-5:
                cross = (dx1*dy2 - dy1*dx2) / (L1*L2)
                if abs(cross) < 1e-5: 
                    cleaned.pop(i) 
                    spike_found = True
                    break
        if not spike_found: break
            
    return cleaned

def compute_skeleton_wrapper(exterior):
    """
    Aggressively quantizes the input to bypass IEEE-754 float singularities 
    in the py_straight_skeleton library.
    """
    d = SkeletonTuning.QUANTIZATION_DECIMALS
    quant_exterior = [(round(p[0], d), round(p[1], d)) for p in exterior]
    
    k = len(quant_exterior)
    area = sum(quant_exterior[i][0] * quant_exterior[(i+1)%k][1] - quant_exterior[(i+1)%k][0] * quant_exterior[i][1] for i in range(k))
    
    if abs(area) / 2.0 > 0.95: return None
    if area < 0: quant_exterior.reverse()
        
    cleaned = dedupe_exterior(quant_exterior)
    if len(cleaned) < 3: return None
    
    try:
        return compute_skeleton(exterior=cleaned, holes=[])
    except Exception:
        return None

def canonical_float(p):
    """Standardizes float coordinates for dictionary hashing."""
    return (round(p[0], 5), round(p[1], 5))


# =============================================================================
# 3. EXACT TOPOLOGY ROUTER
# =============================================================================

def build_crease_pattern(G, pos_solved_exact, faces, N=4, verbose=False):
    """
    Main entry point. Initializes the base Cp225 with boundary geometry, then 
    routes straight skeleton creases using exact algebraic raycasting.
    """
    # 1. Initialize Base Cp225
    n2i = {n: i for i, n in enumerate(G.nodes())}
    vertices = [pos_solved_exact[n] for n in G.nodes()]
    
    def is_border(v1_ex, v2_ex):
        x1, y1 = v1_ex.to_cartesian()
        x2, y2 = v2_ex.to_cartesian()
        return (math.isclose(x1, 0, abs_tol=1e-5) and math.isclose(x2, 0, abs_tol=1e-5)) or \
               (math.isclose(x1, 1, abs_tol=1e-5) and math.isclose(x2, 1, abs_tol=1e-5)) or \
               (math.isclose(y1, 0, abs_tol=1e-5) and math.isclose(y2, 0, abs_tol=1e-5)) or \
               (math.isclose(y1, 1, abs_tol=1e-5) and math.isclose(y2, 1, abs_tol=1e-5))
               
    cp_edges = []
    for u, v in G.edges():
        l_type = 'b' if is_border(pos_solved_exact[u], pos_solved_exact[v]) else 'av'
        cp_edges.append((n2i[u], n2i[v], l_type))
        
    cp = Cp225(vertices, cp_edges)
    
    # Float conversion needed ONLY to feed the C++ skeleton library
    S2 = math.sqrt(2) / 2.0
    pos_float = {u: (float(v.x) + S2*(float(v.y)-float(v.w)), 
                     float(v.z) + S2*(float(v.y)+float(v.w))) 
                 for u, v in pos_solved_exact.items()}

    # 2. Process Faces
    for face in faces:
        exterior = [pos_float[n] for n in face]
        skeleton = compute_skeleton_wrapper(exterior)
        if skeleton is None: continue
            
        # Build un-directed topological graph of the float skeleton
        skel_graph = {}
        for skv1, skv2 in skeleton.arc_iterator():
            p1 = canonical_float((float(getattr(skv1.position, 'x', skv1.position[0])), float(getattr(skv1.position, 'y', skv1.position[1]))))
            p2 = canonical_float((float(getattr(skv2.position, 'x', skv2.position[0])), float(getattr(skv2.position, 'y', skv2.position[1]))))
            skel_graph.setdefault(p1, set()).add(p2)
            skel_graph.setdefault(p2, set()).add(p1)
            
        exact_positions = {}
        node_to_idx = {}
        
        # Anchor boundaries
        for u in face:
            p_f = pos_float[u]
            min_d, closest_node = float('inf'), None
            for node in skel_graph:
                d = math.hypot(node[0] - p_f[0], node[1] - p_f[1])
                if d < min_d: min_d, closest_node = d, node
                    
            if min_d < SkeletonTuning.SNAP_TOLERANCE and closest_node is not None:
                exact_positions[closest_node] = cp.vertices[n2i[u]]
                node_to_idx[closest_node] = n2i[u]
                
        # Iteratively resolve internal nodes
        unresolved = set(skel_graph.keys()) - set(exact_positions.keys())
        while unresolved:
            progress = False
            for node in list(unresolved):
                resolved_nbrs = [nbr for nbr in skel_graph[node] if nbr in exact_positions]
                if len(resolved_nbrs) >= 2:
                    N_e = None
                    for i in range(len(resolved_nbrs)):
                        for j in range(i+1, len(resolved_nbrs)):
                            A_f, B_f = resolved_nbrs[i], resolved_nbrs[j]
                            angle_A = int(round(math.atan2(node[1] - A_f[1], node[0] - A_f[0]) / (math.pi/8))) % 16
                            angle_B = int(round(math.atan2(node[1] - B_f[1], node[0] - B_f[0]) / (math.pi/8))) % 16
                            
                            N_e = intersection(exact_positions[A_f], exact_positions[B_f], angle_A, angle_B)
                            if N_e is not None: break
                        if N_e is not None: break
                            
                    if N_e is not None:
                        exact_positions[node] = N_e
                        #  Deduplicate coincident exact vertices
                        if N_e in cp.vertices:
                            node_to_idx[node] = cp.vertices.index(N_e)
                        else:
                            cp.vertices.append(N_e)
                            node_to_idx[node] = len(cp.vertices) - 1
                        unresolved.remove(node)
                        progress = True
                        break
                        
            if not progress:
                if verbose:
                    print("Warning: Skeleton topology contains unresolvable internal vertices. (Likely Float Degeneracy)")
                break

        # Tag Reflex Bounds for MV assignments
        reflex_cp_indices = set()
        k = len(face)
        for i in range(k):
            u_id, v_id, w_id = face[i-1], face[i], face[(i+1)%k]
            p_u, p_v, p_w = pos_float[u_id], pos_float[v_id], pos_float[w_id]
            dx1, dy1 = p_v[0] - p_u[0], p_v[1] - p_u[1]
            dx2, dy2 = p_w[0] - p_v[0], p_w[1] - p_v[1]
            if (dx1 * dy2 - dy1 * dx2) > -1e-5:
                reflex_cp_indices.add(n2i[v_id])

        # Write creases to CP
        for p1 in skel_graph:
            for p2 in skel_graph[p1]:
                if p1 < p2: 
                    if p1 in node_to_idx and p2 in node_to_idx:
                        idx1, idx2 = node_to_idx[p1], node_to_idx[p2]
                        v1, v2 = cp.vertices[idx1], cp.vertices[idx2]
                        
                        # Implicit Degeneracy Filter: Skip 0-length edges created by exact collisions
                        if v1.x == v2.x and v1.y == v2.y and v1.z == v2.z and v1.w == v2.w:
                            continue
                        
                        exists = any(((u == idx1 and v == idx2) or (u == idx2 and v == idx1)) for u, v, _ in cp.edges)
                        if not exists:
                            l_type = "rv" if (idx1 in reflex_cp_indices or idx2 in reflex_cp_indices) else "rm"
                            cp.edges.append((idx1, idx2, l_type))
                            
    return cp
def add_hinges(cp):
    """
    Post-processes the Cp225 to enforce flat foldability by adding/removing hinges.

    For each skeleton vertex with an odd number of creases, it checks Kawasaki foldability. 
    If invalid, it tests all valid raycast directions (or hinge removals) on a fast clone,
    evaluating the number of operations and secondary Kawasaki violations. It applies 
    the transformation that minimizes the hinge count and overall errors.
    """
    def get_odd_errors(current_cp):
        """Helper to fetch only odd-degree vertices violating Kawasaki, 
        filtering out those with 'b' (border) or 'av' (axial valley) neighbors."""
        current_cp.get_vertex_neighbors()
        errors = current_cp.kawasaki_errors()
        
        return [
            v for v in errors 
            if len(current_cp.vertex_neighbors[v]) % 2 != 0 
        ]

    # Iteratively fix the crease pattern until no odd-degree Kawasaki errors remain
    while True:
        odd_errors = get_odd_errors(cp)
        if not odd_errors:
            break
        # print(f"Found {len(odd_errors)} odd-degree Kawasaki errors. Attempting to resolve...")
            
        target_v = odd_errors[0]
        nbrs = cp.vertex_neighbors[target_v]
        existing_angles = [angle for _, angle, _ in nbrs]
        
        # Cost metric: (Remaining Odd Kawasaki Errors, Operations/Hinges Added)
        best_cost = (float('inf'), float('inf'))
        best_cp = None
        
        # -----------------------------------------------------
        # Strategy A: Try ADDING a hinge
        # -----------------------------------------------------
        for a in {0, 2, 4, 6, 8, 10, 12, 14}:
            if a not in existing_angles:
                # If adding this angle locally satisfies Kawasaki
                if vertex_kawasaki(existing_angles + [a]):
                    
                    # ULTRA-FAST CLONE: Shallow copy the lists (tuples/Vertex4D are immutable)
                    temp_cp = Cp225(cp.vertices[:], cp.edges[:])
                    res, ops = temp_cp.ray_cast(target_v, a, 0)
                    
                    if res is not None:
                        # Fast pythonic conversion of 'm' to 'h'
                        temp_cp.edges = [(u, v, 'h' if lt == 'm' else lt) for u, v, lt in temp_cp.edges]
                        
                        # Evaluate global consequences
                        cost = (len(get_odd_errors(temp_cp)), ops)
                        if cost < best_cost:
                            best_cost = cost
                            best_cp = temp_cp
                            
        # -----------------------------------------------------
        # Strategy B: Try REMOVING an existing hinge
        # -----------------------------------------------------
        for nbr_v, angle, l_type in nbrs:
            if l_type == 'h':
                test_angles = existing_angles.copy()
                test_angles.remove(angle)
                
                # If removing this hinge locally satisfies Kawasaki
                if vertex_kawasaki(test_angles):
                    # Locate the edge index in the global list
                    edge_idx = next((i for i, (u, v, _) in enumerate(cp.edges) if {u, v} == {target_v, nbr_v}), None)
                            
                    if edge_idx is not None:
                        # ULTRA-FAST CLONE
                        temp_cp = Cp225(cp.vertices[:], cp.edges[:])
                        res, ops = temp_cp.remove_crease(edge_idx)
                        
                        if res is not None:
                            cost = (len(get_odd_errors(temp_cp)), ops)
                            if cost < best_cost:
                                best_cost = cost
                                best_cp = temp_cp
                                
        # -----------------------------------------------------
        # Commit the best transformation
        # -----------------------------------------------------
        if best_cp is not None:
            # Overwrite the original object's state directly
            cp.vertices = best_cp.vertices
            cp.edges = best_cp.edges
            cp.faces = best_cp.faces
            # Nuke the cache so it properly rebuilds on the next loop
            if hasattr(cp, '_neighbors_cache'):
                del cp._neighbors_cache
                del cp._cache_key
        else:
            print(f"Warning: Could not resolve Kawasaki error for vertex {target_v} without infinite looping or bounds.")
            break
            
    return cp
# =============================================================================
# 4. DEBUG & VISUALIZATION
# =============================================================================
COLORS = {
    'rm': 'red',  #ridge mountain
    'rv': 'blue', #ridge valley
    'av': 'blue', #axial valley
    'hm': 'red',  #hinge mountain
    'hv': 'blue', #hinge valley
    'h': 'grey', #unknown/flat hinge
    'v': 'blue', 
    'm': 'red',
    'b': 'black'
}
import matplotlib.pyplot as plt
def draw_cp_ax(ax, cp, title="Crease Pattern", debug = False):
    ax.set_title(title, fontsize=14)
    
    # Ensure neighbors are computed to access neighbor counts
    cp.get_vertex_neighbors()
    
    # Draw edges
    for t, x1, y1, x2, y2 in cp.render():
        ax.plot([x1, x2], [y1, y2], color=COLORS.get(t, 'grey'), 
                lw=(1 if t in {'h','hv','hm'} else 2), zorder=2, alpha=0.7)
    
    # Draw vertices and crease count labels
    if debug:
        for i, v in enumerate(cp.vertices):
            x, y = v.to_cartesian()
            ax.scatter(x, y, color='black', s=10, zorder=3, alpha=0.3)
            
            # Get count from the neighbor list populated by get_vertex_neighbors()
            crease_count = len(cp.vertex_neighbors[i])
            
            # Add text label with a slight offset so it doesn't overlap the point
            ax.text(x + 0.02, y + 0.02, str(crease_count), 
                    fontsize=8, color='blue', zorder=4)
            
            # look for non-kawasaki vertices (even or odd) and flag
        for i, nbrs in enumerate(cp.vertex_neighbors):
            angles = [angle for _, angle, _ in nbrs]
            if angles and not vertex_kawasaki(angles):
                x, y = cp.vertices[i].to_cartesian()
                ax.scatter(x, y, color='magenta', s=50, zorder=4, alpha=0.8, marker='X')
        
    ax.set_aspect('equal')
    ax.axis('off')
if __name__ == "__main__":
    import matplotlib.pyplot as plt
    import random
    from src.engine.topology2tiling import solve_tiling, export_frozen_blob, extract_topology

    from src.engine.fold225 import cp_to_fold, plot_multi_state_grid, plot_multiple
    from src.engine.tree import extract_eigenvalues

    import cProfile
    import pstats
    # 1. Plotter


    # 2. Pipeline Integration Test

    profiler = cProfile.Profile()
    profiler.enable()
    fig, axes = plt.subplots(2, 4, figsize=(16, 4))
    axes_flat = axes.flatten()
    sample = random.sample(range(1, 9000), 20)
    # sample = [6270]
    cps = []
    folds = []
    labels = []
    trees = []

    for i, db_id in enumerate(sample):
        # Simulate Module 1 (extract frozen topology -> tiling)
        G_raw = extract_topology(db_id, db_name="topologies_4_diag.db", N=4)
        output = solve_tiling(G_raw, symmetry='diag', N=4, verbose=False, time_limit = 10)
        if output is None:
            print(Warning(f"ID {db_id}: Failed to solve within time limit."))
            # axes_flat[i].set_title(f"ID {db_id} (Solve Failed)", color='red')
            # axes_flat[i].axis('off')
            continue
        G_solved, pos_init, pos_solved_exact, faces, n2i = output
        blob = export_frozen_blob(G_solved, pos_solved_exact, n2i, faces)
        
        # Simulate Module 2 (Blob -> CP)
        loaded_G, loaded_pos, loaded_faces = load_frozen_blob(blob)
        cp = build_crease_pattern(loaded_G, loaded_pos, loaded_faces, N=4)
        cp = add_hinges(cp)
        cps.append(cp)
        
        # Simulate Module 3 (CP -> tree)
        fold = cp_to_fold(cp)
        folds.append(fold)
        tree = fold.get_tree_and_packing()[0]
        embedding = extract_eigenvalues(tree, eig_count = 32)

        # labels.append(f"ID {db_id}")
        # draw_cp_ax(axes_flat[i], cp, title=f"CP from Blob (ID {db_id})")
        print(f"ID {db_id}: Successfully processed full pipeline.")
            
    profiler.disable()
    stats = pstats.Stats(profiler)
    stats.sort_stats("cumulative")  # Sort by cumulative time
    stats.print_stats(20)  # Print the top  functions
    print("====== Plotting Results ======")
    # try:
    #     # plot_multiple(folds,debug = True)
    #     plot_multi_state_grid(folds,cps = cps, labels = labels,packing_instead_of_cp=False)
    # except Exception as e:
    #     print("Error during folding/plotting:", e)
    # plt.tight_layout()
    # plt.show()


    """
    6270
    1952
    """