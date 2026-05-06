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
import networkx as nx
import matplotlib.pyplot as plt
from matplotlib.widgets import Button

# Import your pipeline functions
from database.tilings.query import query_tilings, plot_query_megaplot

class TreeDrawer:
    def __init__(self, N=4, symmetry="none", n_results=5):
        self.N = N
        self.symmetry = symmetry
        self.n_results = n_results
        
        # Initialize Figure and Axes
        self.fig, self.ax = plt.subplots(figsize=(8, 8))
        self.fig.canvas.manager.set_window_title("Draw Query Tree")
        plt.subplots_adjust(bottom=0.15) # Make room for the button
        
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

        # Setup Button
        self.btn_ax = plt.axes([0.4, 0.02, 0.2, 0.06])
        self.btn_run = Button(self.btn_ax, 'Run Query', color='lightblue', hovercolor='0.975')
        self.btn_run.on_clicked(self.run_query)

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
            else:
                # No node selected, do nothing
                pass
                
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

    def run_query(self, event):
        """Converts the drawing to a NetworkX graph and triggers the FAISS pipeline."""
        print("Building graph and extracting edge lengths...")
        
        G = nx.Graph()
        G.add_nodes_from(self.nodes.keys())
        
        # Calculate raw Euclidean distances for edge lengths
        for u, v in self.edges:
            x1, y1 = self.nodes[u]
            x2, y2 = self.nodes[v]
            dist = math.hypot(x1 - x2, y1 - y2)
            
            # Avoid division by zero if nodes are dragged exactly on top of each other
            if dist < 1e-5: dist = 1e-5 
            
            G.add_edge(u, v, length=1/dist, weight=dist)
            
        # Check connectivity (Laplacian eigenvalues require a single connected component)
        if not nx.is_connected(G):
            print("WARNING: The drawn graph is disconnected! Please connect all nodes to form a single tree.")
            return

        # Execute query
        try:
            results = query_tilings(G, N=self.N, symmetry=self.symmetry, n=self.n_results)
            plot_query_megaplot(G, results)
        except Exception as e:
            print(f"Error executing query: {e}")

if __name__ == "__main__":
    # Launch the Interactive GUI
    app = TreeDrawer(N=4, symmetry="diag", n_results=5)