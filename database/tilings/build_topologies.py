import multiprocessing as mp
import time
import cProfile
import pstats
import math
import itertools
import mmh3
from wakepy import keep

from sqlalchemy import create_engine, Column, Integer, LargeBinary, Boolean, String, text, BigInteger, func
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.dialects.sqlite import insert

import networkx as nx
from z3.z3 import Solver, Bool, And, Or, Not, If, Implies, BoolVal, sat, is_true

from src.engine.topology225 import apply_transform, is_valid_tiling_global, plot_multiple_graphs, get_canonical_hash

Base = declarative_base()

# =============================================================================
# DATABASE MODELS
# =============================================================================

class Prefix(Base):
    __tablename__ = 'prefixes'
    id = Column(Integer, primary_key=True)
    bits = Column(String, nullable=False, unique=True) # e.g. '10110'
    is_done = Column(Boolean, default=False)

class State(Base):
    __tablename__ = 'states'
    id = Column(Integer, primary_key=True)
    prefix_id = Column(Integer) 
    binary_state = Column(LargeBinary, nullable=False) 
    hashed_state = Column(BigInteger, nullable=False, unique=True, index=True)
# =============================================================================
# BINARY COMPRESSION & EDGE UTILS
# =============================================================================

def get_ordered_internal_edges(N):
    """Returns a strictly sorted list of all possible internal edges."""
    edges = []
    # Orthogonal
    for i in range(N):
        for j in range(1, N):
            edges.append(tuple(sorted(((i, j), (i+1, j)))))
            edges.append(tuple(sorted(((j, i), (j, i+1)))))
    # Diagonals
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

def compress_edges(active_internal_edges, ordered_edges):
    """Compresses a list of active edges into a raw byte string."""
    active_set = set(active_internal_edges)
    bit_string = "".join("1" if e in active_set else "0" for e in ordered_edges)
    # Convert bit string to bytes
    num_bytes = (len(ordered_edges) + 7) // 8
    return int(bit_string, 2).to_bytes(num_bytes, byteorder='big')

def decompress_edges(binary_blob, N):
    """Reconstructs the full edge list (including boundaries) from a byte string."""
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


def extract_topology(state_id,db_name=None, N = 4):
    if db_name is None:
        db_name = f'topologies_{N}_diag.db' # Default to diagonal symmetry for extraction
    engine = create_engine(f'sqlite:///database/tilings/storage/{db_name}')
    Session = sessionmaker(bind=engine)
    session = Session()
    
    state = session.query(State).filter_by(id=state_id).first()
    if not state:
        print(f"No state found with ID {state_id}")
        return None
    
    edges = decompress_edges(state.binary_state, N=N)

    G = nx.Graph()
    G.add_edges_from(edges)
    
    # 3. FIX: Manually re-assign the 'pos' attribute to every node
    # This tells the plotter that node (x, y) is located at (x, y)
    pos = {node: node for node in G.nodes()}
    nx.set_node_attributes(G, pos, 'pos')
    return G

# =============================================================================
# MULTIPROCESSING: Z3 WORKER
# =============================================================================

def _prefix_worker(args):
    """
    Isolated Z3 worker that searches for valid prefixes starting with a specific bit seed.
    """
    N, symmetry, prefix_length, seed_bits = args
    
    ordered_edges = get_ordered_internal_edges(N)
    boundary_edges = get_boundary_edges(N)

    s = Solver()
    edge_vars = {e: Bool(f"e_{i}") for i, e in enumerate(ordered_edges)}

    # Apply base geometric rules
    for x in range(N):
        for y in range(N):
            d1 = tuple(sorted(((x, y), (x+1, y+1))))
            d2 = tuple(sorted(((x+1, y), (x, y+1))))
            s.add(Not(And(edge_vars[d1], edge_vars[d2])))

    incident_vars = { (x,y): [] for x in range(N+1) for y in range(N+1) }
    for u, v in boundary_edges:
        incident_vars[u].append(1)
        incident_vars[v].append(1)
    for e, var in edge_vars.items():
        val = If(var, 1, 0)
        incident_vars[e[0]].append(val)
        incident_vars[e[1]].append(val)
    for node, vars_list in incident_vars.items():
        s.add(sum(vars_list) != 1)

    if symmetry != 'none':
        t_type = 6 if symmetry == 'diag' else 4
        for e, var in edge_vars.items():
            re = tuple(sorted((apply_transform(e[0], N, t_type), apply_transform(e[1], N, t_type))))
            if re in edge_vars:
                s.add(var == edge_vars[re])

    # HARDCODE THE SEED BITS to isolate this worker's search space
    for i, bit in enumerate(seed_bits):
        s.add(edge_vars[ordered_edges[i]] == (bit == '1'))

    prefix_vars = [edge_vars[ordered_edges[i]] for i in range(prefix_length)]
    valid_prefixes = []
    
    while s.check() == sat:
        m = s.model()
        bits = "".join('1' if is_true(m.evaluate(v, model_completion=True)) else '0' for v in prefix_vars)
        valid_prefixes.append(bits)

        # Block this specific prefix from being found again
        block = []
        for v in prefix_vars:
            is_active = is_true(m.evaluate(v, model_completion=True))
            block.append(v != is_active)
        s.add(Or(*block))

    return valid_prefixes

