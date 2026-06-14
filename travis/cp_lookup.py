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
import struct
from dataclasses import dataclass, field
from typing import Callable, Optional

from math225_core import Vertex4D, Fraction, AplusBsqrt2
from cp225 import Cp225, rotate_90, rotate_180, rotate_270, reflect_x_axis

# ---------------------------------------------------------------------------
# Re-export helpers needed by callers (avoid importing cp_tree directly)
# ---------------------------------------------------------------------------

def _frac(n, d=1): return Fraction(n, d)
_HALF = _frac(1, 2)

def unpack_vertex(b: bytes) -> Vertex4D:
    xn,xd,yn,yd,zn,zd,wn,wd = struct.unpack(">8q", b)
    return Vertex4D(Fraction(xn,xd), Fraction(yn,yd),
                    Fraction(zn,zd), Fraction(wn,wd))

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
    q = ("SELECT n.id, n.parent_id, n.depth, n.canonical_id"
         " FROM cp_vertices cv JOIN nodes n ON n.id = cv.node_id"
         " WHERE cv.xn=? AND cv.xd=? AND cv.yn=? AND cv.yd=?"
         "   AND cv.zn=? AND cv.zd=? AND cv.wn=? AND cv.wd=?")
    p = [v.x.num, v.x.den, v.y.num, v.y.den,
         v.z.num, v.z.den, v.w.num, v.w.den]
    if max_depth is not None:
        q += " AND n.depth <= ?"
        p.append(max_depth)
    q += " ORDER BY n.depth ASC, n.id ASC"
    return [{"id": r[0], "parent_id": r[1], "depth": r[2], "canonical_id": r[3]}
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

def lookup_vertex_v4d(
    conn: sqlite3.Connection,
    user_vertex: Vertex4D,
    max_depth: Optional[int] = None,
    first_only: bool = True,
) -> list[LookupResult]:
    """
    Find tree nodes whose CP contains user_vertex or any of its 7 symmetry
    equivalents.

    For each of the 8 transforms T:
        stored_vertex = T^-1(user_vertex)
        query DB for stored_vertex
        for each hit node: record (node, T, stored_vertex)

    Returns results sorted by depth ASC, node_id ASC.
    If first_only=True, stops after finding the shallowest depth with any hit.
    """
    results: list[LookupResult] = []
    best_depth = None

    for name, inv_fn in INVERSES.items():
        # The vertex as it would appear in the DB if the CP is stored in
        # the canonical orientation that corresponds to this transform.
        stored_v = inv_fn(user_vertex)

        hits = _query_vertex(conn, stored_v, max_depth)
        if not hits:
            continue

        fwd_fn = TRANSFORMS[name]

        for hit in hits:
            if first_only and best_depth is not None and hit['depth'] > best_depth:
                continue
            ancestry = _get_ancestry(conn, hit['id'])
            results.append(LookupResult(
                node_id        = hit['id'],
                depth          = hit['depth'],
                transform_name = name,
                transform_fn   = fwd_fn,
                inverse_fn     = inv_fn,
                matched_stored = stored_v,
                matched_user   = user_vertex,
                ancestry       = ancestry,
            ))
            if best_depth is None or hit['depth'] < best_depth:
                best_depth = hit['depth']

    results.sort(key=lambda r: (r.depth, r.node_id))
    return results


def lookup_vertex(
    conn: sqlite3.Connection,
    ax: int, bx: int, cx: int,
    ay: int, by: int, cy: int,
    max_depth: Optional[int] = None,
    first_only: bool = True,
) -> list[LookupResult]:
    """
    Look up a vertex specified as:
        x_coord = (ax + bx*sqrt(2)) / cx
        y_coord = (ay + by*sqrt(2)) / cy

    Example: the center of the square is ax=1,bx=0,cx=2, ay=1,by=0,cy=2.
    Example: sqrt(2)-1 on x-axis is ax=-1,bx=1,cx=1, ay=0,by=0,cy=1.
    """
    v = xy_to_vertex4d(ax, bx, cx, ay, by, cy)
    return lookup_vertex_v4d(conn, v, max_depth=max_depth, first_only=first_only)


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
    import math
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
    import sys

    def _is_int(s):
        try: int(s); return True
        except ValueError: return False

    # db_path is optional first arg — detected by whether it looks like an int
    args = sys.argv[1:]
    if args and not _is_int(args[0]):
        db_path = args.pop(0)
    else:
        db_path = "cp_tree.db"

    if len(args) != 6:
        print("Usage: python cp_lookup.py [db_path] ax bx cx ay by cy")
        print()
        print("  Looks up the vertex  x=(ax + bx*sqrt(2))/cx,  y=(ay + by*sqrt(2))/cy")
        print()
        print("  Examples:")
        print("    Center of square:      python cp_lookup.py 1 0 2  1 0 2")
        print("    Bottom-left corner:    python cp_lookup.py 0 0 1  0 0 1")
        print("    sqrt(2)-1 on x-axis:   python cp_lookup.py -1 1 1  0 0 1")
        print("    Horizontal midpoint:   python cp_lookup.py 1 0 2  0 0 1")
        sys.exit(1)

    ax,bx,cx_ = int(args[0]), int(args[1]), int(args[2])
    ay,by,cy_ = int(args[3]), int(args[4]), int(args[5])

    conn = sqlite3.connect(db_path)
    try:
        import math
        xf = (ax + bx*math.sqrt(2)) / cx_
        yf = (ay + by*math.sqrt(2)) / cy_
        print(f"Looking up ({ax}+{bx}√2)/{cx_}, ({ay}+{by}√2)/{cy_}  ≈  ({xf:.6g}, {yf:.6g}) ...")
        results = lookup_vertex(conn, ax, bx, cx_, ay, by, cy_)
        if not results:
            print("No match found in tree.")
        else:
            print(f"Found {len(results)} result(s). Shallowest:\n")
            print_result(results[0], conn, verbose=True)
            if len(results) > 1:
                print(f"\nOther matches at same depth:")
                for r in results[1:]:
                    print_result(r, conn, verbose=False)
    except ValueError as e:
        print(f"Error: {e}")
    finally:
        conn.close()