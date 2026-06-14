"""
cp_lookup.py
============
Vertex lookup against the CP tree database.

Given a user-supplied vertex (as cartesian x,y floats or as a Vertex4D),
finds the shallowest node in the tree whose CP contains that vertex or any
of its 7 rotated/reflected equivalents under the square's symmetry group.

Returns the node, the transform that maps the stored CP onto the user's
orientation, and the full ancestry chain for step-by-step display.

Public API
----------
lookup_vertex(conn, cx, cy)
    -> list of LookupResult, sorted by depth then node id.
       Each result has: node_id, depth, transform_name, transform_fn,
       inverse_transform_fn, matched_vertex (as stored), ancestry_chain.

lookup_vertex_v4d(conn, v: Vertex4D)
    -> same, for callers that already have an exact Vertex4D.

LookupResult (dataclass)
    node_id         int
    depth           int
    transform_name  str          e.g. 'rot90', 'reflect_x+rot180'
    transform_fn    callable     maps stored Vertex4D -> user-orientation Vertex4D
    inverse_fn      callable     maps user-orientation Vertex4D -> stored Vertex4D
    matched_stored  Vertex4D     the vertex as it appears in the DB
    matched_user    Vertex4D     the vertex in user orientation (== input)
    ancestry        list[dict]   from get_ancestry_chain()

xy_to_vertex4d(cx, cy)
    Convert float cartesian coords to the nearest exact Vertex4D in Q(sqrt(2)).
    Raises ValueError if the point is not representable (not in Q(sqrt(2))).

Usage
-----
    import sqlite3
    from cp_lookup import lookup_vertex, apply_transform_to_cp

    conn = sqlite3.connect('cp_tree.db')
    results = lookup_vertex(conn, 0.5, 0.5)   # center of square
    if results:
        r = results[0]   # shallowest match
        print(r.node_id, r.depth, r.transform_name)
        # Get the CP in user orientation:
        cp = load_cp_by_node_id(conn, r.node_id)
        oriented_cp = apply_transform(cp, r.transform_fn)
"""

from __future__ import annotations
import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Optional

from src.engine.math225_core import Vertex4D, Fraction, AplusBsqrt2
from src.engine.cp225 import Cp225, rotate_90, rotate_180, rotate_270, reflect_x_axis

CONN = sqlite3.connect("database/refs/storage/cp_pruned.db")
# ---------------------------------------------------------------------------
# Re-export helpers needed by callers (avoid importing cp_tree directly)
# ---------------------------------------------------------------------------

def _frac(n, d=1): return Fraction(n, d)
_HALF = _frac(1, 2)

def unpack_vertex(b: bytes) -> Vertex4D:
    """Unpack a variable-length z2 blob into a Vertex4D (matches cp_tree.pack_vertex)."""
    from database.refs.cp_tree import unpack_vertex as _uv
    return _uv(b)

# ---------------------------------------------------------------------------
# Coordinate conversion
# ---------------------------------------------------------------------------

def xy_to_vertex4d(ax: int, bx: int, cx: int,
                   ay: int, by: int, cy: int) -> Vertex4D:
    """
    Construct a Vertex4D from the six integers that define a point in Q(sqrt(2))^2.

    The cartesian coordinates are:
        x_coord = (ax + bx*sqrt(2)) / cx
        y_coord = (ay + by*sqrt(2)) / cy

    Vertex4D(x,y,z,w) encodes cartesian as:
        x_coord = x + (y-w) * sqrt(2)/2   so  x_coord = x + (y-w)/sqrt(2)
        y_coord = z + (y+w) * sqrt(2)/2

    In AplusBsqrt2 form: x_coord = A_cx + B_cx*sqrt(2)
        A_cx = x,       B_cx = (y-w)/2
        A_cy = z,       B_cy = (y+w)/2

    Given user input x_coord = ax/cx + (bx/cx)*sqrt(2):
        A_cx = ax/cx  =>  x = ax/cx
        B_cx = bx/cx  =>  (y-w)/2 = bx/cx  =>  y-w = 2*bx/cx

    Given y_coord = ay/cy + (by/cy)*sqrt(2):
        A_cy = ay/cy  =>  z = ay/cy
        B_cy = by/cy  =>  (y+w)/2 = by/cy  =>  y+w = 2*by/cy

    Solving:
        y = bx/cx + by/cy
        w = by/cy - bx/cx
    """
    if cx == 0 or cy == 0:
        raise ValueError("cx and cy denominators must be non-zero")
    x = Fraction(ax, cx)
    z = Fraction(ay, cy)
    y = Fraction(bx, cx) + Fraction(by, cy)
    w = Fraction(by, cy) - Fraction(bx, cx)
    return Vertex4D(x, y, z, w)


