"""
cp_tree.py  (v4)
================
Tree of crease patterns, one new crease per child, stored in SQLite.

Usage:
    python cp_tree.py [max_depth] [db_path]
    python cp_tree.py 6               # depth 6, default db
    python cp_tree.py 6 myfile.db     # depth 6, named db
    python cp_tree.py                 # depth 4, default db

Resume logic: on restart the build finds the max depth already fully written
to the DB and continues from there.  The in-memory `seen` set only accumulates
canonical IDs generated during the *current run*, so already-stored siblings
don't ghost future expansions.

new_vertex_idxs: only vertices created by intersection (index >= parent count).
Pre-existing vertices stay "old" even if used to define the crease.

refs: JSON list of typed ref objects.
  {"type":"vertex",  "v":[xn,xd,yn,yd,zn,zd,wn,wd]}
  {"type":"crease",  "v1":[...], "v2":[...]}
  {"type":"edge",    "v1":[...], "v2":[...]}
"""

import sqlite3, hashlib, json, struct, itertools
from typing import Optional

from math225_core import Vertex4D, Fraction, AplusBsqrt2
from cp225 import Cp225, freeze, canonicalize, vertex_on_border
from cp_general_crease import (
    add_general_crease,
    _vertex_on_infinite_line,
    vertex4d_to_aplusbsqrt2_xy,
    aplusbsqrt2_xy_to_vertex4d,
    point_on_segment_exact,
)

# ---------------------------------------------------------------------------
# Vertex4D pack / unpack
# ---------------------------------------------------------------------------

def pack_vertex(v):
    return struct.pack(">8q",
        v.x.num,v.x.den,v.y.num,v.y.den,
        v.z.num,v.z.den,v.w.num,v.w.den)

def unpack_vertex(b):
    xn,xd,yn,yd,zn,zd,wn,wd = struct.unpack(">8q", b)
    return Vertex4D(Fraction(xn,xd),Fraction(yn,yd),
                    Fraction(zn,zd),Fraction(wn,wd))

def vertex_to_list(v):
    return [v.x.num,v.x.den,v.y.num,v.y.den,
            v.z.num,v.z.den,v.w.num,v.w.den]

def list_to_vertex(lst):
    return Vertex4D(Fraction(lst[0],lst[1]),Fraction(lst[2],lst[3]),
                    Fraction(lst[4],lst[5]),Fraction(lst[6],lst[7]))

def canonical_hash(frozen):
    return hashlib.sha256(repr(frozen).encode()).digest()

_HALF = Fraction(1, 2)

def _translate_to_origin(v):
    """
    Shift a Vertex4D by cartesian (-0.5, -0.5), i.e. subtract 1/2 from the
    x and z components (which hold the rational parts of cx and cy).
    This maps our unit-square (corners at 0 and 1) to an origin-centred square
    (corners at ±0.5) so that cp225's rotate_90/180/270/reflect — which rotate
    about the origin — correctly identify rotationally-equivalent CPs.
    """
    return Vertex4D(v.x - _HALF, v.y, v.z - _HALF, v.w)

def canonicalize_centered(cp) -> tuple:
    """
    Canonical form invariant under the 8-element symmetry group of the square,
    correctly handling our (0,0)-(1,1) square by translating to origin first.
    """
    shifted = Cp225([_translate_to_origin(v) for v in cp.vertices], list(cp.edges))
    return canonicalize(shifted)

# ---------------------------------------------------------------------------
# Ref helpers
# ---------------------------------------------------------------------------

def ref_vertex(v):  return {"type":"vertex","v":vertex_to_list(v)}
def ref_crease(v1,v2): return {"type":"crease","v1":vertex_to_list(v1),"v2":vertex_to_list(v2)}
def ref_edge(v1,v2):   return {"type":"edge",  "v1":vertex_to_list(v1),"v2":vertex_to_list(v2)}

def encode_refs(refs): return json.dumps(refs)

def decode_refs(s):
    out = []
    for r in json.loads(s):
        t = r["type"]
        if t == "vertex":
            out.append({"type":t,"v":list_to_vertex(r["v"])})
        elif t in ("crease","edge"):
            out.append({"type":t,"v1":list_to_vertex(r["v1"]),"v2":list_to_vertex(r["v2"])})
        else:
            out.append(r)
    return out

# ---------------------------------------------------------------------------
# Fraction / Vertex4D convenience
# ---------------------------------------------------------------------------

def _frac(n,d=1): return Fraction(n,d)
def _v(cx,cy):
    if isinstance(cx,int): cx=_frac(cx)
    if isinstance(cy,int): cy=_frac(cy)
    return Vertex4D(cx,_frac(0),cy,_frac(0))

# ---------------------------------------------------------------------------
# Root CP — unit square (0,0),(1,0),(1,1),(0,1)
# ---------------------------------------------------------------------------

