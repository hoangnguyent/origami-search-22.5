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

from wakepy import keep
from sqlalchemy import create_engine, Column, Integer, LargeBinary, ForeignKey, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.dialects.sqlite import insert

# Pipeline Imports
from src.engine.topology2tiling import solve_tiling, export_frozen_blob
from src.engine.tiling2cp import load_frozen_blob, build_crease_pattern, add_hinges
from src.engine.fold225 import cp_to_fold
from src.engine.tree import extract_eigenvalues

# =============================================================================
# CONFIGURATION
# =============================================================================
N = 4
SYMMETRY = "diag"
TIME_LIMIT = 10 # Internal Python DFS time limit
EXTERNAL_TIMEOUT = 12 # External backup timeout (slightly higher to allow graceful internal exit)

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
def process_topology_task(topo_id, binary_state, n_val, symmetry_val, time_lim):
    """
    Isolated worker task. Reconstructs graph, solves tiling, builds CP, and extracts tree.
    Returns: (topo_id, status_flag, pickled_blob, embedding_bytes)
    """
    try:
        # 1. Decompress & Build Graph
        edges = decompress_edges(binary_state, n_val)
        G_raw = nx.Graph()
        G_raw.add_edges_from(edges)
        pos = {node: node for node in G_raw.nodes()}
        nx.set_node_attributes(G_raw, pos, 'pos')
        
        # 2. Solve Tiling
        output = solve_tiling(G_raw, symmetry=symmetry_val, N=n_val, verbose=False, time_limit=time_lim)
        if output is None:
            return (topo_id, 2, None, None) # STATUS 2: Timeout
            
        G_solved, pos_init, pos_solved_exact, faces, n2i = output
        blob_dict = export_frozen_blob(G_solved, pos_solved_exact, n2i, faces)
        
        # 3. Post-Processing Pipeline (CP -> Fold -> Tree -> Eigenvalues)
        loaded_G, loaded_pos, loaded_faces = load_frozen_blob(blob_dict)
        cp = build_crease_pattern(loaded_G, loaded_pos, loaded_faces, N=n_val)
        cp = add_hinges(cp)
        
        fold = cp_to_fold(cp)
        tree = fold.get_tree_and_packing()[0]
        embedding = extract_eigenvalues(tree, dim=32)
        
        # 4. Serialize for DB (Pickle is hyper-efficient for exact math objects / dicts)
        blob_bytes = pickle.dumps(blob_dict, protocol=pickle.HIGHEST_PROTOCOL)
        embedding_bytes = np.array(embedding, dtype=np.float32).tobytes()
        
        return (topo_id, 1, blob_bytes, embedding_bytes) # STATUS 1: Success
        
    except Exception as e:
        # Catch degenerate float errors, kawasaki errors, or exact-fraction crashes
        return (topo_id, 3, None, None) # STATUS 3: CP/Pipeline Error

