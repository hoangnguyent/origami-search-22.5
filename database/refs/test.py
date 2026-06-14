from src.engine.math225_core import Vertex4D
from database.refs.cp_lookup import lookup_vertex_v4d, lookup_multiple_vertices 
import time
import cProfile
import pstats
if __name__ == "__main__":
    profiler = cProfile.Profile()
    profiler.enable()
    # Example usage of lookup_vertex_v4d
    vertices = [
        Vertex4D(1,1,-1,1)
    ] * 10000  # Test with 10 identical vertices for benchmarking
    start_time = time.time()
    # results = [lookup_vertex_v4d(v) for v in vertices]
    results = lookup_multiple_vertices(vertices)
    end_time = time.time()
    print(f"Lookup time for 10 vertices: {end_time - start_time:.4f} seconds")

    profiler.disable()
    stats = pstats.Stats(profiler)
    stats.sort_stats("cumulative")  # Sort by cumulative time
    stats.print_stats(20)  # Print the top  function