# ---------------------------------------------------------------------------
# The 8 symmetry transforms of the unit square
#
# The cp225 transforms (rotate_90 etc.) operate on ORIGIN-CENTERED coords.
# Our square has corners at (0,0)-(1,1), centered at (0.5,0.5).
# So every transform must: translate to origin → apply → translate back.
# ---------------------------------------------------------------------------

def _translate_to_origin(v: Vertex4D) -> Vertex4D:
    return Vertex4D(v.x - _HALF, v.y, v.z - _HALF, v.w)

def _translate_from_origin(v: Vertex4D) -> Vertex4D:
    return Vertex4D(v.x + _HALF, v.y, v.z + _HALF, v.w)

def _make_transform(fn):
    """Wrap an origin-centered transform for use on unit-square vertices."""
    def t(v: Vertex4D) -> Vertex4D:
        return _translate_from_origin(fn(_translate_to_origin(v)))
    return t

# Identity
_identity_origin = lambda v: v

# Build all 8 transforms operating on origin-centered coords
_origin_transforms = {
    'identity':        _identity_origin,
    'rot90':           rotate_90,
    'rot180':          rotate_180,
    'rot270':          rotate_270,
    'reflect_x':       reflect_x_axis,
    'reflect_x+rot90': lambda v: rotate_90(reflect_x_axis(v)),
    'reflect_x+rot180':lambda v: rotate_180(reflect_x_axis(v)),
    'reflect_x+rot270':lambda v: rotate_270(reflect_x_axis(v)),
}

# Inverses (in origin-centered space)
# rot90^-1 = rot270, rot180^-1 = rot180, rot270^-1 = rot90
# reflect^-1 = reflect, (reflect∘rot)^-1 = rot^-1∘reflect
_origin_inverses = {
    'identity':         _identity_origin,
    'rot90':            rotate_270,
    'rot180':           rotate_180,
    'rot270':           rotate_90,
    'reflect_x':        reflect_x_axis,
    'reflect_x+rot90':  lambda v: reflect_x_axis(rotate_270(v)),
    'reflect_x+rot180': lambda v: reflect_x_axis(rotate_180(v)),
    'reflect_x+rot270': lambda v: reflect_x_axis(rotate_90(v)),
}

# Unit-square versions
TRANSFORMS: dict[str, Callable] = {
    name: _make_transform(fn) for name, fn in _origin_transforms.items()
}
INVERSES: dict[str, Callable] = {
    name: _make_transform(fn) for name, fn in _origin_inverses.items()
}


# ---------------------------------------------------------------------------
# LookupResult
# ---------------------------------------------------------------------------

@dataclass
class LookupResult:
    node_id:          int
    depth:            int
    transform_name:   str        # which of the 8 symmetries found the match
    transform_fn:     Callable   # stored → user orientation
    inverse_fn:       Callable   # user → stored orientation
    matched_stored:   Vertex4D   # vertex as it lives in the DB
    matched_user:     Vertex4D   # vertex in user's orientation (== query input)
    ancestry:         list       # get_ancestry_chain() result


# ---------------------------------------------------------------------------
# DB query
# ---------------------------------------------------------------------------

def _query_vertex(conn: sqlite3.Connection, v: Vertex4D,
                  max_depth: Optional[int] = None) -> list[dict]:
    """Find all nodes containing exactly vertex v, ordered by depth."""
    from database.refs.cp_tree import v4d_to_z2
    px, qx, dx, py, qy, dy = v4d_to_z2(v)
    
    q = ("SELECT node_id, depth"
         " FROM vertex_index"
         " WHERE px=? AND qx=? AND dx=?"
         "   AND py=? AND qy=? AND dy=?")
    p = [px, qx, dx, py, qy, dy]
    
    if max_depth is not None:
        q += " AND depth <= ?"
        p.append(max_depth)
        
    q += " ORDER BY depth ASC, node_id ASC"
    
    # We return dummy None values for unused keys to avoid breaking downstream typing
    return [{"id": r[0], "parent_id": None, "depth": r[1], "canonical_id": None}
            for r in conn.execute(q, p)]