# =============================================================================
# MAIN ORCHESTRATOR
# =============================================================================
def main():
    print(f"=== Tiling Pipeline Initializing (N={N}, Symmetry={SYMMETRY}) ===")
    
    # 1. Setup Source DB
    src_engine = create_engine(SOURCE_DB_URI)
    SrcSession = sessionmaker(bind=src_engine)
    src_session = SrcSession()
    
    # 2. Setup Dest DB
    dest_engine = create_engine(DEST_DB_URI)
    DestBase.metadata.create_all(dest_engine)
    DestSession = sessionmaker(bind=dest_engine)
    dest_session = DestSession()
    
    # 3. Sync Databases: Copy all topologies over that don't exist yet
    print("Syncing topologies from source database...")
    all_source_states = src_session.query(SourceState.id, SourceState.binary_state).yield_per(1000)
    
    # We use SQLite's INSERT OR IGNORE to massively speed up syncing
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

    # 4. Fetch Pending Tasks
    pending_topologies = dest_session.query(Topology.id, Topology.binary_state).filter(Topology.status == 0).all()
    total_pending = len(pending_topologies)
    
    if total_pending == 0:
        print("Database is already complete! No pending topologies found.")
        return

    print(f"Resuming operation: {total_pending} topologies left to process.")
    
    # 5. Multiprocessing Execution
    num_workers = max(1, mp.cpu_count() - 2)
    print(f"Spinning up {num_workers} parallel workers...")
    
    t0 = time.time()
    processed_count = 0
    success_count = 0
    timeout_count = 0
    error_count = 0

    with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as executor:
        # Dictionary mapping Future -> (topo_id, start_time)
        active_futures = {}
        pending_iter = iter(pending_topologies)
        
        # Initial fill
        for _ in range(num_workers):
            try:
                topo = next(pending_iter)
                fut = executor.submit(process_topology_task, topo.id, topo.binary_state, N, SYMMETRY, TIME_LIMIT)
                active_futures[fut] = (topo.id, time.time())
            except StopIteration:
                break
                
        while active_futures:
            # Wait for at least one task to complete (or 1 second to check for external timeouts)
            done, not_done = concurrent.futures.wait(
                active_futures.keys(), 
                timeout=1.0, 
                return_when=concurrent.futures.FIRST_COMPLETED
            )
            
            # --- 1. Handle Completed Tasks ---
            for fut in done:
                topo_id, _ = active_futures.pop(fut)
                try:
                    res_topo_id, status, blob_bytes, embedding_bytes = fut.result()
                    
                    # Update DB
                    dest_session.query(Topology).filter(Topology.id == res_topo_id).update({"status": status})
                    if status == 1:
                        dest_session.add(Tiling(
                            topology_id=res_topo_id,
                            tiling_blob=blob_bytes,
                            embedding=embedding_bytes
                        ))
                        success_count += 1
                    elif status == 2:
                        timeout_count += 1
                    elif status == 3:
                        error_count += 1
                        
                except Exception as e:
                    print(f"Critical Worker Crash on ID {topo_id}: {e}")
                    dest_session.query(Topology).filter(Topology.id == topo_id).update({"status": 3})
                    error_count += 1
                
                processed_count += 1

            # --- 2. Enforce External Timeout ---
            current_time = time.time()
            for fut in list(not_done):
                topo_id, start_time = active_futures[fut]
                if current_time - start_time > EXTERNAL_TIMEOUT:
                    # The task is hung in C++ or stuck. We abandon the future and flag the DB.
                    # Note: ProcessPoolExecutor doesn't natively "kill" the process, but abandoning 
                    # it allows the pipeline to continue moving forward.
                    active_futures.pop(fut)
                    dest_session.query(Topology).filter(Topology.id == topo_id).update({"status": 2})
                    timeout_count += 1
                    processed_count += 1
                    # print(f"External Timeout Triggered for ID {topo_id}")

            # --- 3. Refill the Queue ---
            while len(active_futures) < num_workers:
                try:
                    topo = next(pending_iter)
                    fut = executor.submit(process_topology_task, topo.id, topo.binary_state, N, SYMMETRY, TIME_LIMIT)
                    active_futures[fut] = (topo.id, time.time())
                except StopIteration:
                    break
                    
            # Periodically commit to DB and print progress
            if processed_count % 20 == 0 or len(active_futures) == 0:
                dest_session.commit()
                elapsed = time.time() - t0
                rate = processed_count / elapsed if elapsed > 0 else 0
                print(f"Progress: [{processed_count}/{total_pending}] | Rate: {rate:.1f} it/s | "
                      f"Success: {success_count} | Timeouts: {timeout_count} | Errors: {error_count}")

    # Final commit and cleanup
    dest_session.commit()
    print("=== Pipeline Complete ===")
    print(f"Total Processed: {processed_count}")
    print(f"Successful Tilings Added: {success_count}")
    print(f"Flagged (Timeout): {timeout_count}")
    print(f"Flagged (Pipeline Error): {error_count}")

if __name__ == "__main__":
    mp.freeze_support()
    with keep.running():
        main()