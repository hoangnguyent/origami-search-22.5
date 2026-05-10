import pickle
import time
import multiprocessing
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Adjust these imports to match your project architecture
from database.tilings.build_tilings import Tiling, clean_tree_for_storage 
from src.engine.tiling2cp import load_frozen_blob, build_crease_pattern, add_hinges
from src.engine.cp225 import canonicalize, unfreeze
from src.engine.fold225 import cp_to_fold

def worker_task(payload):
    """
    Isolated worker function. Takes a raw blob, reconstructs the tree, 
    cleans the metadata, and serializes the raw NetworkX graph.
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
        return (t_id, None, str(e))

def migrate_database_trees(N, symmetry):
    db_path = f'database/tilings/storage/tilings_{N}_{symmetry}.db'
    db_uri = f'sqlite:///{db_path}'
    
    print(f"Connecting to DB: N={N}, Sym={symmetry}...")
    engine = create_engine(db_uri)
    Session = sessionmaker(bind=engine)
    session = Session()

    total_rows = session.query(Tiling.id).count()
    if total_rows == 0:
        print("Database is empty. Skipping.")
        session.close()
        return

    # Generator to yield payloads without loading the entire DB into RAM
    def payload_generator():
        for tiling in session.query(Tiling.id, Tiling.tiling_blob).yield_per(2000):
            yield (tiling.id, tiling.tiling_blob, N)

    print(f"Found {total_rows} tilings. Starting Multiprocessing Pool to cache raw trees...")
    start_time = time.time()
    
    # Use CPU count - 1 to leave a core free for the OS / SQLite thread
    num_cores = max(1, multiprocessing.cpu_count() - 1)
    processed = 0
    errors = 0
    batch_updates = []
    
    with multiprocessing.Pool(processes=num_cores) as pool:
        # imap_unordered yields results as soon as they finish, regardless of order
        for t_id, tree_bytes, err in pool.imap_unordered(worker_task, payload_generator(), chunksize=100):
            processed += 1
            
            if err:
                errors += 1
                print(f"Error on ID {t_id}: {err}")
                continue
                
            # Queue the update dictionary (Storing the pickled tree in the embedding column)
            batch_updates.append({'id': t_id, 'embedding': tree_bytes})
            
            # Bulk commit to SQLite every 500 rows to prevent write-locking bottlenecks
            if len(batch_updates) >= 500:
                session.bulk_update_mappings(Tiling, batch_updates)
                session.commit()
                
                elapsed = time.time() - start_time
                rate = processed / elapsed
                print(f"Processed {processed}/{total_rows} ({(processed/total_rows)*100:.1f}%) | {rate:.2f} iters/sec")
                batch_updates = []

    # Commit any remaining rows
    if batch_updates:
        session.bulk_update_mappings(Tiling, batch_updates)
        session.commit()
        
    session.close()
    
    print("-" * 50)
    print(f"MP TREE MIGRATION COMPLETE FOR N={N}, Sym={symmetry}")
    print(f"Successfully updated: {processed - errors}")
    print(f"Errors encountered:   {errors}")
    print(f"Total Time:           {(time.time() - start_time):.2f} seconds")
    print("-" * 50)


if __name__ == "__main__":
    # Example usage:
    # migrate_database_trees(N=3, symmetry='diag')
    # migrate_database_trees(N=3, symmetry='none')
    migrate_database_trees(N=3, symmetry='book')
    migrate_database_trees(N=4, symmetry='diag')
    migrate_database_trees(N=4, symmetry='none')
    migrate_database_trees(N=4, symmetry='book')
    migrate_database_trees(N=5, symmetry='diag')