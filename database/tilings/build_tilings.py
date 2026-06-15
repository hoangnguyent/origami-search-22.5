"""
Database Builder: Topology -> Tiling -> Crease Pattern -> Folding Tree

This script syncs unprocessed topologies from the source database, runs them through 
the exact solver pipeline, and stores the resulting frozen tilings and eigenvalue 
embeddings in a new database.

Status Flags in Topology table:
0 = Pending
1 = Success (Tiling added)
2 = Timeout (Failed to solve within time limit)
3 = CP Error (Failed during CP generation, folding, or eigenvalue extraction)
"""

import time
import os
import multiprocessing as mp
import concurrent.futures
import pickle
import numpy as np
import networkx as nx
import mmh3

from wakepy import keep
from sqlalchemy import create_engine, Column, Integer, LargeBinary, ForeignKey, Boolean, BigInteger
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.dialects.sqlite import insert

# Pipeline Imports
from src.engine.topology2tiling import solve_tiling, export_frozen_blob, canonicalize_tiling_geometry
from src.engine.tiling2cp import load_frozen_blob, build_crease_pattern, add_hinges
from src.engine.fold225 import cp_to_fold
from src.engine.cp225 import canonicalize
from src.engine.tree import extract_eigenvalues

# =============================================================================
# CONFIGURATION
# =============================================================================
N = 6
SYMMETRY = "book"
diversity_threshold = 4
num_solutions = 10
TIME_LIMIT = 30 # Internal Python DFS time limit
EXTERNAL_TIMEOUT = 35 # External backup timeout (slightly higher to allow graceful internal exit)

SOURCE_DB_URI = f'sqlite:///database/tilings/storage/topologies_{N}_{SYMMETRY}.db'
DEST_DB_URI = f'sqlite:///database/tilings/storage/tilings_{N}_{SYMMETRY}.db'

# =============================================================================
# DATABASE SCHEMAS
# =============================================================================
SourceBase = declarative_base()
DestBase = declarative_base()

class SourceState(SourceBase):
    __tablename__ = 'states'
    id = Column(Integer, primary_key=True)
    binary_state = Column(LargeBinary, nullable=False, unique=True)

class Topology(DestBase):
    __tablename__ = 'topologies'
    id = Column(Integer, primary_key=True)
    binary_state = Column(LargeBinary, nullable=False, unique=True)
    status = Column(Integer, default=0) # 0: Pending, 1: Success, 2: Timeout, 3: CP Error
    
    # Ready for future one-to-many relationship
    tilings = relationship("Tiling", back_populates="topology")

class Tiling(DestBase):
    __tablename__ = 'tilings'
    id = Column(Integer, primary_key=True)
    topology_id = Column(Integer, ForeignKey('topologies.id'), nullable=False)
    hashed_tiling = Column(BigInteger, unique=True, index=True) # <-- ADDED
    tiling_blob = Column(LargeBinary, nullable=False) # Pickled dictionary
    embedding = Column(LargeBinary, nullable=False) # Float32 Numpy Array Bytes
    
    topology = relationship("Topology", back_populates="tilings")

# =============================================================================
# EDGE DECOMPRESSION UTILS (Embedded for worker stability)
# =============================================================================
def get_ordered_internal_edges(N):
    edges = []
    for i in range(N):
        for j in range(1, N):
            edges.append(tuple(sorted(((i, j), (i+1, j)))))
            edges.append(tuple(sorted(((j, i), (j, i+1)))))
    for x in range(N):
        for y in range(N):
            edges.append(tuple(sorted(((x, y), (x+1, y+1)))))
            edges.append(tuple(sorted(((x+1, y), (x, y+1)))))
    return sorted(edges)

def get_boundary_edges(N):
    return [tuple(sorted(((i, 0), (i+1, 0)))) for i in range(N)] + \
           [tuple(sorted(((i, N), (i+1, N)))) for i in range(N)] + \
           [tuple(sorted(((0, i), (0, i+1)))) for i in range(N)] + \
           [tuple(sorted(((N, i), (N, i+1)))) for i in range(N)]

def decompress_edges(binary_blob, N):
    ordered_edges = get_ordered_internal_edges(N)
    num_bits = len(ordered_edges)
    val = int.from_bytes(binary_blob, byteorder='big')
    bit_string = bin(val)[2:].zfill(num_bits)
    
    edges = []
    for i, bit in enumerate(bit_string):
        if bit == '1':
            edges.append(ordered_edges[i])
    edges.extend(get_boundary_edges(N))
    return edges

# =============================================================================
# MULTIPROCESSING: THE PIPELINE WORKER
# =============================================================================