def make_root_cp():
    verts = [_v(0,0),_v(1,0),_v(1,1),_v(0,1)]
    edges = [(0,1,'b'),(1,2,'b'),(2,3,'b'),(3,0,'b')]
    return Cp225(verts,edges)

# ---------------------------------------------------------------------------
# CP serialization
# ---------------------------------------------------------------------------

def cp_to_rows(node_id, cp):
    vrows = [(node_id,
              v.x.num,v.x.den,v.y.num,v.y.den,
              v.z.num,v.z.den,v.w.num,v.w.den,i)
             for i,v in enumerate(cp.vertices)]
    erows = [(node_id,v1,v2,lt) for v1,v2,lt in cp.edges]
    return vrows,erows

def rows_to_cp(vrows,erows):
    verts=[Vertex4D(Fraction(r[1],r[2]),Fraction(r[3],r[4]),
                    Fraction(r[5],r[6]),Fraction(r[7],r[8]))
           for r in sorted(vrows,key=lambda r:r[-1])]
    edges=[(v1,v2,lt) for (_,v1,v2,lt) in erows]
    return Cp225(verts,edges)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _copy_cp(cp):
    return Cp225(list(cp.vertices),list(cp.edges))

def _find_crease_boundary_endpoints(cp, ref_v1, ref_v2, neighbors_fresh=False):
    if not neighbors_fresh:
        cp.get_vertex_neighbors()
    hits = []
    for idx,v in enumerate(cp.vertices):
        if not _vertex_on_infinite_line(ref_v1,ref_v2,v): continue
        try:
            if vertex_on_border(cp.vertex_neighbors[idx]):
                hits.append(v)
        except ValueError:
            pass
    if len(hits) < 2: return None
    rx,ry = vertex4d_to_aplusbsqrt2_xy(ref_v1)
    dx = vertex4d_to_aplusbsqrt2_xy(ref_v2)[0]-rx
    dy = vertex4d_to_aplusbsqrt2_xy(ref_v2)[1]-ry
    hits.sort(key=lambda v:float(dx*(vertex4d_to_aplusbsqrt2_xy(v)[0]-rx)
                                +dy*(vertex4d_to_aplusbsqrt2_xy(v)[1]-ry)))
    return [hits[0],hits[-1]]

# ---------------------------------------------------------------------------
# Function 1: vertex_pair
# ---------------------------------------------------------------------------

# Square corners — present in every CP, used to identify corner-corner pairs
_SQUARE_CORNERS = {(_frac(0),_frac(0)), (_frac(1),_frac(0)),
                   (_frac(1),_frac(1)), (_frac(0),_frac(1))}

def _is_corner(v):
    """True if v is one of the four unit-square corners."""
    return (v.x, v.z) in _SQUARE_CORNERS and v.y.num==0 and v.w.num==0

def generate_vertex_pair_children(cp, new_vertex_indices):
    """
    Skip a pair (i,j) only if it is provably redundant:
    1. Both are square corners: corner-corner creases (edges/diagonals) were
       all tried at the root; any novel one is already in the tree shallower.
    2. Both are in new_vertex_indices AND both lie on new_crease_line: they
       are collinear on the crease just added, so the line = that crease.
       (This case is handled implicitly: add_general_crease returns None.)
    All other pairs are tried — including old-interior with old-interior,
    which are needed when vertices from different ancestor creases interact.
    """
    n = len(cp.vertices)
    has_interior = any(not _is_corner(cp.vertices[k]) for k in range(n))

    # Build a set of existing directed edges for O(1) lookup.
    # If a direct edge already exists between i and j, the line is already
    # present and add_general_crease will return None — skip early.
    existing_edges = {(min(v1i,v2i), max(v1i,v2i)) for v1i,v2i,_ in cp.edges}

    children = []
    for i,j in itertools.combinations(range(n), 2):
        v1,v2 = cp.vertices[i],cp.vertices[j]
        if has_interior and _is_corner(v1) and _is_corner(v2):
            continue
        # Skip if a direct edge already exists (line is already drawn)
        if (min(i,j), max(i,j)) in existing_edges:
            continue
        new_cp = _copy_cp(cp)
        parent_vc = len(new_cp.vertices)
        if add_general_crease(new_cp,v1,v2,line_type='m') is None: continue
        bpts = _find_crease_boundary_endpoints(new_cp,v1,v2, neighbors_fresh=True)
        if bpts is None: continue
        children.append({
            'cp':              new_cp,
            'new_vertex_idxs': set(range(parent_vc,len(new_cp.vertices))),
            'refs':            [ref_vertex(v1),ref_vertex(v2)],
            'new_crease_v1':   bpts[0],
            'new_crease_v2':   bpts[1],
            'function_name':   'vertex_pair',
        })
    return children

# ---------------------------------------------------------------------------
# Function 2: angle_bisector
# ---------------------------------------------------------------------------

