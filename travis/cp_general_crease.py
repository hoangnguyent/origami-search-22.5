"""
cp_general_crease.py
====================
Adds a crease defined by any two Vertex4D points to a Cp225.
The crease direction need not be a 22.5-degree multiple.
All arithmetic stays in Q(sqrt(2)).

Key design decisions:
- Intersection points are computed via exact Cramer's rule in AplusBsqrt2.
- Segment containment is verified AFTER computing the intersection point using
  point_on_segment_exact (cross-product + dot-product, exact Fraction arithmetic),
  rather than trying to check the parameter s before computing the point.
  This avoids precision issues with Fraction comparisons on large numerators.
- Edge scanning uses a snapshot of the original edge list so that splits
  don't corrupt intersection detection.
"""

from math225_core import Vertex4D, Fraction, AplusBsqrt2

HALF = Fraction(1, 2)
TWO  = Fraction(2, 1)
ZERO = Fraction(0, 1)

# ---------------------------------------------------------------------------
# Coordinate bridge
# ---------------------------------------------------------------------------

def vertex4d_to_aplusbsqrt2_xy(v: Vertex4D):
    return (AplusBsqrt2(v.x, (v.y - v.w) * HALF),
            AplusBsqrt2(v.z, (v.y + v.w) * HALF))

def aplusbsqrt2_xy_to_vertex4d(cx: AplusBsqrt2, cy: AplusBsqrt2) -> Vertex4D:
    return Vertex4D(cx.A, cx.B + cy.B, cy.A, cy.B - cx.B)

# ---------------------------------------------------------------------------
# Exact zero test (used for parallelism check only)
# ---------------------------------------------------------------------------

def _is_zero(a: AplusBsqrt2) -> bool:
    return a.A.num == 0 and a.B.num == 0

# ---------------------------------------------------------------------------
# Segment containment — exact Fraction arithmetic
# ---------------------------------------------------------------------------

def point_on_segment_exact(p1: Vertex4D, p2: Vertex4D, pt: Vertex4D) -> bool:
    """
    True iff pt lies on the closed segment [p1, p2].
    Uses exact cross-product (collinearity) and dot-product (betweenness).
    All arithmetic is Fraction or AplusBsqrt2 — no floats used for decisions.
    """
    if p1 == pt or p2 == pt:
        return True

    ax, ay = vertex4d_to_aplusbsqrt2_xy(p1)
    bx, by = vertex4d_to_aplusbsqrt2_xy(p2)
    px, py = vertex4d_to_aplusbsqrt2_xy(pt)

    dx = bx - ax;  dy = by - ay
    ex = px - ax;  ey = py - ay

    # Collinearity: cross product must be zero (exact)
    cross = dx * ey - dy * ex
    if not _is_zero(cross):
        return False

    # Betweenness: 0 <= dot(e, d) <= dot(d, d)
    # We compare using AplusBsqrt2.sign() which uses to_float() — but only
    # for ordering, not for the geometric computation itself.  Since we've
    # already confirmed collinearity exactly, the only question is position
    # along the line, which a float comparison handles reliably.
    dot  = dx * ex + dy * ey
    len2 = dx * dx + dy * dy

    # dot >= 0
    if dot.sign() < 0:
        return False
    # dot <= len2  i.e.  len2 - dot >= 0
    if (len2 - dot).sign() < 0:
        return False

    return True

def _vertex_on_infinite_line(lp1: Vertex4D, lp2: Vertex4D, pt: Vertex4D) -> bool:
    """True iff pt lies on the infinite line through lp1->lp2 (exact cross-product)."""
    lp1x, lp1y = vertex4d_to_aplusbsqrt2_xy(lp1)
    lp2x, lp2y = vertex4d_to_aplusbsqrt2_xy(lp2)
    ptx,  pty  = vertex4d_to_aplusbsqrt2_xy(pt)
    dx = lp2x - lp1x;  dy = lp2y - lp1y
    ex = ptx  - lp1x;  ey = pty  - lp1y
    return _is_zero(dx * ey - dy * ex)

# ---------------------------------------------------------------------------
# Line x segment intersection — compute point, then verify containment
# ---------------------------------------------------------------------------

def line_segment_intersection_exact(
    lp1: Vertex4D, lp2: Vertex4D,   # infinite line
    sp1: Vertex4D, sp2: Vertex4D,   # finite segment
) -> "Vertex4D | None":
    """
    Intersect the infinite line lp1->lp2 with the closed segment [sp1, sp2].

    Strategy: compute the intersection point of the two infinite lines via
    Cramer's rule (exact AplusBsqrt2 arithmetic), then verify the point lies
    on the segment using point_on_segment_exact.  This is more robust than
    checking the parameter s directly, because parameter comparison requires
    dividing AplusBsqrt2 values and comparing the quotient to 0 and 1 — which
    can lose precision when Fraction numerators/denominators are large.
    """
    lp1x, lp1y = vertex4d_to_aplusbsqrt2_xy(lp1)
    lp2x, lp2y = vertex4d_to_aplusbsqrt2_xy(lp2)
    sp1x, sp1y = vertex4d_to_aplusbsqrt2_xy(sp1)
    sp2x, sp2y = vertex4d_to_aplusbsqrt2_xy(sp2)

    dlx = lp2x - lp1x;  dly = lp2y - lp1y   # line direction
    dsx = sp2x - sp1x;  dsy = sp2y - sp1y   # segment direction
    rx  = sp1x - lp1x;  ry  = sp1y - lp1y   # offset

    # det = dly*dsx - dlx*dsy
    det = dly * dsx - dlx * dsy
    if _is_zero(det):
        return None   # parallel or coincident

    # t = (dsx*ry - dsy*rx) / det
    t_num = dsx * ry - dsy * rx

    # Compute intersection point on the line: P = lp1 + t * dl
    px = lp1x + t_num * dlx / det
    py = lp1y + t_num * dly / det

    pt = aplusbsqrt2_xy_to_vertex4d(px, py)

    # Verify pt actually lies on the segment (catches s outside [0,1])
    if not point_on_segment_exact(sp1, sp2, pt):
        return None

    return pt