def _get_ancestry(conn: sqlite3.Connection, node_id: int) -> list[dict]:
    chain = []
    cur = node_id
    while cur is not None:
        row = conn.execute(
            "SELECT id, parent_id, function_name, new_crease_v1, new_crease_v2,"
            " refs, depth, canonical_id FROM nodes WHERE id=?", (cur,)).fetchone()
        if row is None:
            break
        chain.append({
            "id": row[0], "parent_id": row[1], "function_name": row[2],
            "new_crease_v1": unpack_vertex(row[3]),
            "new_crease_v2": unpack_vertex(row[4]),
            "refs_raw": row[5],
            "depth": row[6], "canonical_id": row[7],
        })
        cur = row[1]
    chain.reverse()
    return chain


def _load_cp(conn: sqlite3.Connection, node_id: int) -> Cp225:
    vrows = conn.execute(
        "SELECT node_id,xn,xd,yn,yd,zn,zd,wn,wd,vertex_index"
        " FROM cp_vertices WHERE node_id=? ORDER BY vertex_index",
        (node_id,)).fetchall()
    erows = conn.execute(
        "SELECT node_id,v1_idx,v2_idx,line_type FROM cp_edges WHERE node_id=?",
        (node_id,)).fetchall()
    verts = [Vertex4D(Fraction(r[1],r[2]), Fraction(r[3],r[4]),
                      Fraction(r[5],r[6]), Fraction(r[7],r[8]))
             for r in sorted(vrows, key=lambda r: r[-1])]
    edges = [(v1, v2, lt) for (_, v1, v2, lt) in erows]
    return Cp225(verts, edges)


# ---------------------------------------------------------------------------
# Core lookup
# ---------------------------------------------------------------------------
# def lookup_vertex_v4d(
#     user_vertex: Vertex4D,
#     max_depth: Optional[int] = None,
#     first_only: bool = True,
#     conn: sqlite3.Connection = CONN,
# ) -> list[LookupResult]:
    
#     from database.refs.cp_tree import v4d_to_z2
    
#     # Pre-calculate the 8 transforms
#     transform_data = []
#     z2_tuples = []
    
#     for name, inv_fn in INVERSES.items():
#         stored_v = inv_fn(user_vertex)
#         z2 = v4d_to_z2(stored_v)
#         transform_data.append((name, TRANSFORMS[name], inv_fn, stored_v, z2))
#         z2_tuples.append(z2)

#     # Build the IN clause for 8 coordinate sets
#     placeholders = ", ".join(["(?,?,?,?,?,?)"] * 8)
#     flat_params = [val for z2 in z2_tuples for val in z2]
    
#     q = (f"SELECT node_id, depth, px, qx, dx, py, qy, dy "
#          f"FROM vertex_index "
#          f"WHERE (px, qx, dx, py, qy, dy) IN ({placeholders})")
    
#     if max_depth is not None:
#         q += " AND depth <= ?"
#         flat_params.append(max_depth)
        
#     q += " ORDER BY depth ASC, node_id ASC"
    
#     # Execute exactly 1 query
#     rows = conn.execute(q, flat_params).fetchall()
    
#     results: list[LookupResult] = []
#     best_depth = None

#     for r in rows:
#         node_id, depth, px, qx, dx, py, qy, dy = r
        
#         if first_only and best_depth is not None and depth > best_depth:
#             continue
            
#         # Match the returned row back to which transform it was
#         row_z2 = (px, qx, dx, py, qy, dy)
#         matching_transforms = [td for td in transform_data if td[4] == row_z2]
        
#         for td in matching_transforms:
#             name, fwd_fn, inv_fn, stored_v, _ = td
#             ancestry = _get_ancestry(conn, node_id)
            
#             results.append(LookupResult(
#                 node_id        = node_id,
#                 depth          = depth,
#                 transform_name = name,
#                 transform_fn   = fwd_fn,
#                 inverse_fn     = inv_fn,
#                 matched_stored = stored_v,
#                 matched_user   = user_vertex,
#                 ancestry       = ancestry,
#             ))
            
#             if best_depth is None or depth < best_depth:
#                 best_depth = depth