def _make_bisector_specs():
    F=Fraction
    def V(x,y,z,w): return Vertex4D(F(x),F(y),F(z),F(w))
    BL=_v(0,0); BR=_v(1,0); TR=_v(1,1); TL=_v(0,1)
    d1v1,d1v2 = BL,TR
    specs=[
        dict(diagonal=(d1v1,d1v2),corner=BL,target=V(1, 1,-1, 1),refs=[ref_crease(d1v1,d1v2),ref_edge(BL,BR)]),
        dict(diagonal=(d1v1,d1v2),corner=BL,target=V(1, 1, 1, 1),refs=[ref_crease(d1v1,d1v2),ref_edge(BL,TL)]),
        dict(diagonal=(d1v1,d1v2),corner=TR,target=V(2,-1, 0, 1),refs=[ref_crease(d1v1,d1v2),ref_edge(TR,BR)]),
        dict(diagonal=(d1v1,d1v2),corner=TR,target=V(0,-1, 2,-1),refs=[ref_crease(d1v1,d1v2),ref_edge(TR,TL)]),
    ]
    d2v1,d2v2 = BR,TL
    def rot90(v):
        cx,cy = vertex4d_to_aplusbsqrt2_xy(v)
        one = AplusBsqrt2(F(1),F(0))
        return aplusbsqrt2_xy_to_vertex4d(one-cy,cx)
    for s in list(specs):
        if s['diagonal']!=(d1v1,d1v2): continue
        specs.append(dict(
            diagonal=(d2v1,d2v2),
            corner=rot90(s['corner']), target=rot90(s['target']),
            refs=[ref_crease(d2v1,d2v2),
                  ref_edge(rot90(list_to_vertex(s['refs'][1]['v1'])),
                           rot90(list_to_vertex(s['refs'][1]['v2'])))],
        ))
    return specs

_BISECTOR_SPECS = _make_bisector_specs()

def _edge_exists_between(cp,va,vb):
    try: ia=cp.vertices.index(va); ib=cp.vertices.index(vb)
    except ValueError: return False
    return any((e0==ia and e1==ib)or(e0==ib and e1==ia) for e0,e1,_ in cp.edges)

def generate_angle_bisector_children(cp, new_vertex_indices, depth=None):
    children=[]
    for spec in _BISECTOR_SPECS:
        d_v1,d_v2=spec['diagonal']
        if not _edge_exists_between(cp,d_v1,d_v2): continue
        new_cp=_copy_cp(cp); parent_vc=len(new_cp.vertices)
        if add_general_crease(new_cp,spec['corner'],spec['target'],line_type='m') is None: continue
        bpts=_find_crease_boundary_endpoints(new_cp,spec['corner'],spec['target'],neighbors_fresh=True)
        if bpts is None: continue
        children.append({
            'cp':              new_cp,
            'new_vertex_idxs': set(range(parent_vc,len(new_cp.vertices))),
            'refs':            spec['refs'],
            'new_crease_v1':   bpts[0],
            'new_crease_v2':   bpts[1],
            'function_name':   'angle_bisector',
        })
    return children

def generate_all_children(cp, new_vertex_indices, new_crease_line, depth=None):
    """
    new_crease_line: (v1, v2) boundary endpoints of the crease that created
    this node, or None for the root.
    depth: current node depth, used to gate expensive/exhausted functions.
    """
    return (generate_vertex_pair_children(cp, new_vertex_indices)+
            generate_angle_bisector_children(cp, new_vertex_indices, depth=depth)+
            generate_perpendicular_children(cp, new_vertex_indices)+
            generate_parallel_bisector_children(cp, new_vertex_indices, new_crease_line))

# ---------------------------------------------------------------------------
# Function 4: parallel_bisector
#
# For every pair of distinct parallel creases whose direction is a multiple of
# 45° (horizontal, slope+1, vertical, slope-1), add the crease halfway between
# them — the "fold one onto the other" crease.
#
# This includes the four edges of the square, so e.g. the horizontal bisector
# of the bottom and top edges is the horizontal midline of the square, even if
# no internal horizontal crease exists yet.
#
# Algorithm
# ---------
# 1.  Classify each edge into one of four direction families by checking its
#     direction vector (dx, dy) as AplusBsqrt2.
# 2.  Within each family, group edges by their line identity (the perpendicular
#     intercept, stored as a canonical frozenset key).
# 3.  For each pair of distinct lines within a family, pick one representative
#     vertex from each line, average them to get the midpoint, then add a crease
#     through that midpoint parallel to the family direction.
#
# The midpoint of two Vertex4D objects is (v1 + v2) * Fraction(1,2) — exact.
# The second point for the crease direction is midpoint + direction_offset,
# where direction_offset is a unit Vertex4D in the family's direction.
# ---------------------------------------------------------------------------

def _aplusbsqrt2_key(a):
    """Hashable key for an AplusBsqrt2 value: (num, den, num, den) tuple."""
    return (a.A.num, a.A.den, a.B.num, a.B.den)