def generate_viable_prefixes(N, symmetry, prefix_length):
    """
    Uses multiprocessing to aggressively pre-prune the search tree.
    Divides the search space by seeding the first K bits and dispatching to cores.
    """
    num_cores = max(1, mp.cpu_count() - 1)
    
    # Determine how many initial bits to seed to keep all cores busy.
    # We bump the multiplier from 4 to 16 for deeper prefix lengths to ensure no core sits idle.
    target_chunks = num_cores * 16 
    seed_depth = min(prefix_length - 2, max(2, int(math.ceil(math.log2(target_chunks)))))
    
    seeds = ["".join(seq) for seq in itertools.product("01", repeat=seed_depth)]
    print(f"\nAsking Z3 to dynamically pre-prune viable {prefix_length}-bit prefixes...")
    print(f"Divided search space into {len(seeds)} parallel chunks (Seed depth: {seed_depth})...")
    
    args_list = [(N, symmetry, prefix_length, seed) for seed in seeds]
    valid_prefixes = []
    completed = 0
    t0 = time.time()
    
    # Use imap_unordered for instant progress updates as chunks finish
    with mp.Pool(processes=num_cores) as pool:
        for chunk_res in pool.imap_unordered(_prefix_worker, args_list):
            valid_prefixes.extend(chunk_res)
            completed += 1
            elapsed = time.time() - t0
            rate = completed / elapsed if elapsed > 0 else 0
            
            # Print live progress on the same line
            print(f"Prefix Progress: [{completed}/{len(seeds)}] chunks | Viable found: {len(valid_prefixes)} | Rate: {rate:.1f} chunks/s", end='\r')
            
    print(f"\nZ3 pruning complete! Shrunk search tree from {2**prefix_length} down to {len(valid_prefixes)} viable prefixes in {time.time()-t0:.1f}s.\n")
    return valid_prefixes