#     results.sort(key=lambda r: (r.depth, r.node_id))
#     return results
def lookup_multiple_vertices(
    user_vertices: list[Vertex4D],
    max_depth: Optional[int] = None,
    first_only: bool = True,
    conn: sqlite3.Connection = CONN,
) -> dict[int, list[LookupResult]]:
    """
    Batches multiple Vertex4D lookups into a single SQLite JOIN operation.
    Returns a dictionary mapping the index of the input vertex to its results.
    """
    from database.refs.cp_tree import v4d_to_z2

    # 1. Create an in-memory temporary table for this batch
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS batch_query ("
                 "user_idx INTEGER, transform_name TEXT, "
                 "px INTEGER, qx INTEGER, dx INTEGER, "
                 "py INTEGER, qy INTEGER, dy INTEGER)")
    conn.execute("DELETE FROM batch_query") # Clear it if it already exists

    # 2. Prepare the 8 permutations for all input vertices
    insert_data = []
    transform_cache = {} # Map (user_idx, transform_name) to (fwd_fn, inv_fn, stored_v)

    for idx, user_vertex in enumerate(user_vertices):
        for name, inv_fn in INVERSES.items():
            stored_v = inv_fn(user_vertex)
            px, qx, dx, py, qy, dy = v4d_to_z2(stored_v)
            
            insert_data.append((idx, name, px, qx, dx, py, qy, dy))
            transform_cache[(idx, name)] = (TRANSFORMS[name], inv_fn, stored_v)

    # 3. Bulk insert the query coordinates (Extremely fast)
    conn.executemany(
        "INSERT INTO batch_query VALUES (?, ?, ?, ?, ?, ?, ?, ?)", 
        insert_data
    )
    conn.execute("CREATE INDEX IF NOT EXISTS temp.idx_batch ON batch_query(px, qx, dx, py, qy, dy)")
    # 4. The Magic: Short-circuit the Index Scan
    if first_only:
        # Extreme optimization: Forces SQLite to fetch ONLY the absolute shallowest 
        # rowid per transform before doing any joins or grouping.
        max_depth_sql = f"AND v_sub.depth <= {int(max_depth)}" if max_depth is not None else ""
        
        query = f"""
            SELECT 
                b.user_idx, 
                b.transform_name, 
                MIN(v.depth) as min_depth, 
                v.node_id
            FROM batch_query b
            JOIN vertex_index v ON v.rowid = (
                SELECT v_sub.rowid
                FROM vertex_index v_sub
                WHERE v_sub.px = b.px AND v_sub.qx = b.qx AND v_sub.dx = b.dx
                  AND v_sub.py = b.py AND v_sub.qy = b.qy AND v_sub.dy = b.dy
                  {max_depth_sql}
                ORDER BY v_sub.depth ASC, v_sub.node_id ASC
                LIMIT 1
            )
            GROUP BY b.user_idx
        """
        params = []
    else:
        # Standard join if you genuinely want the entire list of nodes 
        # (Warning: will return millions of rows for common vertices)
        query = """
            SELECT b.user_idx, b.transform_name, v.depth, v.node_id
            FROM batch_query b
            INNER JOIN vertex_index v 
                ON b.px = v.px AND b.qx = v.qx AND b.dx = v.dx
               AND b.py = v.py AND b.qy = v.qy AND b.dy = v.dy
        """
        params = []
        if max_depth is not None:
            query += " WHERE v.depth <= ?"
            params.append(max_depth)
        query += " ORDER BY b.user_idx ASC, v.depth ASC, v.node_id ASC"

    # 5. Execute
    rows = conn.execute(query, params).fetchall()

    # 6. Parse results
    results_by_vertex = {i: [] for i in range(len(user_vertices))}
    best_depth_by_vertex = {}

    for row in rows:
        user_idx, t_name, node_id, depth = row
        
        if first_only:
            best = best_depth_by_vertex.get(user_idx)
            if best is not None and depth > best:
                continue
            best_depth_by_vertex[user_idx] = depth

        fwd_fn, inv_fn, stored_v = transform_cache[(user_idx, t_name)]
        
        results_by_vertex[user_idx].append(LookupResult(
            node_id        = node_id,
            depth          = depth,
            transform_name = t_name,
            transform_fn   = fwd_fn,
            inverse_fn     = inv_fn,
            matched_stored = stored_v,
            matched_user   = user_vertices[user_idx],
            ancestry       = None, # See note below regarding ancestry
        ))

    return results_by_vertex
# def lookup_vertex(
#     conn: sqlite3.Connection,
#     ax: int, bx: int, cx: int,
#     ay: int, by: int, cy: int,
#     max_depth: Optional[int] = None,
#     first_only: bool = True,
# ) -> list[LookupResult]:
#     """
#     Look up a vertex specified as:
#         x_coord = (ax + bx*sqrt(2)) / cx
#         y_coord = (ay + by*sqrt(2)) / cy