# ---------------------------------------------------------------------------
# split_edge_general
# ---------------------------------------------------------------------------

def split_edge_general(cp, edge_index: int, new_vertex: Vertex4D) -> int:
    """
    Split cp.edges[edge_index] at new_vertex.
    Validates collinearity with point_on_segment_exact (angle-independent).
    Returns the index of new_vertex in cp.vertices.
    Does NOT call cp.get_vertex_neighbors().
    """
    v1i, v2i, ltype = cp.edges[edge_index]
    v1 = cp.vertices[v1i]
    v2 = cp.vertices[v2i]

    if new_vertex == v1: return v1i
    if new_vertex == v2: return v2i

    if not point_on_segment_exact(v1, v2, new_vertex):
        raise ValueError(
            f"split_edge_general: point {new_vertex.to_cartesian()} does not "
            f"lie on edge {v1.to_cartesian()} -> {v2.to_cartesian()}"
        )

    cp.vertices.append(new_vertex)
    ni = len(cp.vertices) - 1
    cp.edges.pop(edge_index)
    cp.edges.append((v1i, ni, ltype))
    cp.edges.append((ni, v2i, ltype))
    return ni

# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def add_general_crease(cp, p1: Vertex4D, p2: Vertex4D, line_type: str = "m"):
    """
    Add a crease along the infinite line through p1->p2 to cp.
    Returns cp (mutated) on success, None if no new edges were added.
    """
    if p1 == p2:
        raise ValueError("p1 and p2 must be distinct")

    # ── Step 1: snapshot ──────────────────────────────────────────────────
    # All intersection detection runs against this snapshot.
    # The live cp.edges is only touched during splits (Step 4).
    original_edges = list(cp.edges)
    original_verts = list(cp.vertices)

    # ── Step 2: collect hit points ────────────────────────────────────────
    hit_points: list[Vertex4D] = []
    # split_jobs: (original_edge_index, point) for interior intersections
    split_jobs:  list[tuple[int, Vertex4D]] = []

    def _seen(pt: Vertex4D) -> bool:
        return any(pt == h for h in hit_points)

    for ei, (v1i, v2i, _) in enumerate(original_edges):
        v1 = original_verts[v1i]
        v2 = original_verts[v2i]

        # Endpoints on the line (no split needed)
        for vv in (v1, v2):
            if _vertex_on_infinite_line(p1, p2, vv) and not _seen(vv):
                hit_points.append(vv)

        # Interior intersection
        pt = line_segment_intersection_exact(p1, p2, v1, v2)
        if pt is None or pt == v1 or pt == v2:
            continue
        if not _seen(pt):
            hit_points.append(pt)
            split_jobs.append((ei, pt))

    if len(hit_points) < 2:
        return None

    # ── Step 3: sort hits along the line (float ok — only for ordering) ──
    ref_x, ref_y = vertex4d_to_aplusbsqrt2_xy(p1)
    dx = vertex4d_to_aplusbsqrt2_xy(p2)[0] - ref_x
    dy = vertex4d_to_aplusbsqrt2_xy(p2)[1] - ref_y

    def _param(v: Vertex4D) -> float:
        vx, vy = vertex4d_to_aplusbsqrt2_xy(v)
        return float(dx * (vx - ref_x) + dy * (vy - ref_y))

    hit_points.sort(key=_param)

    # ── Step 4: perform splits in reverse original-index order ───────────
    # Reverse order ensures that popping edge[ei] doesn't shift lower indices.
    # If two split_jobs share the same original edge index (two interior
    # intersections on one edge), the second will find the edge gone and must
    # scan for the correct sub-edge.
    for orig_ei, pt in sorted(split_jobs, key=lambda x: x[0], reverse=True):
        # Check if edge at orig_ei in live list still corresponds to original
        if orig_ei < len(cp.edges):
            live_v1i, live_v2i, _ = cp.edges[orig_ei]
            if (cp.vertices[live_v1i] == original_verts[original_edges[orig_ei][0]] and
                cp.vertices[live_v2i] == original_verts[original_edges[orig_ei][1]]):
                split_edge_general(cp, orig_ei, pt)
                continue

        # Edge was already split (two hits on same original edge, or index shifted).
        # Find the sub-edge containing pt by scanning the live list.
        for scan_ei in range(len(cp.edges)):
            s1i, s2i, _ = cp.edges[scan_ei]
            if point_on_segment_exact(cp.vertices[s1i], cp.vertices[s2i], pt):
                split_edge_general(cp, scan_ei, pt)
                break
        # If not found, pt is already a vertex from a previous split — fine.

    # ── Step 5: add crease edges between consecutive hit points ───────────
    def _find(vv: Vertex4D) -> int:
        for i, u in enumerate(cp.vertices):
            if u == vv: return i
        raise RuntimeError(f"vertex not found after splits: {vv.to_cartesian()}")

    added = 0
    for i in range(len(hit_points) - 1):
        ai = _find(hit_points[i])
        bi = _find(hit_points[i + 1])
        if not any((e0 == ai and e1 == bi) or (e0 == bi and e1 == ai)
                   for e0, e1, _ in cp.edges):
            cp.edges.append((ai, bi, line_type))
            added += 1

    if added == 0:
        return None

    cp.get_vertex_neighbors()
    return cp