def clean_tree_for_storage(tree):
    """
    Strips unnecessary metadata from the tree to minimize SQLite database size.
    Converts custom exact-math objects to standard Python floats.
    Retains ONLY the 'length' attribute on edges.
    """
    # 1. Clear global graph attributes
    tree.graph.clear()
    
    # 2. Clear all node-level attributes
    for n in tree.nodes():
        tree.nodes[n].clear()
        
    # 3. Clean edge-level attributes
    for u, v, data in tree.edges(data=True):
        # Extract length, defaulting to 1.0
        raw_length = data.get('length', 1.0)
        
        clean_length = float(raw_length)
        data.clear()
        
        # Re-assign ONLY the cleaned float length
        data['length'] = clean_length
        
    return tree

def process_topology_task(topo_id, binary_state, n_val, symmetry_val, time_lim):
    try:
        edges = decompress_edges(binary_state, n_val)
        G_raw = nx.Graph()
        G_raw.add_edges_from(edges)
        pos = {node: node for node in G_raw.nodes()}
        nx.set_node_attributes(G_raw, pos, 'pos')
        
        # 1. Gather all diverse exact solutions
        outputs = solve_tiling(
            G_raw, symmetry=symmetry_val, N=n_val, verbose=False, 
            time_limit=time_lim, diversity_threshold=diversity_threshold, num_solutions=num_solutions
        )
        
        if outputs is None or len(outputs) == 0:
            return (topo_id, 2, []) # STATUS 2: Timeout / Empty List
            
        success_tilings = []
        
        # 2. Iterate through all diverse valid tilings
        for out in outputs:
            G_solved, pos_init, pos_solved_exact, faces, n2i = out
            
            # Canonicalize and prep for duplicate check
            raw_canonical_tuple = canonicalize_tiling_geometry(G_solved, pos_solved_exact, n_val)
            
            # 2. Hash the tuple into a 64-bit integer for the database!
            tiling_hash = mmh3.hash64(pickle.dumps(raw_canonical_tuple), signed=True)[0]
            blob_dict = export_frozen_blob(G_solved, pos_solved_exact, n2i, faces)
            
            # 3. Post-Processing Pipeline
            try:
                loaded_G, loaded_pos, loaded_faces = load_frozen_blob(blob_dict)
                cp = build_crease_pattern(loaded_G, loaded_pos, loaded_faces, N=n_val)
                cp = add_hinges(cp)
                
                fold = cp_to_fold(cp)
                tree = fold.get_tree_and_packing()[0]
                clean_tree = clean_tree_for_storage(tree)
                # embedding = extract_eigenvalues(tree, dim=32)
                blob_bytes = pickle.dumps(blob_dict, protocol=pickle.HIGHEST_PROTOCOL)
                # embedding_bytes = np.array(embedding, dtype=np.float32).tobytes()
                tree_bytes = pickle.dumps(clean_tree, protocol=pickle.HIGHEST_PROTOCOL)
                
                success_tilings.append({
                    "tiling_hash": tiling_hash,
                    "blob_bytes": blob_bytes,
                    "embedding_bytes": tree_bytes
                })
            except Exception as e:
                continue # If one specific tiling has a CP degeneracy, skip it but try the others
        
        if not success_tilings:
            return (topo_id, 3, []) # STATUS 3: ALL tilings failed CP generation
            
        return (topo_id, 1, success_tilings) # STATUS 1: Success
        
    except Exception as e:
        return (topo_id, 3, [])

def worker_wrapper(topo_id, binary_state, n_val, symmetry_val, time_lim, return_dict):
    """Wraps the task to pipe the tuple result back into a shared dictionary."""
    res = process_topology_task(topo_id, binary_state, n_val, symmetry_val, time_lim)
    return_dict[topo_id] = res

