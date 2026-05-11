import pickle
import time
import multiprocessing
import networkx as nx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Adjust these imports to match your project architecture
from database.tilings.build_tilings import Tiling, clean_tree_for_storage 
from src.engine.tiling2cp import load_frozen_blob, build_crease_pattern, add_hinges
from src.engine.cp225 import canonicalize, unfreeze
from src.engine.fold225 import cp_to_fold
from wakepy import keep

def is_valid_tree(embedding_bytes):
    """
    Quickly checks if the binary blob is already a perfectly formatted NetworkX tree.
    Catches legacy numpy arrays, NULLs, and corrupted pickling.
    """
    if not embedding_bytes:
        return False
    try:
        obj = pickle.loads(embedding_bytes)
        # Duck-typing check to ensure it's a graph, not a numpy array
        if isinstance(obj, nx.Graph):
            return True
        return False
    except Exception:
        # Catches UnpicklingError, TypeError, EOFError, etc.
        return False

def worker_task(payload):
    """
    Isolated worker function. Rebuilds the tree only for malformed rows.
    If it fails, it returns a flag signaling the row is unrecoverable.
    """
    t_id, blob_bytes, N = payload
    try:
        blob = pickle.loads(blob_bytes)
        
        loaded_G, loaded_pos, loaded_faces = load_frozen_blob(blob)
        
        cp = build_crease_pattern(loaded_G, loaded_pos, loaded_faces, N=N)
        cp = add_hinges(cp)
        cp_frozen = canonicalize(cp)
        cp = unfreeze(cp_frozen)
        
        fold = cp_to_fold(cp)
        res_tree = fold.get_tree_and_packing()[0]
        
        clean_tree = clean_tree_for_storage(res_tree)
        
        # Serialize the raw, sanitized NetworkX tree
        tree_bytes = pickle.dumps(clean_tree, protocol=pickle.HIGHEST_PROTOCOL)
        
        return (t_id, tree_bytes, None)
        
    except Exception as e:
        # If it hits this block, the tiling geometry itself is completely broken
        return (t_id, None, str(e))

def smart_migrate_and_clean(N, symmetry):
    db_path = f'database/tilings/storage/tilings_{N}_{symmetry}.db'
    db_uri = f'sqlite:///{db_path}'
    
    print(f"\n[{N}_{symmetry}] Connecting to DB...")
    engine = create_engine(db_uri)
    Session = sessionmaker(bind=engine)
    session = Session()

    total_rows = session.query(Tiling.id).count()
    if total_rows == 0:
        print("Database is empty. Skipping.")
        session.close()
        return

    print(f"[{N}_{symmetry}] Auditing {total_rows} tilings for malformed data...")

    # Generator to filter out healthy rows instantly
    def payload_generator():
        for tiling in session.query(Tiling.id, Tiling.tiling_blob, Tiling.embedding).yield_per(2000):
            if not is_valid_tree(tiling.embedding):
                yield (tiling.id, tiling.tiling_blob, N)

    start_time = time.time()
    num_cores = max(1, multiprocessing.cpu_count() - 1)
    
    processed = 0
    recovered = 0
    batch_updates = []
    ids_to_delete = []
    error_log_path = "migration_errors.txt"

    with multiprocessing.Pool(processes=num_cores) as pool:
        with open(error_log_path, "a") as err_file:
            # Note: This will only iterate over the MALFORMED rows yielded by the generator
            for t_id, tree_bytes, err in pool.imap_unordered(worker_task, payload_generator(), chunksize=100):
                processed += 1
                
                if err:
                    # Unrecoverable error! Mark for deletion.
                    ids_to_delete.append(t_id)
                    error_msg = f"ID: {t_id}, N: {N}, Sym: {symmetry} | FATAL: {err}\n"
                    err_file.write(error_msg)
                    err_file.flush()
                    continue
                    
                # Successfully recovered! Queue for update.
                recovered += 1
                batch_updates.append({'id': t_id, 'embedding': tree_bytes})
                
                # Bulk commit every 500 rows
                if len(batch_updates) >= 500:
                    session.bulk_update_mappings(Tiling, batch_updates)
                    session.commit()
                    batch_updates = []
                    print(f"  Repaired {recovered} malformed tilings so far...")

    # Commit any remaining recovered rows
    if batch_updates:
        session.bulk_update_mappings(Tiling, batch_updates)
        session.commit()
        
    # --- THE PURGE ---
    deleted_count = len(ids_to_delete)
    if deleted_count > 0:
        print(f"\n[{N}_{symmetry}] Purging {deleted_count} unrecoverable tilings...")
        # Delete them safely in one massive query
        session.query(Tiling).filter(Tiling.id.in_(ids_to_delete)).delete(synchronize_session=False)
        session.commit()

    session.close()
    
    healthy_count = total_rows - processed
    
    print("-" * 50)
    print(f"SMART MIGRATION COMPLETE FOR N={N}, Sym={symmetry}")
    print(f"Already Healthy (Skipped): {healthy_count}")
    print(f"Malformed & Recovered:   {recovered}")
    print(f"Malformed & DELETED:     {deleted_count}")
    print(f"Total Time:              {(time.time() - start_time):.2f} seconds")
    print("-" * 50)


if __name__ == "__main__":
    with keep.running():
        # smart_migrate_and_clean(N=3, symmetry='diag')
        # smart_migrate_and_clean(N=3, symmetry='none')
        # smart_migrate_and_clean(N=3, symmetry='book')
        # smart_migrate_and_clean(N=4, symmetry='diag')
        smart_migrate_and_clean(N=4, symmetry='none')
        smart_migrate_and_clean(N=4, symmetry='book')
        smart_migrate_and_clean(N=5, symmetry='diag')