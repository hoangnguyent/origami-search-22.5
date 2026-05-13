import os
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import sessionmaker

# Adjust these imports to match your project architecture
from database.tilings.build_tilings import Topology, Tiling

def get_dynamic_db_stats(N, symmetry):
    db_path = f'database/tilings/storage/tilings_{N}_{symmetry}.db'
    if not os.path.exists(db_path):
        return None

    engine = create_engine(f'sqlite:///{db_path}')
    Session = sessionmaker(bind=engine)
    session = Session()

    # 1. Physical Footprint
    total_db_size_mb = os.path.getsize(db_path) / (1024 * 1024)

    # 2. Dynamic Prefix Audit
    # We query the 'prefixes' table directly to determine the search space scale
    try:
        # Get the total number of work units (prefixes) defined for this DB
        total_prefixes = session.execute(text("SELECT COUNT(*) FROM prefixes")).scalar() or 1
        
        # Count units flagged as 'done' or 'exhausted'
        # Adjust 'status' or 'is_done' to match your specific progress tracking column
        done_prefixes = session.execute(text(
            "SELECT COUNT(*) FROM prefixes WHERE status = 'done' OR status = 'exhausted'"
        )).scalar() or 0
        
        completion_ratio = done_prefixes / total_prefixes
    except Exception:
        # Fallback heuristic if the prefix table is missing or differently named
        total_prefixes = 0
        done_prefixes = 0
        completion_ratio = 1.0 # Assume complete if tracking is unavailable

    # 3. Object Counts
    total_topologies = session.query(Topology).count()
    total_tilings = session.query(Tiling).count()
    
    # Measure raw data density (blobs only)
    topo_blob_bytes = session.query(func.sum(func.length(Topology.binary_state))).scalar() or 0
    tiling_blob_bytes = session.query(func.sum(func.length(Tiling.tiling_blob))).scalar() or 0
    raw_data_mb = (topo_blob_bytes + tiling_blob_bytes) / (1024 * 1024)

    # 4. Extrapolation
    # We use the completion ratio to estimate the final scale of the data
    est_final_topos = int(total_topologies / completion_ratio) if completion_ratio > 0 else total_topologies
    est_final_tilings = int(total_tilings / completion_ratio) if completion_ratio > 0 else total_tilings
    est_final_size_gb = (total_db_size_mb / completion_ratio) / 1024 if completion_ratio > 0 else total_db_size_mb / 1024

    session.close()

    return {
        "config": f"{N} {symmetry}",
        "topos": total_topologies,
        "tilings": total_tilings,
        "prefix_done": done_prefixes,
        "prefix_total": total_prefixes,
        "completion_pct": completion_ratio * 100,
        "db_size_mb": total_db_size_mb,
        "raw_data_mb": raw_data_mb,
        "est_tilings": est_final_tilings,
        "est_size_gb": est_final_size_gb
    }

def print_appendix_audit():
    configs = [
        (3, 'none'), (3, 'diag'), (3, 'book'),
        (4, 'none'), (4, 'diag'), (4, 'book'),
        (5, 'diag')
    ]
    
    header = f"{'DB Config':<10} | {'Topos':<8} | {'Tilings':<8} | {'Done/Total':<12} | {'Compl%':<7} | {'Size(MB)':<8} | {'Est. Final'}"
    print("\n" + "="*100)
    print("VARIABLE-PREFIX DATABASE AUDIT")
    print("="*100)
    print(header)
    print("-" * 100)
    
    for N, sym in configs:
        s = get_dynamic_db_stats(N, sym)
        if s:
            prefix_str = f"{s['prefix_done']}/{s['prefix_total']}"
            print(f"{s['config']:<10} | "
                  f"{s['topos']:<8,} | "
                  f"{s['tilings']:<8,} | "
                  f"{prefix_str:<12} | "
                  f"{s['completion_pct']:>6.1f}% | "
                  f"{s['db_size_mb']:>8.1f} | "
                  f"{s['est_tilings']:>9,} tilings ({s['est_size_gb']:.1f} GB)")
    print(f"Total number of topologies across all DBs: {sum(get_dynamic_db_stats(N, sym)['topos'] for N, sym in configs):,}")
    print(f"Total number of tilings across all DBs: {sum(get_dynamic_db_stats(N, sym)['tilings'] for N, sym in configs):,}")

if __name__ == "__main__":
    print_appendix_audit()