# Direction offsets in Vertex4D for each 45n family.
# These are used to define the second point for add_general_crease.
# Horizontal (0°):  cartesian (1, 0) → Vertex4D(1,0,0,0)
# Diagonal+  (45°): cartesian (1, 1) → Vertex4D(1,0,1,0)
# Vertical   (90°): cartesian (0, 1) → Vertex4D(0,0,1,0)
# Diagonal-  (135°):cartesian (1,-1) → Vertex4D(1,0,-1,0) [or (-1,0,1,0)]
_F1 = Fraction(1)
_F0 = Fraction(0)
_Fm1= Fraction(-1)

_DIR_OFFSET = {
    'H':  Vertex4D(_F1,  _F0,  _F0,  _F0),
    'D+': Vertex4D(_F1,  _F0,  _F1,  _F0),
    'V':  Vertex4D(_F0,  _F0,  _F1,  _F0),
    'D-': Vertex4D(_F1,  _F0, _Fm1,  _F0),
}

def _classify_edge_direction(v1: Vertex4D, v2: Vertex4D):
    """
    Return 'H', 'V', 'D+', 'D-', or None for the four 45n families.
    Uses exact AplusBsqrt2 arithmetic via _is_zero.
    """
    from cp_general_crease import vertex4d_to_aplusbsqrt2_xy, _is_zero
    ax, ay = vertex4d_to_aplusbsqrt2_xy(v1)
    bx, by = vertex4d_to_aplusbsqrt2_xy(v2)
    dx = bx - ax
    dy = by - ay
    if _is_zero(dy):              return 'H'   # horizontal
    if _is_zero(dx):              return 'V'   # vertical
    if _is_zero(dx - dy):         return 'D+'  # slope +1
    if _is_zero(dx + dy):         return 'D-'  # slope -1
    return None

def _line_key(family: str, v: Vertex4D):
    """
    Canonical key identifying the infinite line through v in the given family.
    Returns the perpendicular intercept as a frozenset-friendly tuple.

    H:   cy is constant  → key = (A_cy, B_cy) of cartesian y
    V:   cx is constant  → key = (A_cx, B_cx) of cartesian x
    D+:  cy - cx is constant (slope+1 lines: y = x + c  →  c = y - x)
    D-:  cy + cx is constant (slope-1 lines: y = -x + c → c = y + x)
    """
    from cp_general_crease import vertex4d_to_aplusbsqrt2_xy
    cx, cy = vertex4d_to_aplusbsqrt2_xy(v)
    if family == 'H':   val = cy
    elif family == 'V': val = cx
    elif family == 'D+':val = cy - cx
    else:               val = cy + cx   # D-
    return (val.A.num, val.A.den, val.B.num, val.B.den)


def generate_parallel_bisector_children(cp, new_vertex_indices, new_crease_line):
    """
    For each pair of distinct parallel 45n-angle creases, add the midline crease.
    Restricts to pairs where at least one member is the new crease (new_crease_line),
    since all other pairs were available to ancestors and already generated.
    new_crease_line: (v1, v2) or None (root).
    """
    from cp_general_crease import vertex4d_to_aplusbsqrt2_xy, _is_zero

    # ── Step 1: group edges by (family, line_key) ─────────────────────────
    # lines[family][line_key] = one representative Vertex4D from that line
    lines = {'H': {}, 'V': {}, 'D+': {}, 'D-': {}}

    cp.get_vertex_neighbors()

    for v1i, v2i, ltype in cp.edges:
        v1 = cp.vertices[v1i]
        v2 = cp.vertices[v2i]
        fam = _classify_edge_direction(v1, v2)
        if fam is None:
            continue
        key = _line_key(fam, v1)
        if key not in lines[fam]:
            lines[fam][key] = (v1, v2)  # store both endpoints to define the line

    children = []

    # ── Step 2: for each family, iterate all pairs of distinct lines ──────
    for fam, line_dict in lines.items():
        line_keys = list(line_dict.keys())
        dir_offset = _DIR_OFFSET[fam]

        for i, j in itertools.combinations(range(len(line_keys)), 2):
            ka, kb = line_keys[i], line_keys[j]
            la1, la2 = line_dict[ka]   # two endpoints of line A
            lb1, lb2 = line_dict[kb]   # two endpoints of line B

            # Midpoint vertex: average of one point from each line (exact)
            mid = (la1 + lb1) * Fraction(1, 2)

            # Second point on the bisector crease (parallel to family direction)
            mid2 = mid + dir_offset

            new_cp  = _copy_cp(cp)
            pvc     = len(new_cp.vertices)

            if add_general_crease(new_cp, mid, mid2, line_type='m') is None:
                continue

            bpts = _find_crease_boundary_endpoints(new_cp, mid, mid2, neighbors_fresh=True)
            if bpts is None:
                continue

            # refs: the two representative vertices that defined the pair
            # (one from each source line — enough to reconstruct which lines
            # were bisected when drawing the step)
            children.append({
                'cp':              new_cp,
                'new_vertex_idxs': set(range(pvc, len(new_cp.vertices))),
                'refs':            [ref_crease(la1, la2), ref_crease(lb1, lb2)],
                'new_crease_v1':   bpts[0],
                'new_crease_v2':   bpts[1],
                'function_name':   'parallel_bisector',
            })

    return children

