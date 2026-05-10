"""
Delete specific tilings from the databases (remove corrupted/degenerate edge cases that slipped through)

"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database.tilings.build_tilings import Tiling 

def remove_tiling(tiling_id, N, symmetry):
    db_path = f'database/tilings/storage/tilings_{N}_{symmetry}.db'
    db_uri = f'sqlite:///{db_path}'
    
    if not os.path.exists(db_path):
        print(f"Error: Database file '{db_path}' does not exist.")
        return

    print(f"Connecting to DB: N={N}, Sym={symmetry}...")
    engine = create_engine(db_uri)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Fetch the target tiling
        tiling = session.query(Tiling).filter_by(id=tiling_id).first()
        
        if tiling:
            topo_id = tiling.topology_id
            
            # Delete and commit
            session.delete(tiling)
            session.commit()
            
            print(f"SUCCESS: Tiling ID {tiling_id} (Child of Topology ID {topo_id}) has been permanently deleted.")
            print("-" * 60)
            print("CRITICAL REMINDER: Your FAISS index and SQLite database are now out of sync!")
            print(f"Please re-run `faiss_cache.py` for N={N}, Sym={symmetry} to rebuild the index.")
            print("-" * 60)
        else:
            print(f"Notice: Tiling ID {tiling_id} was not found in the database. No action taken.")
            
    except Exception as e:
        session.rollback()
        print(f"An error occurred during deletion: {e}")
    finally:
        session.close()

if __name__ == "__main__":

    pass
    # targets = [
    #     (28461, 3, 'none'),
    #     (31541, 3, 'none'),
    #     (31543, 3, 'none'),
    #     (49105, 3, 'none'),
    #     (49118, 3, 'none'),

    #     (42964, 4, 'diag'),
    #     (54891, 4, 'diag'),
    #     (65390, 4, 'diag'),
    #     (80214, 4, 'diag'),
    #     (88627, 4, 'diag'),
    #     (93123, 4, 'diag'),
    #     (107259, 4, 'diag'),
    #     (114341, 4, 'diag'),
    #     (139578, 4, 'diag'),
    #     (139581, 4, 'diag'),
    #     (146851, 4, 'diag'),

    #     (201606, 4, 'diag'),
    # ]
    targets = []
    for tiling_id, N, sym in targets:
        remove_tiling(tiling_id, N, sym)
    