# =============================================================================
# MAIN ORCHESTRATOR
# =============================================================================
def main():
    print(f"=== Tiling Pipeline Initializing (N={N}, Symmetry={SYMMETRY}) ===")
    
    src_engine = create_engine(SOURCE_DB_URI)
    SrcSession = sessionmaker(bind=src_engine)
    src_session = SrcSession()
    
    dest_engine = create_engine(DEST_DB_URI)
    DestBase.metadata.create_all(dest_engine)
    DestSession = sessionmaker(bind=dest_engine)
    dest_session = DestSession()
    
    print("Syncing topologies from source database...")
    all_source_states = src_session.query(SourceState.id, SourceState.binary_state).yield_per(1000)
    
    sync_batch = []
    for s_id, s_bin in all_source_states:
        sync_batch.append({"id": s_id, "binary_state": s_bin, "status": 0})
        if len(sync_batch) >= 5000:
            stmt = insert(Topology).values(sync_batch).on_conflict_do_nothing(index_elements=['id'])
            dest_session.execute(stmt)
            sync_batch = []
            
    if sync_batch:
        stmt = insert(Topology).values(sync_batch).on_conflict_do_nothing(index_elements=['id'])
        dest_session.execute(stmt)
        
    dest_session.commit()
    print("Sync complete.")

    pending_topologies = dest_session.query(Topology.id, Topology.binary_state).filter(Topology.status == 0).all()
    total_pending = len(pending_topologies)
    
    if total_pending == 0:
        print("Database is already complete! No pending topologies found.")
        return

    print(f"Resuming operation: {total_pending} topologies left to process.")
    
    num_workers = max(1, mp.cpu_count() - 2)
    print(f"Spinning up {num_workers} parallel workers...")
    
    t0 = time.time()
    processed_count = 0
    success_count = 0
    timeout_count = 0
    error_count = 0

    manager = mp.Manager()
    return_dict = manager.dict()
    
    active_processes = {}  
    pending_iter = iter(pending_topologies)
    
    while True:
        # --- 1. Fill Available Cores ---
        while len(active_processes) < num_workers:
            try:
                topo = next(pending_iter)
                p = mp.Process(target=worker_wrapper, args=(
                    topo.id, topo.binary_state, N, SYMMETRY, TIME_LIMIT, return_dict
                ))
                p.start()
                active_processes[topo.id] = (p, time.time())
            except StopIteration:
                break 

        if not active_processes:
            break

        # --- 2. Monitor Running Processes ---
        current_time = time.time()
        for topo_id in list(active_processes.keys()):
            if topo_id not in active_processes:
                continue
                
            p, start_time = active_processes[topo_id]

            # Case A: The process finished gracefully
            if not p.is_alive():
                p.join()
                active_processes.pop(topo_id, None) 
                processed_count += 1
                
                res = return_dict.pop(topo_id, None)
                
                if res is not None:
                    res_topo_id, status, tilings_data = res
                    dest_session.query(Topology).filter(Topology.id == res_topo_id).update({"status": status})
                    
                    if status == 1:
                        # 1. Deduplicate hashes *within* the returned list itself
                        tiling_inserts = []
                        seen_hashes = set()
                        for t_data in tilings_data:
                            h = t_data["tiling_hash"]
                            if h not in seen_hashes:
                                seen_hashes.add(h)
                                tiling_inserts.append({
                                    "topology_id": res_topo_id,
                                    "hashed_tiling": h,
                                    "tiling_blob": t_data["blob_bytes"],
                                    "embedding": t_data["embedding_bytes"]
                                })
                        
                        # 2. Bulk Insert with native SQLite Conflict Ignorance
                        if tiling_inserts:
                            try:
                                stmt = insert(Tiling).values(tiling_inserts).on_conflict_do_nothing(index_elements=['hashed_tiling'])
                                dest_session.execute(stmt)
                                success_count += 1
                            except Exception as e:
                                print(f"\nDB Error on Topo {res_topo_id}: {e}")
                                dest_session.query(Topology).filter(Topology.id == res_topo_id).update({"status": 3})
                                error_count += 1
                                
                    elif status == 2:
                        timeout_count += 1
                    elif status == 3:
                        error_count += 1
                else:
                    dest_session.query(Topology).filter(Topology.id == topo_id).update({"status": 3})
                    error_count += 1

            # Case B: Exceeded the External Timeout
            elif current_time - start_time > EXTERNAL_TIMEOUT:
                p.terminate()
                p.join()
                active_processes.pop(topo_id, None) 
                
                processed_count += 1
                timeout_count += 1
                dest_session.query(Topology).filter(Topology.id == topo_id).update({"status": 2})

        # --- 3. Save Progress & Prevent CPU Spinning ---
        if processed_count % 20 == 0 or len(active_processes) == 0:
            dest_session.commit()
            elapsed = time.time() - t0
            rate = processed_count / elapsed if elapsed > 0 else 0
            print(f"Progress: [{processed_count}/{total_pending}] | Rate: {rate:.1f} it/s | "
                  f"Success: {success_count} | Timeouts: {timeout_count} | Errors: {error_count}    ", end='\r')
            
        time.sleep(0.05) 

    dest_session.commit()
    print("\n=== Pipeline Complete ===")
    print(f"Total Processed: {processed_count}")
    print(f"Successful Topologies Found: {success_count}")
    print(f"Flagged (Timeout Force-Killed): {timeout_count}")
    print(f"Flagged (Pipeline/Segfault Error): {error_count}")