#     Example: the center of the square is ax=1,bx=0,cx=2, ay=1,by=0,cy=2.
#     Example: sqrt(2)-1 on x-axis is ax=-1,bx=1,cx=1, ay=0,by=0,cy=1.
#     """
#     v = xy_to_vertex4d(ax, bx, cx, ay, by, cy)
#     return lookup_vertex_v4d(conn, v, max_depth=max_depth, first_only=first_only)


# ---------------------------------------------------------------------------
# Transform a full CP into user orientation
# ---------------------------------------------------------------------------

def apply_transform(cp: Cp225, transform_fn: Callable) -> Cp225:
    """
    Return a new Cp225 with all vertices mapped through transform_fn.
    Edges are unchanged (indices stay the same).
    """
    return Cp225([transform_fn(v) for v in cp.vertices], list(cp.edges))


def apply_transform_to_ancestry(
    ancestry: list[dict],
    transform_fn: Callable,
) -> list[dict]:
    """
    Return a copy of the ancestry chain with all Vertex4D fields transformed.
    Useful for drawing the step sequence in user orientation.
    """
    out = []
    for step in ancestry:
        out.append({
            **step,
            "new_crease_v1": transform_fn(step["new_crease_v1"]),
            "new_crease_v2": transform_fn(step["new_crease_v2"]),
        })
    return out


# ---------------------------------------------------------------------------
# Pretty-print helper
# ---------------------------------------------------------------------------

def print_result(r: LookupResult, conn: sqlite3.Connection, verbose: bool = True):
    def fmt(v: Vertex4D):
        c = v.to_cartesian()
        return f"({c[0]:.4g}, {c[1]:.4g})"

    print(f"Node #{r.node_id}  depth={r.depth}  transform={r.transform_name}")
    print(f"  matched vertex (stored):  {fmt(r.matched_stored)}")
    print(f"  matched vertex (user):    {fmt(r.matched_user)}")

    if verbose:
        print(f"  Steps ({len(r.ancestry)}):")
        for step in r.ancestry:
            fn = step['function_name']
            if fn == 'root':
                print(f"    [0] root (unit square)")
            else:
                v1 = step['new_crease_v1']; v2 = step['new_crease_v2']
                print(f"    [{step['depth']}] {fn}: {fmt(v1)} → {fmt(v2)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pass
    # import sys

    # def _is_int(s):
    #     try: int(s); return True
    #     except ValueError: return False

    # # db_path is optional first arg — detected by whether it looks like an int
    # args = sys.argv[1:]
    # if args and not _is_int(args[0]):
    #     db_path = args.pop(0)
    # else:
    #     db_path = "cp_tree.db"

    # if len(args) != 6:
    #     print("Usage: python cp_lookup.py [db_path] ax bx cx ay by cy")
    #     print()
    #     print("  Looks up the vertex  x=(ax + bx*sqrt(2))/cx,  y=(ay + by*sqrt(2))/cy")
    #     print()
    #     print("  Examples:")
    #     print("    Center of square:      python cp_lookup.py 1 0 2  1 0 2")
    #     print("    Bottom-left corner:    python cp_lookup.py 0 0 1  0 0 1")
    #     print("    sqrt(2)-1 on x-axis:   python cp_lookup.py -1 1 1  0 0 1")
    #     print("    Horizontal midpoint:   python cp_lookup.py 1 0 2  0 0 1")
    #     sys.exit(1)

    # ax,bx,cx_ = int(args[0]), int(args[1]), int(args[2])
    # ay,by,cy_ = int(args[3]), int(args[4]), int(args[5])

    # conn = sqlite3.connect(db_path)
    # try:
    #     import math
    #     xf = (ax + bx*math.sqrt(2)) / cx_
    #     yf = (ay + by*math.sqrt(2)) / cy_
    #     print(f"Looking up ({ax}+{bx}√2)/{cx_}, ({ay}+{by}√2)/{cy_}  ≈  ({xf:.6g}, {yf:.6g}) ...")
    #     results = lookup_vertex(conn, ax, bx, cx_, ay, by, cy_)
    #     if not results:
    #         print("No match found in tree.")
    #     else:
    #         print(f"Found {len(results)} result(s). Shallowest:\n")
    #         print_result(results[0], conn, verbose=True)
    #         if len(results) > 1:
    #             print(f"\nOther matches at same depth:")
    #             for r in results[1:]:
    #                 print_result(r, conn, verbose=False)
    # except ValueError as e:
    #     print(f"Error: {e}")
    # finally:
    #     conn.close()