def z3_worker(task_queue, result_queue, N, symmetry):
    ordered_edges = get_ordered_internal_edges(N)
    boundary_edges = get_boundary_edges(N)
    
    while True:
        task = task_queue.get()
        if task is None: 
            break 
        
        prefix_id, prefix_bits = task
        
        s = Solver()
        edge_vars = {e: Bool(f"e_{i}") for i, e in enumerate(ordered_edges)}
        
        # APPLY THE PREFIX
        for i, bit in enumerate(prefix_bits):
            s.add(edge_vars[ordered_edges[i]] == (bit == '1'))

        # CORE Z3 CONSTRAINTS 
        cells = {}
        for x in range(N):
            for y in range(N):
                d1 = tuple(sorted(((x, y), (x+1, y+1))))
                d2 = tuple(sorted(((x+1, y), (x, y+1))))
                s.add(Not(And(edge_vars[d1], edge_vars[d2])))

        incident_vars = { (x,y): [] for x in range(N+1) for y in range(N+1) }
        for u, v in boundary_edges:
            incident_vars[u].append(1)
            incident_vars[v].append(1)
        for e, var in edge_vars.items():
            val = If(var, 1, 0)
            incident_vars[e[0]].append(val)
            incident_vars[e[1]].append(val)
        for node, vars_list in incident_vars.items():
            s.add(sum(vars_list) != 1)

        if symmetry != 'none':
            t_type = 6 if symmetry == 'diag' else 4
            for e, var in edge_vars.items():
                re = tuple(sorted((apply_transform(e[0], N, t_type), apply_transform(e[1], N, t_type))))
                if re in edge_vars:
                    s.add(var == edge_vars[re])

        for x in range(1, N):
            for y in range(1, N):
                neighbors = [
                    (x+1, y), (x+1, y+1), (x, y+1), (x-1, y+1),
                    (x-1, y), (x-1, y-1), (x, y-1), (x+1, y-1)
                ]
                e_vars = [edge_vars[tuple(sorted(((x,y), nbr)))] for nbr in neighbors]
                pairs = [(e_vars[0], e_vars[4]), (e_vars[2], e_vars[6]), 
                         (e_vars[1], e_vars[5]), (e_vars[3], e_vars[7])]
                any_straight = Or(*[And(p[0], p[1]) for p in pairs])
                all_paired = And([p[0] == p[1] for p in pairs])
                s.add(Implies(any_straight, all_paired))

        # 4. SOLVE AND COMPRESS (WITH TIME-BASED FLUSHING)
        found_states = []
        models_checked = 0
        BATCH_SIZE = 50 
        
        last_flush_time = time.time()
        last_print_time = time.time()
        
        while s.check() == sat:
            model = s.model()
            models_checked += 1
            
            current_active = []
            block_clause = []
            
            for e, v in edge_vars.items():
                is_active = is_true(model.evaluate(v, model_completion=True))
                if is_active:
                    current_active.append(e)
                block_clause.append(v != is_active)
            
            s.add(Or(*block_clause))
            
            # Global Filter
            if is_valid_tiling_global(current_active + boundary_edges):
                canonical_edges = get_canonical_hash(current_active + boundary_edges, N)
                internal_canonical = [e for e in canonical_edges if e not in boundary_edges]
                
                binary_blob = compress_edges(internal_canonical, ordered_edges)
                hash_val = mmh3.hash64(binary_blob, signed=True)[0]
                
                found_states.append({
                    "binary": binary_blob, 
                    "hash": hash_val
                })
                
            # --- Heartbeat Print & Time-Based DB Flush ---
            current_time = time.time()
            
            # Print a heartbeat every 10 seconds so we know Z3 isn't stuck
            if current_time - last_print_time > 10:
                print(f"[Worker] Prefix ID {prefix_id} active | {models_checked} raw Z3 models evaluated so far...")
                last_print_time = current_time
                
            # Flush if we hit 50 items, OR if 15 seconds have passed and we have at least 1 item
            if len(found_states) >= BATCH_SIZE or (len(found_states) > 0 and (current_time - last_flush_time > 15)):
                result_queue.put((prefix_id, found_states, False)) # False = prefix not done yet
                found_states = []
                last_flush_time = current_time
                
        # Send any remaining states and signal that this prefix is COMPLETE
        result_queue.put((prefix_id, found_states, True))
# =============================================================================
# MULTIPROCESSING: DATABASE WRITER
# =============================================================================

def db_writer(db_uri, result_queue, total_prefixes):
    engine = create_engine(db_uri)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    processed = 0
    t_start = time.time()
    
    while True:
        res = result_queue.get()
        if res is None: break # Poison pill
        
        prefix_id, states, _ = res
        
        # 1. Bulk save NEWLY discovered topologies incrementally
        if states:
            # Reformat the dictionaries into SQLAlchemy insert values
            insert_values = [
                {
                    "prefix_id": prefix_id, 
                    "binary_state": s["binary"], 
                    "hashed_state": s["hash"]
                } 
                for s in states
            ]
            
            # INSERT OR IGNORE based strictly on the 64-bit Hash
            stmt = insert(State).values(insert_values)
            stmt = stmt.on_conflict_do_nothing(index_elements=['hashed_state'])
            session.execute(stmt)
            session.commit() # Safely committed to disk immediately!
            
        # 2. Mark Prefix as done ONLY when the worker signals it has exhausted the prefix
        # if is_complete:
        #     session.query(Prefix).filter_by(id=prefix_id).update({"is_done": True})
        #     session.commit()
            
        #     processed += 1
        #     elapsed = time.time() - t_start
        #     print(f">>> [DB Writer] Progress: [{processed}/{total_prefixes}] Prefixes Exhausted | Time: {elapsed:.1f}s")