def retry_timeouts(new_time_limit=20, new_external_timeout=25):
    """
    Makes a second pass at topologies that previously timed out (status == 2).
    Applies a longer time limit to give complex constraints more time to resolve.
    """
    print(f"\n=== Retrying Timeouts (N={N}, Symmetry={SYMMETRY}) ===")
    print(f"New Time Limit: {new_time_limit}s (External: {new_external_timeout}s)")
    
    dest_engine = create_engine(DEST_DB_URI)
    DestSession = sessionmaker(bind=dest_engine)
    dest_session = DestSession()

    timeout_topologies = dest_session.query(Topology.id, Topology.binary_state)\
                                     .filter(Topology.status == 2).all()
    
    total_pending = len(timeout_topologies)
    if total_pending == 0:
        print("No timed-out topologies found. Nothing to retry!")
        return

    print(f"Found {total_pending} topologies to retry.")
    
    num_workers = max(1, mp.cpu_count() - 2)
    print(f"Spinning up {num_workers} parallel workers...")
    
    t0 = time.time()
    processed_count = 0
    success_count = 0
    still_timeout_count = 0
    error_count = 0

    manager = mp.Manager()
    return_dict = manager.dict()
    
    active_processes = {}
    pending_iter = iter(timeout_topologies)
    
    while True:
        while len(active_processes) < num_workers:
            try:
                topo = next(pending_iter)
                p = mp.Process(target=worker_wrapper, args=(
                    topo.id, topo.binary_state, N, SYMMETRY, new_time_limit, return_dict
                ))
                p.start()
                active_processes[topo.id] = (p, time.time())
            except StopIteration:
                break 

        if not active_processes:
            break

        current_time = time.time()
        for topo_id in list(active_processes.keys()):
            if topo_id not in active_processes:
                continue
                
            p, start_time = active_processes[topo_id]

            if not p.is_alive():
                p.join()
                active_processes.pop(topo_id, None)
                processed_count += 1
                
                res = return_dict.pop(topo_id, None)
                
                if res is not None:
                    res_topo_id, status, tilings_data = res
                    dest_session.query(Topology).filter(Topology.id == res_topo_id).update({"status": status})
                    
                    if status == 1:
                        tiling_inserts = []
                        seen_hashes = set()
                        for t_data in tilings_data:
                            h = t_data["tiling_hash"]
                            if h not in seen_hashes:
                                seen_hashes.add(h)
                                tiling_inserts.append({
                                    "topology_id": res_topo_id,
                                    "hashed_tiling": h,
                                    "tiling_blob": t_data["blob_bytes"],
                                    "embedding": t_data["embedding_bytes"]
                                })
                        
                        if tiling_inserts:
                            try:
                                stmt = insert(Tiling).values(tiling_inserts).on_conflict_do_nothing(index_elements=['hashed_tiling'])
                                dest_session.execute(stmt)
                                success_count += 1
                            except Exception as e:
                                print(f"\nDB Error on Topo {res_topo_id}: {e}")
                                dest_session.query(Topology).filter(Topology.id == res_topo_id).update({"status": 3})
                                error_count += 1
                                
                    elif status == 2:
                        still_timeout_count += 1 
                    elif status == 3:
                        error_count += 1
                else:
                    dest_session.query(Topology).filter(Topology.id == topo_id).update({"status": 3})
                    error_count += 1

            elif current_time - start_time > new_external_timeout:
                p.terminate()
                p.join()
                active_processes.pop(topo_id, None)
                processed_count += 1
                still_timeout_count += 1 

        if processed_count % 5 == 0 or len(active_processes) == 0:
            dest_session.commit()
            elapsed = time.time() - t0
            rate = processed_count / elapsed if elapsed > 0 else 0
            print(f"Progress: [{processed_count}/{total_pending}] | Rate: {rate:.1f} it/s | "
                  f"Recovered: {success_count} | Still Timeout: {still_timeout_count} | Errors: {error_count}    ", end='\r')
            
        time.sleep(0.1) 

    dest_session.commit()
    print("\n=== Retry Pipeline Complete ===")
    print(f"Topologies Recovered (Success): {success_count}")
    print(f"Topologies Still Timing Out: {still_timeout_count}")
    print(f"Topologies Failed (CP/Pipeline Error): {error_count}")
if __name__ == "__main__":
    mp.freeze_support()
    with keep.running():
        main()
        retry_timeouts(new_time_limit=120, new_external_timeout=125)

"""


"""