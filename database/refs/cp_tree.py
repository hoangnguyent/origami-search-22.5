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
from math import gcd
import multiprocessing as _mp
_mp.freeze_support()  # required on Windows
import sys
import os 
import time

import sqlite3, hashlib, json, struct, itertools
from typing import Optional

from src.engine.math225_core import Vertex4D, Fraction, AplusBsqrt2
from src.engine.cp225 import Cp225, vertex_on_border
from database.refs.cp_general_crease import (
    add_general_crease,
    _vertex_on_infinite_line,
    vertex4d_to_aplusbsqrt2_xy,
    aplusbsqrt2_xy_to_vertex4d,
    point_on_segment_exact,
)


# ---------------------------------------------------------------------------
# Vertex4D pack / unpack
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Z[√2] / [p,q,d] compact format  (6 ints per vertex, always reduced)
# cx = px/dx + qx/dx*sqrt(2),  cy = py/dy + qy/dy*sqrt(2)
# ---------------------------------------------------------------------------

def _z2_gcd(a,b):
    a,b=abs(int(a)),abs(int(b))
    while b: a,b=b,a%b
    return a or 1

def _z2_reduce(p,q,d):
    p,q,d=int(p),int(q),int(d)
    if d<0: p,q,d=-p,-q,-d
    if p==0 and q==0: return (0,0,1)
    g=_z2_gcd(_z2_gcd(abs(p),abs(q)),d)
    return (p//g,q//g,d//g)

def v4d_to_z2(v):
    """Vertex4D -> (px,qx,dx, py,qy,dy) reduced.
    
    Uses Python's built-in Fraction reduction to keep numbers small,
    then converts to the z2 [p,q,d] form.
    cx = Ax + Bx*sqrt(2)  where Ax=v.x (Fraction), Bx=(v.y-v.w)/2 (Fraction)
    cy = Ay + By*sqrt(2)  where Ay=v.z (Fraction), By=(v.y+v.w)/2 (Fraction)
    """
    # Get fully reduced Fractions first
    Ax = v.x          # already a reduced Fraction
    Bx = (v.y - v.w) * Fraction(1, 2)   # Fraction arithmetic keeps it reduced
    Ay = v.z
    By = (v.y + v.w) * Fraction(1, 2)

    # Convert pair (A, B) of Fractions to [p,q,d] with common denominator
    # d = lcm(Ax.den, Bx.den),  p = Ax.num*(d/Ax.den),  q = Bx.num*(d/Bx.den)
    def fracs_to_z2(A, B):
        # Reduce each Fraction first (they should already be reduced,
        # but be defensive)
        an, ad = int(A.num), int(A.den)
        bn, bd = int(B.num), int(B.den)
        g = gcd(abs(an), ad); an //= g; ad //= g
        g = gcd(abs(bn), bd); bn //= g; bd //= g
        # Common denominator via lcm, but reduce first to keep small
        g2 = gcd(ad, bd)
        d = ad * (bd // g2)
        p = an * (d // ad)
        q = bn * (d // bd)
        return _z2_reduce(p, q, d)

    return fracs_to_z2(Ax, Bx) + fracs_to_z2(Ay, By)

def z2_to_v4d(px,qx,dx,py,qy,dy):
    """(px,qx,dx, py,qy,dy) -> Vertex4D."""
    # math225_core.Fraction requires int64 range
    INT64 = (1<<63)-1
    for n in (px,qx,dx,py,qy,dy):
        if abs(int(n)) > INT64:
            raise OverflowError(f'z2 component {n} exceeds int64 — vertex too deep')
    x=Fraction(int(px),int(dx)); z=Fraction(int(py),int(dy))
    y=Fraction(int(qx),int(dx))+Fraction(int(qy),int(dy))
    w=Fraction(int(qy),int(dy))-Fraction(int(qx),int(dx))
    return Vertex4D(x,y,z,w)

def pack_vertex(v):
    """Pack a single Vertex4D as variable-length blob."""
    out = b''
    for n in v4d_to_z2(v):
        out += _pack_int(n)
    return out

def unpack_vertex(b):
    """Unpack a single Vertex4D from variable-length blob."""
    offset = 0
    z2 = []
    for _ in range(6):
        val, offset = _unpack_int(b, offset)
        z2.append(val)
    return z2_to_v4d(*z2)

def vertex_to_list(v):
    return list(v4d_to_z2(v))

def list_to_vertex(lst):
    return z2_to_v4d(*lst)

# Blob packing: vertices as 4-byte count + N*48-byte z2 tuples
#               edges as 4-byte count + N*9-byte (int32,int32,uint8)
_LT_ENC={'b':0,'m':1,'v':2,'a':3}; _LT_DEC={0:'b',1:'m',2:'v',3:'a'}

def _pack_int(n):
    """Pack a Python int as 2-byte length + big-endian signed bytes."""
    n = int(n)
    if n == 0:
        return b'\x00\x01\x00'
    byte_len = (n.bit_length() + 8) // 8  # +8 for sign bit headroom
    nb = n.to_bytes(byte_len, 'big', signed=True)
    return len(nb).to_bytes(2, 'big') + nb

def _unpack_int(b, offset):
    """Unpack a variable-length int from b at offset. Returns (value, new_offset)."""
    byte_len = int.from_bytes(b[offset:offset+2], 'big')
    val = int.from_bytes(b[offset+2:offset+2+byte_len], 'big', signed=True)
    return val, offset+2+byte_len

def _pack_vertices(verts):
    out = struct.pack(">I", len(verts))
    for v in verts:
        for n in v4d_to_z2(v):
            out += _pack_int(n)
    return out

def _unpack_vertices(b):
    n = struct.unpack(">I", b[:4])[0]
    offset = 4
    verts = []
    for _ in range(n):
        z2 = []
        for _ in range(6):
            val, offset = _unpack_int(b, offset)
            z2.append(val)
        verts.append(z2_to_v4d(*z2))
    return verts

def _pack_edges(edges):
    out=struct.pack(">I",len(edges))
    for v1,v2,lt in edges: out+=struct.pack(">IIB",v1,v2,_LT_ENC.get(lt,1))
    return out

def _unpack_edges(b):
    n=struct.unpack(">I",b[:4])[0]
    return [(struct.unpack(">II",b[4+i*9:4+i*9+8])+((_LT_DEC.get(b[4+i*9+8],'m')),)) for i in range(n)]

def cp_to_blobs(cp):
    return _pack_vertices(cp.vertices), _pack_edges(cp.edges)

def blobs_to_cp(vb,eb):
    return Cp225(_unpack_vertices(vb),_unpack_edges(eb))

# ---------------------------------------------------------------------------
# Fast canonical hash — pure integer D4, no Cp225 copies, no freeze()
# ---------------------------------------------------------------------------

def _z2_oneminus(p,q,d): return _z2_reduce(d-p,-q,d)  # [1,0,1] - [p,q,d]

def fast_canonical_hash(cp):
    """SHA-256 of the lexicographically minimal D4 transform. ~10x faster than canonicalize()."""
    vz2=[v4d_to_z2(v) for v in cp.vertices]
    edges=cp.edges
    best=None
    for sym in range(8):
        def t(px,qx,dx,py,qy,dy,s=sym):
            x=(px,qx,dx); y=(py,qy,dy)
            ox=_z2_oneminus(*x); oy=_z2_oneminus(*y)
            return [x+y,oy+x,ox+oy,y+ox,x+oy,ox+y,y+x,oy+ox][s]
        tv=[t(*z) for z in vz2]
        order=sorted(range(len(tv)),key=lambda i:tv[i])
        remap=[0]*len(tv)
        for ni,oi in enumerate(order): remap[oi]=ni
        sv=tuple(tv[i] for i in order)
        se=tuple(sorted((min(remap[v1],remap[v2]),max(remap[v1],remap[v2]),lt) for v1,v2,lt in edges))
        key=(sv,se)
        if best is None or key<best: best=key
    return hashlib.sha256(repr(best).encode()).digest()

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
    # edges = [(0,1,'b'),(1,2,'b'),(2,3,'b'),(3,0,'b')]
    edges = [
        (0,1,'b'), (1,2,'b'), (2,3,'b'), (3,0,'b'),
        (0,2,'m'), (1,3,'m') # BL-TR and BR-TL diagonals
    ]
    return Cp225(verts,edges)

# ---------------------------------------------------------------------------
# CP serialization
# ---------------------------------------------------------------------------

# cp_to_rows/rows_to_cp replaced by cp_to_blobs/blobs_to_cp above

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

# def _aplusbsqrt2_key(a):
#     """Hashable key for an AplusBsqrt2 value: (num, den, num, den) tuple."""
#     return (a.A.num, a.A.den, a.B.num, a.B.den)

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
    Uses exact AplusBsqrt2 arithmetic.
    """
    ax, ay = vertex4d_to_aplusbsqrt2_xy(v1)
    bx, by = vertex4d_to_aplusbsqrt2_xy(v2)
    dx = bx - ax
    dy = by - ay
    if dy == 0:              return 'H'   # horizontal
    if dx == 0:              return 'V'   # vertical
    if dx - dy == 0:         return 'D+'  # slope +1
    if dx + dy == 0:         return 'D-'  # slope -1
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
PRAGMA page_size=8192;
CREATE TABLE IF NOT EXISTS nodes(
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id       INTEGER REFERENCES nodes(id),
    function_name   TEXT    NOT NULL,
    new_crease_v1   BLOB    NOT NULL,
    new_crease_v2   BLOB    NOT NULL,
    refs            TEXT    NOT NULL,
    new_vertex_idxs TEXT    NOT NULL,
    canonical_id    BLOB    NOT NULL,
    depth           INTEGER NOT NULL,
    vertices_blob   BLOB    NOT NULL,
    edges_blob      BLOB    NOT NULL
);
CREATE TABLE IF NOT EXISTS vertex_index(
    node_id INTEGER NOT NULL,
    px INTEGER, qx INTEGER, dx INTEGER,
    py INTEGER, qy INTEGER, dy INTEGER,
    depth   INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_canon  ON nodes(canonical_id);
CREATE INDEX IF NOT EXISTS idx_parent        ON nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_depth         ON nodes(depth);
CREATE INDEX IF NOT EXISTS idx_vi_coords     ON vertex_index(px,qx,dx,py,qy,dy);
CREATE INDEX IF NOT EXISTS idx_vi_node       ON vertex_index(node_id);
"""

# ---------------------------------------------------------------------------
# Multiprocessing worker
# ---------------------------------------------------------------------------

def _expand_parent_worker(args):
    """
    Worker function: deserialize a parent CP, generate all children,
    serialize children back to blobs.  Runs in a subprocess — no DB access,
    no shared state.

    args: (pid, vblob, eblob, new_vertex_idxs_json, new_crease_blob_or_none,
           new_crease2_blob_or_none, fn_name, depth)
    returns: list of serialized child dicts
    """
    (pid, vblob, eblob, nvi_json,
     ncv1_blob, ncv2_blob, fn_name, depth) = args

    cp = blobs_to_cp(vblob, eblob)
    nvi = set(json.loads(nvi_json))

    if fn_name == 'root' or ncv1_blob is None:
        ncl = None
    else:
        ncl = (unpack_vertex(ncv1_blob), unpack_vertex(ncv2_blob))

    children = generate_all_children(cp, nvi, ncl, depth=depth)

    # Serialize children — return only what main process needs
    out = []
    for child in children:
        try:
            # Compute z2 tuples for all vertices — catches overflow early
            cp = child['cp']
            vertex_z2_list = [v4d_to_z2(v) for v in cp.vertices]
            # Validate all fit in int64
            INT64 = (1<<63)-1
            for z2 in vertex_z2_list:
                for n in z2:
                    if abs(int(n)) > INT64:
                        raise OverflowError('int64 overflow')
            vb, eb = cp_to_blobs(cp)
            chash = fast_canonical_hash(cp)
            ncv1 = pack_vertex(child['new_crease_v1'])
            ncv2 = pack_vertex(child['new_crease_v2'])
        except (OverflowError, struct.error):
            continue  # vertex coordinates exceed int64 — skip this child
        out.append({
            'parent_id':        pid,
            'function_name':    child['function_name'],
            'new_crease_v1':    ncv1,
            'new_crease_v2':    ncv2,
            'refs':             encode_refs(child['refs']),
            'new_vertex_idxs':  json.dumps(sorted(child['new_vertex_idxs'])),
            'chash':            chash,
            'depth':            depth + 1,
            'vertices_blob':    vb,
            'edges_blob':       eb,
            'vertex_z2_list':   vertex_z2_list,
        })
    return out


class CpTreeBuilder:

    def __init__(self, db_path="cp_tree.db", batch_size=50, n_workers=None):
        self.db_path    = db_path
        self.batch_size = batch_size
        self.n_workers  = n_workers   # None = use os.cpu_count()
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
        vblob,eblob = cp_to_blobs(cp)
        cur = conn.execute(
            "INSERT INTO nodes(parent_id,function_name,new_crease_v1,new_crease_v2,"
            "refs,new_vertex_idxs,canonical_id,depth,vertices_blob,edges_blob)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (parent_id,fn_name,pack_vertex(ncv1),pack_vertex(ncv2),
             encode_refs(refs),json.dumps(sorted(new_vidxs)),chash,depth,vblob,eblob))
        nid=cur.lastrowid
        vi_rows=[(nid,*v4d_to_z2(v),depth) for v in cp.vertices]
        conn.executemany("INSERT INTO vertex_index VALUES(?,?,?,?,?,?,?,?)",vi_rows)
        return nid

    # ── main build ────────────────────────────────────────────────────────

    def build(self, max_depth=4, verbose=True, max_db_mb=None, resume_from=None, skip_parents=0):
        conn   = self._db()
        zero_v = _v(0,0)

        # ── ensure root exists ────────────────────────────────────────────
        root_cp   = make_root_cp()
        root_hash = fast_canonical_hash(root_cp)
        row = conn.execute("SELECT id FROM nodes WHERE canonical_id=?",(root_hash,)).fetchone()
        if row is None:
            with conn:
                rid=self._insert_node(conn,None,'root',zero_v,zero_v,[],
                                      set(range(len(root_cp.vertices))),root_hash,0,root_cp)
            if verbose: print(f"[depth 0] inserted root (id={rid})")
        else:
            if verbose: print(f"[depth 0] root present (id={row[0]})")

        # ── find resume point ─────────────────────────────────────────────
        if resume_from is not None:
            start_depth = resume_from
            if verbose: print(f"Resuming from depth {start_depth} (forced)")
        else:
            start_depth = self._max_depth_in_db() + 1
        if start_depth > max_depth:
            total = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            if verbose: print(f"DB already at depth {start_depth-1} >= requested {max_depth}. "
                              f"Total nodes: {total:,}. Nothing to do.")
            return

        seen: set[bytes] = {r[0] for r in conn.execute("SELECT canonical_id FROM nodes")}
        build_start = time.perf_counter()

        for depth in range(start_depth, max_depth+1):
            print(f"[depth {depth}]")
            depth_start = time.perf_counter()
            parent_ids=[r[0] for r in conn.execute(
                "SELECT id FROM nodes WHERE depth=?",(depth-1,))]
            if skip_parents > 0 and depth == resume_from:
                if verbose: print(f"  Skipping first {skip_parents:,} parents")
                parent_ids = parent_ids[skip_parents:]

            total_before = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            if verbose:
                print(f"\n[depth {depth}] expanding {len(parent_ids):,} parents  "
                      f"(tree has {total_before:,} nodes so far)")

            inserted=skipped=candidates=0
            fn_candidates={}; fn_inserted={}; fn_deduped={}; hash_time={}
            pending=[]
            last_report = time.perf_counter()

            # Build worker args: serialize each parent to blobs
            worker_args = []
            for pid in parent_ids:
                row = conn.execute(
                    "SELECT new_vertex_idxs,new_crease_v1,new_crease_v2,"
                    "function_name,vertices_blob,edges_blob FROM nodes WHERE id=?",
                    (pid,)).fetchone()
                worker_args.append((
                    pid, row[4], row[5], row[0],
                    row[1], row[2], row[3], depth-1
                ))

            n_workers = self.n_workers or os.cpu_count() or 1
            # Use multiprocessing only when it's worth the spawn overhead
            # use_mp = n_workers > 1 and len(worker_args) >= n_workers * 2
            use_mp = False 
            def _process_results(results_iter):
                nonlocal inserted, skipped, candidates, last_report, p_num
                for batch_results in results_iter:
                    p_num += 1
                    candidates += len(batch_results)
                    for child in batch_results:
                        fn = child['function_name']
                        fn_candidates[fn] = fn_candidates.get(fn,0) + 1
                        chash = child['chash']
                        if chash in seen:
                            fn_deduped[fn] = fn_deduped.get(fn,0) + 1
                            skipped+=1; continue
                        fn_inserted[fn] = fn_inserted.get(fn,0) + 1
                        seen.add(chash)
                        pending.append(child)
                        if len(pending)>=self.batch_size:
                            inserted+=self._flush_serialized(conn,pending); pending.clear()
                    now = time.perf_counter()
                    if verbose and (now - last_report) >= 5.0:
                        elapsed = now - depth_start
                        rate = p_num / max(elapsed,0.001)
                        remaining = (len(parent_ids)-p_num)/rate if rate>0 else 0
                        print(f"  {p_num:,}/{len(parent_ids):,} parents done  "
                              f"| {inserted:,} inserted  {skipped:,} deduped  "
                              f"| {rate:.1f} parents/s  "
                              f"| ETA {remaining:.0f}s")
                        last_report = now

            p_num = 0
            # if use_mp:
            #     CHUNK = max(1, min(50, len(worker_args) // (n_workers * 4)))
            #     with _mp.Pool(processes=n_workers) as pool:
            #         _process_results(pool.imap_unordered(
            #             _expand_parent_worker, worker_args, chunksize=CHUNK))
            # else:
            _process_results(map(_expand_parent_worker, worker_args))

            if pending: inserted+=self._flush_serialized(conn,pending)

            depth_elapsed = time.perf_counter() - depth_start
            total_after = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            unique_verts = conn.execute(
                "SELECT COUNT(*) FROM ("
                "  SELECT DISTINCT px,qx,dx,py,qy,dy FROM vertex_index"
                ")"
            ).fetchone()[0]

            # File size
            db_mb = os.path.getsize(self.db_path) / (1024*1024)

            if verbose:
                print(f"[depth {depth}] done in {depth_elapsed:.1f}s  "
                      f"| candidates={candidates:,}  inserted={inserted:,}  deduped={skipped:,}  "
                      f"| {total_after:,} nodes  {unique_verts:,} unique verts  "
                      f"| DB {db_mb:.1f} MB")
                total_hash=sum(hash_time.values())
                print(f"  hash time: {total_hash:.2f}s of {depth_elapsed:.2f}s = {100*total_hash/max(depth_elapsed,0.001):.0f}%")
                all_fns = sorted(set(list(fn_candidates)+list(fn_inserted)+list(fn_deduped)))
                for fn in all_fns:
                    c=fn_candidates.get(fn,0); i=fn_inserted.get(fn,0); d=fn_deduped.get(fn,0)
                    pct=f"{100*i/c:.0f}%" if c else '-'
                    print(f"    {fn:<28} candidates={c:>6,}  inserted={i:>6,}  deduped={d:>6,}  yield={pct}  hash={hash_time.get(fn,0):.2f}s")

            # if inserted==0:
            #     if verbose: print("  → complete."); break

            # if max_db_mb is not None and db_mb >= max_db_mb:
            #     if verbose: print(f"  → DB size limit {max_db_mb} MB reached ({db_mb:.1f} MB). Stopping.")
            #     break

        total_elapsed = time.perf_counter() - build_start
        total = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        if verbose:
            print(f"\nBuild finished in {total_elapsed:.1f}s  |  {total:,} total nodes")
        # _notify(f"cp_tree done: {total:,} nodes in {total_elapsed:.0f}s")

    def _flush(self,conn,pending):
        with conn:
            for pid,child,chash,depth in pending:
                self._insert_node(conn,pid,
                    child['function_name'],
                    child['new_crease_v1'],child['new_crease_v2'],
                    child['refs'],child['new_vertex_idxs'],
                    chash,depth,child['cp'])
        return len(pending)

    def _flush_serialized(self,conn,pending):
        """
        Flush pre-serialized child dicts from worker processes.
        Reads vertex z2 tuples directly from the blob — never reconstructs
        Vertex4D objects, so no int64 overflow risk here.
        """
        with conn:
            for child in pending:
                vblob = child['vertices_blob']
                eblob = child['edges_blob']
                cur = conn.execute(
                    "INSERT INTO nodes(parent_id,function_name,"
                    "new_crease_v1,new_crease_v2,refs,new_vertex_idxs,"
                    "canonical_id,depth,vertices_blob,edges_blob)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (child['parent_id'], child['function_name'],
                     child['new_crease_v1'], child['new_crease_v2'],
                     child['refs'], child['new_vertex_idxs'],
                     child['chash'], child['depth'],
                     vblob, eblob))
                nid = cur.lastrowid
                # Extract z2 tuples directly from blob without Vertex4D
                vi_rows = [(nid, *z2, child['depth'])
                           for z2 in child['vertex_z2_list']]
                conn.executemany(
                    "INSERT INTO vertex_index VALUES(?,?,?,?,?,?,?,?)",
                    vi_rows)
        return len(pending)

    def _load_cp(self,conn,node_id):
        row=conn.execute(
            "SELECT new_vertex_idxs,new_crease_v1,new_crease_v2,function_name,"
            "vertices_blob,edges_blob FROM nodes WHERE id=?",(node_id,)).fetchone()
        nvi=set(json.loads(row[0]))
        fn=row[3]
        new_crease_line=None if fn=='root' else (unpack_vertex(row[1]),unpack_vertex(row[2]))
        return blobs_to_cp(row[4],row[5]),nvi,new_crease_line

# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def find_nodes_with_vertex(conn,v,max_depth=None):
    px,qx,dx,py,qy,dy = v4d_to_z2(v)
    q=("SELECT vi.node_id,n.parent_id,n.depth,n.canonical_id"
       " FROM vertex_index vi JOIN nodes n ON n.id=vi.node_id"
       " WHERE vi.px=? AND vi.qx=? AND vi.dx=?"
       "   AND vi.py=? AND vi.qy=? AND vi.dy=?")
    p=[px,qx,dx,py,qy,dy]
    if max_depth is not None: q+=" AND vi.depth<=?"; p.append(max_depth)
    q+=" ORDER BY n.depth ASC"
    return [{"id":r[0],"parent_id":r[1],"depth":r[2],"canonical_id":r[3]}
            for r in conn.execute(q,p)]

def load_cp_by_node_id(conn,node_id):
    row=conn.execute("SELECT vertices_blob,edges_blob FROM nodes WHERE id=?",(node_id,)).fetchone()
    return blobs_to_cp(row[0],row[1])

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
    args=sys.argv[1:]

    # Usage:
    #   python cp_tree.py [max_depth] [db_path]
    #   python cp_tree.py fresh [max_depth] [db_path]
    fresh = False
    if args and args[0]=="fresh":
        fresh=True; args=args[1:]

    max_depth=int(args[0]) if args else 4
    db_path=args[1] if len(args)>1 else "cp_tree.db"

    if fresh and os.path.exists(db_path):
        os.remove(db_path)
        print(f"Cleared {db_path}")

    max_mb = float(sys.argv[3]) if len(sys.argv) > 3 else None
    resume_from = int(sys.argv[4]) if len(sys.argv) > 4 else None
    skip_parents = int(sys.argv[5]) if len(sys.argv) > 5 else 0
    builder=CpTreeBuilder(db_path)
    try: builder.build(max_depth=max_depth,verbose=True,max_db_mb=max_mb,resume_from=resume_from,skip_parents=skip_parents)
    finally: builder.close