# ---------------------------------------------------------------------------
# Function 3: perpendicular_through_vertex
#
# For each new vertex in the CP, emit up to 4 children:
#   child H: horizontal crease (y = const) through the vertex
#             — skipped if vertex is on y=0 or y=1 (already a boundary)
#   child V: vertical crease (x = const) through the vertex
#             — skipped if vertex is on x=0 or x=1
#   child D1: 45° crease (slope +1, i.e. y-y0 = x-x0) through the vertex
#              — only if the BR→TL diagonal is present in the CP
#              ref: that diagonal + the vertex
#   child D2: 135° crease (slope -1, i.e. y-y0 = -(x-x0)) through the vertex
#              — only if the BL→TR diagonal is present in the CP
#              ref: that diagonal + the vertex
#
# "Horizontal" and "vertical" are relative to the square's axes.
# For H/V, refs = [ref_vertex(v), ref_edge(boundary edge perpendicular to crease)]
# For D1/D2, refs = [ref_vertex(v), ref_crease(diagonal)]
# ---------------------------------------------------------------------------

def _is_on_boundary_x(cx_float): return abs(cx_float)<1e-9 or abs(cx_float-1)<1e-9
def _is_on_boundary_y(cy_float): return abs(cy_float)<1e-9 or abs(cy_float-1)<1e-9

def generate_perpendicular_children(cp, new_vertex_indices):
    """
    For each interior vertex, generate H/V/D1/D2 creases.
    We try all interior vertices (not just new) because a diagonal may have
    been added *after* an interior vertex existed, enabling D1/D2 creases
    that weren't possible in any ancestor.
    """
    BL=_v(0,0); BR=_v(1,0); TR=_v(1,1); TL=_v(0,1)
    diag_BLTR_present = _edge_exists_between(cp, BL, TR)
    diag_BRTL_present = _edge_exists_between(cp, BR, TL)

    children=[]

    for vi, v in enumerate(cp.vertices):
        # Skip only the four corners of the square — they have no valid
        # H/V crease (those are boundary edges) and D1/D2 at corners
        # are handled by angle_bisector, not perp_through_vertex.
        if _is_corner(v):
            continue

        cx, cy = v.to_cartesian()

        # ── Horizontal crease ─────────────────────────────────────────────
        if not _is_on_boundary_y(cy):
            p2h = Vertex4D(v.x + Fraction(1), v.y, v.z, v.w)
            new_cp=_copy_cp(cp); pvc=len(new_cp.vertices)
            if add_general_crease(new_cp, v, p2h, line_type='m') is not None:
                bpts=_find_crease_boundary_endpoints(new_cp, v, p2h, neighbors_fresh=True)
                if bpts:
                    children.append({
                        'cp':              new_cp,
                        'new_vertex_idxs': set(range(pvc,len(new_cp.vertices))),
                        'refs':            [ref_vertex(v)],
                        'new_crease_v1':   bpts[0],
                        'new_crease_v2':   bpts[1],
                        'function_name':   'perp_through_vertex',
                    })

        # ── Vertical crease ───────────────────────────────────────────────
        if not _is_on_boundary_x(cx):
            p2v = Vertex4D(v.x, v.y, v.z + Fraction(1), v.w)
            new_cp=_copy_cp(cp); pvc=len(new_cp.vertices)
            if add_general_crease(new_cp, v, p2v, line_type='m') is not None:
                bpts=_find_crease_boundary_endpoints(new_cp, v, p2v, neighbors_fresh=True)
                if bpts:
                    children.append({
                        'cp':              new_cp,
                        'new_vertex_idxs': set(range(pvc,len(new_cp.vertices))),
                        'refs':            [ref_vertex(v)],
                        'new_crease_v1':   bpts[0],
                        'new_crease_v2':   bpts[1],
                        'function_name':   'perp_through_vertex',
                    })

        # ── D1: slope +1 (perp to BR→TL diagonal) ────────────────────────
        if diag_BRTL_present:
            p2d1 = Vertex4D(v.x + Fraction(1), v.y, v.z + Fraction(1), v.w)
            new_cp=_copy_cp(cp); pvc=len(new_cp.vertices)
            if add_general_crease(new_cp, v, p2d1, line_type='m') is not None:
                bpts=_find_crease_boundary_endpoints(new_cp, v, p2d1, neighbors_fresh=True)
                if bpts:
                    children.append({
                        'cp':              new_cp,
                        'new_vertex_idxs': set(range(pvc,len(new_cp.vertices))),
                        'refs':            [ref_vertex(v), ref_crease(BR,TL)],
                        'new_crease_v1':   bpts[0],
                        'new_crease_v2':   bpts[1],
                        'function_name':   'perp_through_vertex',
                    })

        # ── D2: slope -1 (perp to BL→TR diagonal) ────────────────────────
        if diag_BLTR_present:
            p2d2 = Vertex4D(v.x + Fraction(1), v.y, v.z - Fraction(1), v.w)
            new_cp=_copy_cp(cp); pvc=len(new_cp.vertices)
            if add_general_crease(new_cp, v, p2d2, line_type='m') is not None:
                bpts=_find_crease_boundary_endpoints(new_cp, v, p2d2, neighbors_fresh=True)
                if bpts:
                    children.append({
                        'cp':              new_cp,
                        'new_vertex_idxs': set(range(pvc,len(new_cp.vertices))),
                        'refs':            [ref_vertex(v), ref_crease(BL,TR)],
                        'new_crease_v1':   bpts[0],
                        'new_crease_v2':   bpts[1],
                        'function_name':   'perp_through_vertex',
                    })

    return children

# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

SCHEMA="""
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS nodes(
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id       INTEGER REFERENCES nodes(id),
    function_name   TEXT    NOT NULL,
    new_crease_v1   BLOB    NOT NULL,
    new_crease_v2   BLOB    NOT NULL,
    refs            TEXT    NOT NULL,
    new_vertex_idxs TEXT    NOT NULL,
    canonical_id    BLOB    NOT NULL,
    depth           INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cp_vertices(
    node_id INTEGER NOT NULL REFERENCES nodes(id),
    xn INTEGER,xd INTEGER,yn INTEGER,yd INTEGER,
    zn INTEGER,zd INTEGER,wn INTEGER,wd INTEGER,
    vertex_index INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cp_edges(
    node_id   INTEGER NOT NULL REFERENCES nodes(id),
    v1_idx    INTEGER NOT NULL,
    v2_idx    INTEGER NOT NULL,
    line_type TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vc ON cp_vertices(xn,xd,yn,yd,zn,zd,wn,wd);
CREATE UNIQUE INDEX IF NOT EXISTS idx_canon ON nodes(canonical_id);
CREATE INDEX IF NOT EXISTS idx_parent ON nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_depth  ON nodes(depth);
"""

# ---------------------------------------------------------------------------
# Tree builder
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Completion notification
# ---------------------------------------------------------------------------

def _notify(message: str):
    """
    Best-effort completion notification.  Tries (in order):
      1. Twilio SMS  — if TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, TWILIO_TO
                       are set as environment variables.
      2. Windows toast notification  — if win10toast is installed.
      3. System beep  — always works.
    """
    import os

    # ── 1. Twilio SMS ──────────────────────────────────────────────────────
    sid   = os.environ.get("TWILIO_SID")
    token = os.environ.get("TWILIO_TOKEN")
    frm   = os.environ.get("TWILIO_FROM")
    to    = os.environ.get("TWILIO_TO")
    if all([sid, token, frm, to]):
        try:
            from twilio.rest import Client
            Client(sid, token).messages.create(body=message, from_=frm, to=to)
            print(f"[notify] SMS sent to {to}")
            return
        except Exception as e:
            print(f"[notify] Twilio failed: {e}")

    # ── 2. Windows toast ───────────────────────────────────────────────────
    try:
        from win10toast import ToastNotifier
        ToastNotifier().show_toast("cp_tree", message, duration=10, threaded=True)
        print("[notify] Windows toast sent")
    except Exception:
        pass

    # ── 3. Beep ────────────────────────────────────────────────────────────
    try:
        import winsound
        for _ in range(3):
            winsound.Beep(1000, 400)
            import time as _t; _t.sleep(0.15)
    except Exception:
        try:
            import subprocess
            subprocess.run(["powershell", "-c",
                "[console]::beep(1000,400);" * 3], capture_output=True)
        except Exception:
            print("\a\a\a", end="", flush=True)   # terminal bell fallback


