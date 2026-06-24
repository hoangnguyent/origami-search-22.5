import os
import json
import sqlite3

def query_scalar(cursor, query, fallback=0):
    """Safely executes a single-value query, returning a fallback if the table/column is missing."""
    try:
        cursor.execute(query)
        res = cursor.fetchone()
        return res[0] if res and res[0] is not None else fallback
    except sqlite3.OperationalError:
        return fallback

def get_db_stats(N, symmetry):
    # Paths based on standard separation of generation and solving phases
    topo_db_path = f'database/tilings/storage/topologies_{N}_{symmetry}.db'
    tiling_db_path = f'database/tilings/storage/tilings_{N}_{symmetry}.db'
    
    stats = {
        "config": f"N={N}, Sym={symmetry}",
        "topology_db": {"exists": False},
        "tiling_db": {"exists": False}
    }
    
    # ==========================================
    # 1. TOPOLOGY DB STATS
    # ==========================================
    if os.path.exists(topo_db_path):
        stats["topology_db"]["exists"] = True
        conn = sqlite3.connect(topo_db_path)
        c = conn.cursor()
        
        # Prefix metrics
        stats["topology_db"]["total_prefixes"] = query_scalar(c, "SELECT COUNT(*) FROM prefixes")
        stats["topology_db"]["done_prefixes"] = query_scalar(c, "SELECT COUNT(*) FROM prefixes WHERE is_done = 1")
        
        # Bits per prefix (Derived from the length of the 'bits' string in a sample prefix)
        sample_bits = query_scalar(c, "SELECT bits FROM prefixes LIMIT 1", fallback="")
        stats["topology_db"]["bits_per_prefix"] = len(str(sample_bits)) if sample_bits else 0
        
        # State counts (Using the exact 'states' table)
        stats["topology_db"]["total_states"] = query_scalar(c, "SELECT COUNT(*) FROM states")
        
        conn.close()
        
    # ==========================================
    # 2. TILING DB STATS
    # ==========================================
    if os.path.exists(tiling_db_path):
        stats["tiling_db"]["exists"] = True
        
        # File size
        stats["tiling_db"]["total_db_size_mb"] = round(os.path.getsize(tiling_db_path) / (1024 * 1024), 2)
        
        conn = sqlite3.connect(tiling_db_path)
        c = conn.cursor()
        
        # Identify the sync table name
        topo_table = "topology" if query_scalar(c, "SELECT COUNT(*) FROM topology", fallback=-1) != -1 else "topologies"
        
        # Topology Sync Status
        stats["tiling_db"]["synced_topologies"] = query_scalar(c, f"SELECT COUNT(*) FROM {topo_table}")
        stats["tiling_db"]["status_0_unprocessed"] = query_scalar(c, f"SELECT COUNT(*) FROM {topo_table} WHERE status = 0")
        stats["tiling_db"]["status_1_success"] = query_scalar(c, f"SELECT COUNT(*) FROM {topo_table} WHERE status = 1")
        stats["tiling_db"]["status_2_timeout"] = query_scalar(c, f"SELECT COUNT(*) FROM {topo_table} WHERE status = 2")
        stats["tiling_db"]["status_3_error"] = query_scalar(c, f"SELECT COUNT(*) FROM {topo_table} WHERE status = 3")
        
        # Identify the tiling table name
        tiling_table = "tiling" if query_scalar(c, "SELECT COUNT(*) FROM tiling", fallback=-1) != -1 else "tilings"
        
        # Raw Data Density
        blob_bytes = query_scalar(c, f"SELECT SUM(length(tiling_blob)) FROM {tiling_table}")
        tree_bytes = query_scalar(c, f"SELECT SUM(length(embedding)) FROM {tiling_table}")
        
        stats["tiling_db"]["raw_data_only_mb"] = round((blob_bytes + tree_bytes) / (1024 * 1024), 2)
        stats["tiling_db"]["total_tilings"] = query_scalar(c, f"SELECT COUNT(*) FROM {tiling_table}")
        
        conn.close()
        
    return stats

def run_audit_and_export():
    configs = [
        (2, 'none'), (2, 'diag'), (2, 'book'),
        (3, 'none'), (3, 'diag'), (3, 'book'),
        (4, 'none'), (4, 'diag'), (4, 'book'),
        (5, 'diag'), (5, 'book'), (5, 'none'),
        (6, 'book')
    ]
    
    print("Starting Database Audit...")
    all_stats = {}
    
    for N, sym in configs:
        print(f"  Scanning N={N}, Sym={sym}...")
        config_key = f"{N}_{sym}"
        all_stats[config_key] = get_db_stats(N, sym)
        
    # Export to JSON
    output_filename = "db_audit_stats.json"
    with open(output_filename, "w") as f:
        json.dump(all_stats, f, indent=4)
        
    print(f"\nAudit complete. Data saved to {output_filename}")

if __name__ == "__main__":
    run_audit_and_export()