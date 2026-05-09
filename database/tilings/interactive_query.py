"""
Interactive Matplotlib GUI for drawing a query tree and searching the Tiling Database.

Controls:
- Click a node: Select it.
- Click free space (with a selected node): Create a new branch.
- Drag a node: Move it (updates relative edge lengths).
- Esc: Deselect current node.
- Backspace: Delete the selected node and its connected edges.
"""

import math
import json
import networkx as nx
import matplotlib.pyplot as plt
from matplotlib.widgets import Button, Slider

# Import your pipeline functions
from database.tilings.query import query_tilings, plot_query_megaplot
from src.engine.tree import get_proportional_tree_pos

class TreeDrawer:
    def __init__(self, db_configs=[(4, 'none'), (4, 'diag'), (3, 'none')], n_results=5):
        self.db_configs = db_configs
        self.n_results = n_results
        
        # Initialize Figure and Axes
        self.fig, self.ax = plt.subplots(figsize=(8, 8))
        self.fig.canvas.manager.set_window_title("Draw Query Tree")
        
        # Make extra room at the bottom for the slider and buttons
        plt.subplots_adjust(bottom=0.25) 
        
        self.ax.set_xlim(0, 10)
        self.ax.set_ylim(0, 10)
        self.ax.set_aspect('equal')
        self.ax.set_title("Draw Query Tree\n(Click to branch, Drag to move, Esc to deselect, Backspace to delete)", fontsize=10)
        self.ax.axis('off')

        # Graph State
        self.next_node_id = 2
        self.nodes = {0: (4.0, 5.0), 1: (6.0, 5.0)} # id -> (x, y)
        self.edges = [(0, 1)] # list of (id1, id2)
        
        # Interaction State
        self.selected_node = 1
        self.dragging_node = None
        self.hit_radius = 0.4 # Distance threshold for clicking a node

        # --- SETUP UI ELEMENTS ---
        # Slider for t-parameter (Log space: 10^val)
        self.ax_slider = plt.axes([0.25, 0.12, 0.5, 0.03])
        self.t_slider = Slider(
            ax=self.ax_slider,
            label='simple <--> complex',
            valmin=-5.0,
            valmax=1.0,
            valinit=-2.0
        )

        # Buttons
        self.btn_run_ax = plt.axes([0.3, 0.03, 0.15, 0.06])
        self.btn_run = Button(self.btn_run_ax, 'Run Query', color='lightblue', hovercolor='0.975')
        self.btn_run.on_clicked(self.run_query)

        self.btn_save_ax = plt.axes([0.55, 0.03, 0.15, 0.06])
        self.btn_save = Button(self.btn_save_ax, 'Save Tree', color='lightgreen', hovercolor='0.975')
        self.btn_save.on_clicked(self.save_tree)

        # Connect Events
        self.fig.canvas.mpl_connect('button_press_event', self.on_press)
        self.fig.canvas.mpl_connect('button_release_event', self.on_release)
        self.fig.canvas.mpl_connect('motion_notify_event', self.on_motion)
        self.fig.canvas.mpl_connect('key_press_event', self.on_key)

        self.draw_tree()
        plt.show()

    def get_closest_node(self, x, y):
        """Finds the closest node within the hit radius."""
        closest_id = None
        min_dist = float('inf')
        for n_id, (nx_val, ny_val) in self.nodes.items():
            dist = math.hypot(nx_val - x, ny_val - y)
            if dist < min_dist and dist < self.hit_radius:
                min_dist = dist
                closest_id = n_id
        return closest_id

    def on_press(self, event):
        if event.inaxes != self.ax: return
        if event.button != 1: return # Only left click

        clicked_node = self.get_closest_node(event.xdata, event.ydata)

        if clicked_node is not None:
            # Clicked an existing node -> Select and prep for dragging
            self.selected_node = clicked_node
            self.dragging_node = clicked_node
        else:
            # Clicked empty space
            if self.selected_node is not None:
                # Create new branch from selected node
                new_id = self.next_node_id
                self.next_node_id += 1
                self.nodes[new_id] = (event.xdata, event.ydata)
                self.edges.append((self.selected_node, new_id))
                self.selected_node = new_id # Auto-select the new leaf
                
        self.draw_tree()

    def on_motion(self, event):
        if self.dragging_node is not None and event.inaxes == self.ax:
            # Update position while dragging
            self.nodes[self.dragging_node] = (event.xdata, event.ydata)
            self.draw_tree()

    def on_release(self, event):
        if event.button == 1:
            self.dragging_node = None

    def on_key(self, event):
        if event.key == 'escape':
            self.selected_node = None
            self.draw_tree()
            
        elif event.key == 'backspace':
            if self.selected_node is not None:
                # Delete the node
                del_id = self.selected_node
                del self.nodes[del_id]
                # Remove connected edges
                self.edges = [e for e in self.edges if e[0] != del_id and e[1] != del_id]
                self.selected_node = None
                self.draw_tree()

    def draw_tree(self):
        """Renders the current state of the nodes and edges."""
        self.ax.clear()
        self.ax.set_xlim(0, 10)
        self.ax.set_ylim(0, 10)
        self.ax.set_title("Draw Query Tree\n(Click to branch, Drag to move, Esc to deselect, Backspace to delete)", fontsize=10)
        self.ax.axis('off')

        # Draw Edges
        for u, v in self.edges:
            x1, y1 = self.nodes[u]
            x2, y2 = self.nodes[v]
            self.ax.plot([x1, x2], [y1, y2], color='gray', lw=2, zorder=1)

        # Draw Nodes
        for n_id, (nx_val, ny_val) in self.nodes.items():
            color = 'red' if n_id == self.selected_node else 'lightblue'
            edgecolor = 'darkred' if n_id == self.selected_node else 'black'
            self.ax.plot(nx_val, ny_val, marker='o', markersize=12, 
                         markerfacecolor=color, markeredgecolor=edgecolor, zorder=2)
            
        self.fig.canvas.draw_idle()

    def save_tree(self, event):
        """Saves the current drawing as a JSON file for tuning."""
        filename = input("\nEnter filename to save tree (e.g., dragon.json): ")
        if not filename.strip():
            print("Save cancelled.")
            return
        if not filename.endswith('.json'):
            filename += '.json'
            
        export_data = {"nodes": {}, "edges": []}
        
        for n_id, (x, y) in self.nodes.items():
            export_data["nodes"][n_id] = {"x": x, "y": y}
            
        for u, v in self.edges:
            x1, y1 = self.nodes[u]
            x2, y2 = self.nodes[v]
            dist = math.hypot(x1 - x2, y1 - y2)
            if dist < 1e-5: dist = 1e-5 
            export_data["edges"].append({"u": u, "v": v, "length": dist})
            
        with open(filename, 'w') as f:
            json.dump(export_data, f, indent=4)
        print(f"Successfully saved tree to {filename}\n")

    def run_query(self, event):
        """Converts the drawing to a NetworkX graph and triggers the FAISS pipeline."""
        
        # --- Grab and calculate the t-parameter from the slider ---
        log_t = self.t_slider.val
        t_val = 10 ** log_t
        # print(f"\nBuilding graph and extracting edge lengths...")
        # print(f"Executing query with t = {t_val:.6f} (log10 = {log_t:.2f})")
        
        G = nx.Graph()
        G.add_nodes_from(self.nodes.keys())
        
        # Calculate raw Euclidean distances for edge lengths
        for u, v in self.edges:
            x1, y1 = self.nodes[u]
            x2, y2 = self.nodes[v]
            dist = math.hypot(x1 - x2, y1 - y2)
            
            if dist < 1e-5: dist = 1e-5 
            
            G.add_edge(u, v, length=dist, weight=1/dist)
            
        if not nx.is_connected(G):
            print("WARNING: The drawn graph is disconnected! Please connect all nodes to form a single tree.")
            return

        # Execute query
        try:
            results = query_tilings(
                G, 
                db_configs=self.db_configs, 
                n=self.n_results,
                # t_min = t_min,
                # t_max = t_max
            )

            plot_query_megaplot(G, results)
        except Exception as e:
            print(f"Error executing query: {e}")

if __name__ == "__main__":
    # Launch the Interactive GUI
    # dbs_to_search = [(4, 'diag'), (4, 'none'), (3, 'none'), (3, 'diag')]
    dbs_to_search = [(4, 'diag'),(3, 'diag')]

    app = TreeDrawer(db_configs = dbs_to_search, n_results=5)


"""
maybe when plotting trees, truncate small edges

heat kernel query is a bit slower. probably can speed up by caching

"""