class CpTreeBuilder:

    def __init__(self, db_path="cp_tree.db", batch_size=50):
        self.db_path    = db_path
        self.batch_size = batch_size
        self._conn      = None

    def _db(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        return self._conn

    def close(self):
        if self._conn: self._conn.close(); self._conn=None

    # ── resume helpers ────────────────────────────────────────────────────

    def _max_depth_in_db(self):
        row = self._db().execute("SELECT MAX(depth) FROM nodes").fetchone()
        return row[0] if row and row[0] is not None else -1

    def _canonical_ids_at_depth(self, depth):
        """All canonical IDs for nodes already in the DB at exactly this depth."""
        return {r[0] for r in self._db().execute(
            "SELECT canonical_id FROM nodes WHERE depth=?", (depth,))}

    # ── insert ────────────────────────────────────────────────────────────

    def _insert_node(self, conn, parent_id, fn_name, ncv1, ncv2,
                     refs, new_vidxs, chash, depth, cp):
        cur = conn.execute(
            "INSERT INTO nodes(parent_id,function_name,new_crease_v1,new_crease_v2,"
            "refs,new_vertex_idxs,canonical_id,depth) VALUES(?,?,?,?,?,?,?,?)",
            (parent_id,fn_name,pack_vertex(ncv1),pack_vertex(ncv2),
             encode_refs(refs),json.dumps(sorted(new_vidxs)),chash,depth))
        nid=cur.lastrowid
        vrows,erows=cp_to_rows(nid,cp)
        conn.executemany("INSERT INTO cp_vertices VALUES(?,?,?,?,?,?,?,?,?,?)",vrows)
        conn.executemany("INSERT INTO cp_edges    VALUES(?,?,?,?)",erows)
        return nid

    # ── main build ────────────────────────────────────────────────────────

    def build(self, max_depth=4, verbose=True, max_db_mb=None):
        import time
        conn   = self._db()
        zero_v = _v(0,0)

        # ── ensure root exists ────────────────────────────────────────────
        root_cp   = make_root_cp()
        root_hash = canonical_hash(canonicalize_centered(root_cp))
        row = conn.execute("SELECT id FROM nodes WHERE canonical_id=?",(root_hash,)).fetchone()
        if row is None:
            with conn:
                rid=self._insert_node(conn,None,'root',zero_v,zero_v,[],
                                      set(range(len(root_cp.vertices))),root_hash,0,root_cp)
            if verbose: print(f"[depth 0] inserted root (id={rid})")
        else:
            if verbose: print(f"[depth 0] root present (id={row[0]})")

        # ── find resume point ─────────────────────────────────────────────
        start_depth = self._max_depth_in_db() + 1
        if start_depth > max_depth:
            total = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            if verbose: print(f"DB already at depth {start_depth-1} >= requested {max_depth}. "
                              f"Total nodes: {total:,}. Nothing to do.")
            return

        seen: set[bytes] = {r[0] for r in conn.execute("SELECT canonical_id FROM nodes")}
        build_start = time.perf_counter()

        for depth in range(start_depth, max_depth+1):
            depth_start = time.perf_counter()
            parent_ids=[r[0] for r in conn.execute(
                "SELECT id FROM nodes WHERE depth=?",(depth-1,))]

            total_before = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            if verbose:
                print(f"\n[depth {depth}] expanding {len(parent_ids):,} parents  "
                      f"(tree has {total_before:,} nodes so far)")

            inserted=skipped=candidates=0
            fn_candidates={}; fn_inserted={}; fn_deduped={}
            pending=[]
            last_report = time.perf_counter()

            for p_num, pid in enumerate(parent_ids):
                pcp,pnew,pncl=self._load_cp(conn,pid)
                children = generate_all_children(pcp,pnew,pncl,depth=depth-1)
                candidates += len(children)

                for child in children:
                    fn = child['function_name']
                    fn_candidates[fn] = fn_candidates.get(fn,0) + 1
                    chash=canonical_hash(canonicalize_centered(child['cp']))
                    if chash in seen:
                        fn_deduped[fn] = fn_deduped.get(fn,0) + 1
                        skipped+=1; continue
                    fn_inserted[fn] = fn_inserted.get(fn,0) + 1
                    seen.add(chash)
                    pending.append((pid,child,chash,depth))
                    if len(pending)>=self.batch_size:
                        inserted+=self._flush(conn,pending); pending.clear()

                # Progress report every 5 seconds
                now = time.perf_counter()
                if verbose and (now - last_report) >= 5.0:
                    elapsed = now - depth_start
                    rate = (p_num + 1) / elapsed
                    remaining = (len(parent_ids) - p_num - 1) / rate if rate > 0 else 0
                    print(f"  {p_num+1:,}/{len(parent_ids):,} parents done  "
                          f"| {inserted:,} inserted  {skipped:,} deduped  "
                          f"| {rate:.1f} parents/s  "
                          f"| ETA {remaining:.0f}s")
                    last_report = now

            if pending: inserted+=self._flush(conn,pending)

            depth_elapsed = time.perf_counter() - depth_start
            total_after = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            unique_verts = conn.execute(
                "SELECT COUNT(*) FROM ("
                "  SELECT DISTINCT xn,xd,yn,yd,zn,zd,wn,wd FROM cp_vertices"
                ")"
            ).fetchone()[0]

            # File size
            import os as _os
            db_mb = _os.path.getsize(self.db_path) / (1024*1024)

            if verbose:
                print(f"[depth {depth}] done in {depth_elapsed:.1f}s  "
                      f"| candidates={candidates:,}  inserted={inserted:,}  deduped={skipped:,}  "
                      f"| {total_after:,} nodes  {unique_verts:,} unique verts  "
                      f"| DB {db_mb:.1f} MB")
                all_fns = sorted(set(list(fn_candidates)+list(fn_inserted)+list(fn_deduped)))
                for fn in all_fns:
                    c=fn_candidates.get(fn,0); i=fn_inserted.get(fn,0); d=fn_deduped.get(fn,0)
                    pct=f"{100*i/c:.0f}%" if c else '-'
                    print(f"    {fn:<28} candidates={c:>6,}  inserted={i:>6,}  deduped={d:>6,}  yield={pct}")

            if inserted==0:
                if verbose: print("  → complete."); break

            if max_db_mb is not None and db_mb >= max_db_mb:
                if verbose: print(f"  → DB size limit {max_db_mb} MB reached ({db_mb:.1f} MB). Stopping.")
                break

        total_elapsed = time.perf_counter() - build_start
        total = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        if verbose:
            print(f"\nBuild finished in {total_elapsed:.1f}s  |  {total:,} total nodes")
        _notify(f"cp_tree done: {total:,} nodes in {total_elapsed:.0f}s")

    def _flush(self,conn,pending):
        with conn:
            for pid,child,chash,depth in pending:
                self._insert_node(conn,pid,
                    child['function_name'],
                    child['new_crease_v1'],child['new_crease_v2'],
                    child['refs'],child['new_vertex_idxs'],
                    chash,depth,child['cp'])
        return len(pending)

    def _load_cp(self,conn,node_id):
        row=conn.execute(
            "SELECT new_vertex_idxs,new_crease_v1,new_crease_v2,function_name"
            " FROM nodes WHERE id=?",(node_id,)).fetchone()
        nvi=set(json.loads(row[0]))
        fn=row[3]
        if fn=='root':
            new_crease_line=None
        else:
            new_crease_line=(unpack_vertex(row[1]), unpack_vertex(row[2]))
        vrows=conn.execute(
            "SELECT node_id,xn,xd,yn,yd,zn,zd,wn,wd,vertex_index"
            " FROM cp_vertices WHERE node_id=? ORDER BY vertex_index",(node_id,)).fetchall()
        erows=conn.execute(
            "SELECT node_id,v1_idx,v2_idx,line_type FROM cp_edges WHERE node_id=?",(node_id,)).fetchall()
        return rows_to_cp(vrows,erows),nvi,new_crease_line

# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def find_nodes_with_vertex(conn,v,max_depth=None):
    q=("SELECT n.id,n.parent_id,n.depth,n.canonical_id"
       " FROM cp_vertices cv JOIN nodes n ON n.id=cv.node_id"
       " WHERE cv.xn=? AND cv.xd=? AND cv.yn=? AND cv.yd=?"
       "   AND cv.zn=? AND cv.zd=? AND cv.wn=? AND cv.wd=?")
    p=[v.x.num,v.x.den,v.y.num,v.y.den,v.z.num,v.z.den,v.w.num,v.w.den]
    if max_depth is not None: q+=" AND n.depth<=?"; p.append(max_depth)
    q+=" ORDER BY n.depth ASC"
    return [{"id":r[0],"parent_id":r[1],"depth":r[2],"canonical_id":r[3]}
            for r in conn.execute(q,p)]

def load_cp_by_node_id(conn,node_id):
    vrows=conn.execute(
        "SELECT node_id,xn,xd,yn,yd,zn,zd,wn,wd,vertex_index"
        " FROM cp_vertices WHERE node_id=? ORDER BY vertex_index",(node_id,)).fetchall()
    erows=conn.execute(
        "SELECT node_id,v1_idx,v2_idx,line_type FROM cp_edges WHERE node_id=?",(node_id,)).fetchall()
    return rows_to_cp(vrows,erows)

def get_ancestry_chain(conn,node_id):
    chain=[]; cur=node_id
    while cur is not None:
        row=conn.execute(
            "SELECT id,parent_id,function_name,new_crease_v1,new_crease_v2,"
            "refs,depth,canonical_id FROM nodes WHERE id=?",(cur,)).fetchone()
        if row is None: break
        chain.append({"id":row[0],"parent_id":row[1],"function_name":row[2],
                      "new_crease_v1":unpack_vertex(row[3]),
                      "new_crease_v2":unpack_vertex(row[4]),
                      "refs":decode_refs(row[5]),"depth":row[6],"canonical_id":row[7]})
        cur=row[1]
    chain.reverse(); return chain

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__=="__main__":
    import sys
    args=sys.argv[1:]

    # Usage:
    #   python cp_tree.py [max_depth] [db_path]
    #   python cp_tree.py fresh [max_depth] [db_path]
    fresh = False
    if args and args[0]=="fresh":
        fresh=True; args=args[1:]

    max_depth=int(args[0]) if args else 4
    db_path=args[1] if len(args)>1 else "cp_tree.db"

    if fresh:
        import os
        if os.path.exists(db_path):
            os.remove(db_path)
            print(f"Cleared {db_path}")

    import os as _os
    max_mb = float(sys.argv[3]) if len(sys.argv) > 3 else None
    builder=CpTreeBuilder(db_path)
    try: builder.build(max_depth=max_depth,verbose=True,max_db_mb=max_mb)
    finally: builder.close()