# =============================================================================
# MAIN ORCHESTRATOR
# =============================================================================

if __name__ == "__main__":

    mp.freeze_support() # Safe execution on Windows
    

    # --- CONFIGURATION ---
    N = 5
    symmetry = "book"
    prefix_length = 24

    print(f"Configuration: N={N}, Symmetry={symmetry}")
    # ---------------------
    
    db_uri = f'sqlite:///database/tilings/storage/topologies_{N}_{symmetry}.db'
    engine = create_engine(db_uri)
    Base.metadata.create_all(engine)
    
    Session = sessionmaker(bind=engine)
    session = Session()
    session.execute(text("PRAGMA journal_mode=WAL;"))
    session.execute(text("PRAGMA synchronous=NORMAL;")) 
    
    with keep.running():
        print("===== Start Database Build =====")
        
        # 1. Initialize the Prefixes via Z3 Pre-Pruning (if starting fresh)
        if session.query(Prefix).count() == 0:
            print(f"Prefix Length: {prefix_length} bits")
            valid_bits = generate_viable_prefixes(N, symmetry, prefix_length)
            prefix_objects = [Prefix(bits=b) for b in valid_bits]
            session.bulk_save_objects(prefix_objects)
            session.commit()
            
        # pending_prefixes = session.query(Prefix).filter_by(is_done=False).all()
        # total_pending = len(pending_prefixes)
        pending_prefixes = session.query(Prefix).filter_by(is_done=False).order_by(func.random()).all()
        total_pending = len(pending_prefixes)
        
        if total_pending == 0:
            print("Database is already complete!")
            exit()
            
        print(f"Resuming operation: {total_pending} prefixes left to process.")
        
        # 2. Setup Queues and Processes
        task_queue = mp.Queue()
        result_queue = mp.Queue()
        
        num_cores = max(1, mp.cpu_count() - 1) 
        print(f"Spinning up {num_cores} Z3 Worker processes...")
        
        writer_proc = mp.Process(target=db_writer, args=(db_uri, result_queue, total_pending))
        writer_proc.start()
        
        worker_procs = []
        for _ in range(num_cores):
            p = mp.Process(target=z3_worker, args=(task_queue, result_queue, N, symmetry))
            p.start()
            worker_procs.append(p)
            
        # 3. Feed the tasks
        for prefix in pending_prefixes:
            prefix.is_done = True
            session.commit()
            task_queue.put((prefix.id, prefix.bits))
            
        # Send poison pills to workers
        for _ in range(num_cores):
            task_queue.put(None)
            
        # Wait for workers to finish
        for p in worker_procs:
            p.join()
            
        # Send poison pill to writer
        result_queue.put(None)
        writer_proc.join()
        
        final_count = session.query(State).count()
        print("===== End Database Build =====")
        print(f"Total Unique Topologies Generated: {final_count}")

"""
Back of the envelope scaling calculations
Assume the number of states scales with a base to the power of (N^2) with no symmetry, or (N^2)/2 with symmetry, and divide by 8. To find the exponent base, use the fact that N=4 with diagonal symmetry is ~100,000 states. therefore the base is around 5.5

N = 3: 268 with sym, 5.7e5 without
N = 4: 1e5 with sym, 8.76e10 without
N = 5: 2.2e8 with sym, 4e17 without

It takes roughly 0.4s and 0.04kb per state.
For ever 1e5, that's around 10 hours and 3-4MB.


Last time ran 4 none: prefix ids 1, 20-23, 26-39 ran up til around 80k raw z3 each with no signs of nearing completion. this generated around 27k states in total. If 4 none resumes, mark these prefixes as done and move on to other prefixes.
Can try to generate tilings from these 27k I guess


Goal: get the largest complete db possible for each of the symmetry types
None: use N = 3 (complete) and some prefixes from N = 4. Also query from book and diag. or use the book and diag topologies but asymmetric tilings
Diag: use N = 4  
Book: use N = 4 

"""