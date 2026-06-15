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
lookup_vertices(list[Vertex4D])
    
input: a list of Vertex4D objects representing points in the crease pattern.
output: for each vertex, an ancestry list of dicts where each dict represents a fold in the sequence, containing the fold type (axiom), reference vertices/edges, and the line itself as two cartesian floats.
"""

from __future__ import annotations
import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Optional

from database.refs.cp_tree import decode_refs
from src.engine.math225_core import Vertex4D, Fraction, AplusBsqrt2
from src.engine.cp225 import Cp225, rotate_90, rotate_180, rotate_270, reflect_x_axis

CONN = sqlite3.connect("database/refs/storage/cp_pruned.db", check_same_thread=False)
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
# DB lookup
# ---------------------------------------------------------------------------

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

def get_ancestry_chain(conn, node_id):
    chain = []
    cur = node_id
    while cur is not None:
        # We add vertices_blob and edges_blob to the single query
        row = conn.execute(
            "SELECT id, parent_id, function_name, new_crease_v1, new_crease_v2, "
            "refs, depth, canonical_id, vertices_blob, edges_blob "
            "FROM nodes WHERE id=?", (cur,)
        ).fetchone()
        
        if row is None: break
        
        chain.append({
            "id": row[0],
            "parent_id": row[1],
            "function_name": row[2],
            "new_crease_v1": unpack_vertex(row[3]),
            "new_crease_v2": unpack_vertex(row[4]),
            "refs": decode_refs(row[5]),
            "depth": row[6],
            "canonical_id": row[7],
            "vertices_blob": row[8],
            "edges_blob": row[9]
        })
        cur = row[1]
        
    chain.reverse()
    return chain

# ---------------------------------------------------------------------------
# Core lookup
# ---------------------------------------------------------------------------
def lookup_vertices(
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
    results_by_vertex = {}

    for row in rows:
        # Corrected unpacking order to match the SELECT statement
        user_idx, t_name, depth, node_id = row

        fwd_fn, inv_fn, stored_v = transform_cache[(user_idx, t_name)]
       
        results_by_vertex[user_idx] = apply_transform_to_ancestry(_get_ancestry(conn, node_id), fwd_fn)

    return results_by_vertex

# ---------------------------------------------------------------------------
# Transform a full CP into user orientation
# ---------------------------------------------------------------------------

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
            "new_crease_v1": transform_fn(step["new_crease_v1"]).to_cartesian(),
            "new_crease_v2": transform_fn(step["new_crease_v2"]).to_cartesian(),
        })
    return out


if __name__ == "__main__":
    pass
    