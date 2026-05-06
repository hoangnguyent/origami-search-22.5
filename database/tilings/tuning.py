"""
Interactive Tuning Script for FAISS Eigenvalue Weighting.

Loads a saved JSON query tree and sweeps the exponential decay parameter `t`
in a logarithmic space between 0 and 1. Plots the crease patterns and resulting 
trees for the top 3 matches across 5 different `t` values, allowing the user 
to select the best one and recursively narrow the search bounds.
"""

import json
import numpy as np
import networkx as nx
import matplotlib.pyplot as plt

# Pipeline Imports
from database.tilings.query import query_tilings, draw_cp_ax
from src.engine.tree import get_proportional_tree_pos

def load_tree_from_json(filename):
    """Reconstructs the NetworkX graph with lengths and weights from the JSON."""
    with open(filename, 'r') as f:
        data = json.load(f)
        
    G = nx.Graph()
    for n_id_str, coords in data['nodes'].items():
        G.add_node(int(n_id_str), pos=(coords['x'], coords['y']))
        
    for edge in data['edges']:
        length = edge['length']
        G.add_edge(edge['u'], edge['v'], length=length, weight=1.0/length)
        
    if not nx.is_connected(G):
        raise ValueError("The saved tree is not a single connected component!")
        
    return G

def tune_t_parameter(filename, N=4, symmetry="none"):
    """
    Interactive logarithmic binary search for the optimal t parameter.
    """
    print(f"Loading query tree from {filename}...")
    G_query = load_tree_from_json(filename)
    
    # Initialize Log Space Bounds 
    # (Cannot use exact 0 for logspace, so we start at a tiny epsilon)
    t_min = 1e-4 
    t_max = 10.0
    
    iteration = 1
    
    while True:
        # Generate 5 logarithmically spaced values
        t_vals = np.logspace(np.log10(t_min), np.log10(t_max), 5)
        
        print("\n" + "="*60)
        print(f"ITERATION {iteration} | Range: [{t_min:.5f}, {t_max:.5f}]")
        print("="*60)
        for i, t in enumerate(t_vals):
            print(f"Option {i+1}: t = {t:.5f}")
            
        all_results = []
        
        # Suppress the print spam from the inner query function
        import sys, os
        old_stdout = sys.stdout
        sys.stdout = open(os.devnull, 'w')
        try:
            for t in t_vals:
                res = query_tilings(G_query, N=N, symmetry=symmetry, n=3, 
                                    weight_method="value_decay", weight_param=t)
                all_results.append(res)
        finally:
            sys.stdout.close()
            sys.stdout = old_stdout
            
        # Plot the comparison matrix (5 rows, 7 columns)
        fig, axes = plt.subplots(5, 7, figsize=(24, 18))
        fig.canvas.manager.set_window_title(f"Tuning t-parameter: Iteration {iteration}")
        
        for i, t in enumerate(t_vals):
            # Col 0: Display the T-Value
            ax_info = axes[i, 0]
            ax_info.axis('off')
            ax_info.text(0.5, 0.5, f"Option {i+1}\nt = {t:.5f}", 
                         ha='center', va='center', fontsize=16, fontweight='bold')
            
            # Col 1-6: Display the Top 3 resulting CPs and Trees
            res_list = all_results[i]
            for j in range(3):
                ax_cp = axes[i, 1 + j*2]
                ax_tree = axes[i, 2 + j*2]
                
                if j < len(res_list):
                    res = res_list[j]
                    
                    # Draw Crease Pattern
                    draw_cp_ax(ax_cp, res['cp'])
                    ax_cp.set_title(f"Rank {j+1} CP\n(Dist: {res['distance']:.4f})", fontsize=10)
                    
                    # Draw Output Tree
                    res_tree = res['tree']
                    pos_out = get_proportional_tree_pos(res_tree)
                    nx.draw(res_tree, pos_out, with_labels=False, node_color='green', 
                            edge_color='gray', ax=ax_tree, node_size=30)
                    ax_tree.set_title(f"Rank {j+1} Tree", fontsize=10)
                    ax_tree.set_aspect('equal')
                else:
                    # Hide axes if there aren't enough results
                    ax_cp.axis('off')
                    ax_tree.axis('off')
                
        plt.tight_layout()
        
        # Display plot without blocking the terminal
        plt.show(block=False) 
        plt.pause(0.1) 
        
        # Get user input from the terminal
        while True:
            try:
                choice = input("\nWhich Option produced the best global layout? (1-5, or 0 to exit): ")
                choice = int(choice)
                if 0 <= choice <= 5:
                    break
                print("Invalid choice. Please pick 1-5.")
            except ValueError:
                print("Please enter a valid number.")
                
        plt.close(fig)
        
        if choice == 0:
            print("\nExiting tuning process.")
            break
            
        # Narrow the logarithmic bounds based on the chosen option
        idx = choice - 1
        if idx == 0:
            t_max = t_vals[1]
        elif idx == 4:
            t_min = t_vals[3]
        else:
            t_min = t_vals[idx - 1]
            t_max = t_vals[idx + 1]
            
        iteration += 1

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python tune_weights.py [filename.json]")
    else:
        filename = sys.argv[1]
        tune_t_parameter(filename, N=4, symmetry="diag")