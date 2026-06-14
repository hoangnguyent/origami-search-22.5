"""
cp_steps.py  —  step-by-step CP instruction visualizer
"""

import sys, sqlite3, struct, json, math
import tkinter as tk

# ── geometry helpers ──────────────────────────────────────────────────────

def _v4d_xy(xn,xd,yn,yd,zn,zd,wn,wd):
    x,y,z,w = xn/xd, yn/yd, zn/zd, wn/wd
    s = math.sqrt(0.5)
    return x+(y-w)*s, z+(y+w)*s

def _v4d_xy_from_obj(v):
    return _v4d_xy(v.x.num,v.x.den,v.y.num,v.y.den,
                   v.z.num,v.z.den,v.w.num,v.w.den)

def _tx_row(row8, tfn):
    from math225_core import Vertex4D, Fraction
    v = Vertex4D(Fraction(row8[0],row8[1]),Fraction(row8[2],row8[3]),
                 Fraction(row8[4],row8[5]),Fraction(row8[6],row8[7]))
    return _v4d_xy_from_obj(tfn(v))

def _tx_lst(lst, tfn):
    from cp_tree import z2_to_v4d
    v = z2_to_v4d(*lst)   # lst is now 6-int z2 format
    return _v4d_xy_from_obj(tfn(v))

# ── canvas coords ─────────────────────────────────────────────────────────

CELL = 220
PAD  = 22

def _px(cx, cy):
    """Unit-square cartesian → canvas pixel."""
    p = PAD; s = CELL - 2*PAD
    return p + cx*s, (CELL-p) - cy*s

def _sq_rect():
    return PAD, PAD, CELL-PAD, CELL-PAD

# ── DB loading ────────────────────────────────────────────────────────────

def _parse_refs(refs_raw, tfn):
    if not refs_raw: return []
    out = []
    try:
        for r in json.loads(refs_raw):
            t = r.get("type","")
            if t == "vertex":
                out.append({"type":"vertex", "xy": _tx_lst(r["v"], tfn)})
            elif t in ("crease","edge"):
                out.append({"type":t,
                            "xy1": _tx_lst(r["v1"], tfn),
                            "xy2": _tx_lst(r["v2"], tfn)})
    except Exception as e:
        print(f"[_parse_refs error] {e}  raw={refs_raw[:80]}")
    return out

def _load_steps(conn, ancestry, tfn):
    from cp_tree import blobs_to_cp
    steps = []
    for entry in ancestry:
        fn = entry["function_name"]
        if fn == "root": continue
        nid = entry["id"]

        # Load CP from blobs stored in nodes table
        row = conn.execute(
            "SELECT vertices_blob, edges_blob FROM nodes WHERE id=?", (nid,)
        ).fetchone()
        cp = blobs_to_cp(row[0], row[1])

        vxy = [_v4d_xy_from_obj(tfn(v)) for v in cp.vertices]
        erows = [(v1,v2,lt) for v1,v2,lt in cp.edges]
        nc1 = _v4d_xy_from_obj(tfn(entry["new_crease_v1"]))
        nc2 = _v4d_xy_from_obj(tfn(entry["new_crease_v2"]))

        steps.append({
            "fn":    fn,
            "depth": entry["depth"],
            "nc1":   nc1,
            "nc2":   nc2,
            "vxy":   vxy,
            "erows": erows,
            "refs":  _parse_refs(entry["refs_raw"], tfn),
        })
    return steps

# ── instruction text ──────────────────────────────────────────────────────

def _instruction(fn, refs):
    if fn == "vertex_pair":
        return "Crease through the circled points."
    elif fn == "parallel_bisector":
        return "Bring the highlighted creases together."
    elif fn == "angle_bisector":
        return "Crease an angle bisector."
    elif fn == "perp_through_vertex":
        has_diag = any(r["type"] == "crease" for r in refs)
        if has_diag:
            return "Crease through the circled point, perpendicular to the diagonal."
        return "Crease through the circled point, perpendicular to the edge."
    return fn.replace("_"," ").capitalize() + "."

# ── drawing ───────────────────────────────────────────────────────────────

GRAY  = "#3a3a50"
BLUE  = "#4fa3e0"
WHITE = "#d8d8e8"
GREEN = "#50e890"
BG    = "#1a1a2e"
SQ    = "#5a5a72"   # square border

BORDER_W = 2     # square border line width
CLIP_PX  = 3     # how many px to inset old-crease endpoints from boundary

def _on_sq_boundary(cx, cy, tol=1e-6):
    return (abs(cx) < tol or abs(cx-1) < tol or
            abs(cy) < tol or abs(cy-1) < tol)

def _clip_to_interior(x1,y1,x2,y2):
    """
    Shorten a line so each endpoint is CLIP_PX pixels away from the square
    boundary, but only if that endpoint lies ON the boundary.  Interior
    endpoints are left untouched.
    """
    p = PAD; s = CELL - 2*PAD
    def to_unit(px,py): return (px-p)/s, 1-(py-p)/s
    def to_px(cx,cy):   return p+cx*s, (CELL-p)-cy*s

    u1 = to_unit(x1,y1); u2 = to_unit(x2,y2)
    dx,dy = x2-x1, y2-y1
    L = math.hypot(dx,dy)
    if L < 1: return x1,y1,x2,y2

    nx,ny = dx/L, dy/L  # unit direction pixel-space

    ox1,oy1 = x1,y1
    ox2,oy2 = x2,y2
    if _on_sq_boundary(*u1):
        ox1 = x1 + nx*CLIP_PX
        oy1 = y1 + ny*CLIP_PX
    if _on_sq_boundary(*u2):
        ox2 = x2 - nx*CLIP_PX
        oy2 = y2 - ny*CLIP_PX
    return ox1,oy1,ox2,oy2


def _extend_to_boundary(cx1,cy1, cx2,cy2):
    """
    Given two points on a line (unit-square coords), return the two
    intersection points of that infinite line with the unit square [0,1]^2.
    Handles corners correctly by deduplicating against ALL seen points.
    """
    EPS = 1e-9
    dx = cx2-cx1; dy = cy2-cy1
    pts = []
    for t in [
        (-cx1/dx)     if abs(dx)>EPS else None,   # x=0
        ((1-cx1)/dx)  if abs(dx)>EPS else None,   # x=1
        (-cy1/dy)     if abs(dy)>EPS else None,   # y=0
        ((1-cy1)/dy)  if abs(dy)>EPS else None,   # y=1
    ]:
        if t is None: continue
        x = cx1 + t*dx; y = cy1 + t*dy
        if -EPS <= x <= 1+EPS and -EPS <= y <= 1+EPS:
            # Deduplicate against ALL already-seen points (handles corners
            # where two boundary edges meet at the same point)
            if not any(math.hypot(x-px, y-py) < 1e-6 for px,py in pts):
                pts.append((x,y))
    if len(pts) < 2: return None
    return pts[0], pts[-1]


def _on_new_crease(p1,p2, nc1,nc2):
    if nc1 is None: return False
    dx,dy = nc2[0]-nc1[0], nc2[1]-nc1[1]
    L2 = dx*dx+dy*dy
    if L2 < 1e-12: return False
    def cross(px,py):
        ex,ey = px-nc1[0], py-nc1[1]
        return abs(dx*ey - dy*ex)
    return cross(*p1) < 1e-6*math.sqrt(L2) and cross(*p2) < 1e-6*math.sqrt(L2)


def draw_step(canvas, step, is_final_copy=False, target_xy=None):
    canvas.delete("all")
    canvas.configure(bg=BG)

    vxy   = step["vxy"]
    erows = step["erows"]
    nc1   = step["nc1"] if not is_final_copy else None
    nc2   = step["nc2"] if not is_final_copy else None
    refs  = step["refs"] if not is_final_copy else []

    ref_line_segs = [r for r in refs if r["type"] in ("crease","edge")]
    ref_verts     = [r for r in refs if r["type"] == "vertex"]

    # ── layer 1: old creases (gray, clipped at boundary) ─────────────────
    for vi,vj,lt in erows:
        if lt == "b": continue
        p1 = vxy[vi]; p2 = vxy[vj]
        if _on_new_crease(p1,p2,nc1,nc2): continue   # draw new crease later
        px1,py1 = _px(*p1); px2,py2 = _px(*p2)
        cx1,cy1,cx2,cy2 = _clip_to_interior(px1,py1,px2,py2)
        canvas.create_line(cx1,cy1,cx2,cy2, fill=GRAY, width=1.2)

    # ── layer 2: square border (overdraw, covers clipped crease stubs) ───
    x0,y0,x1,y1 = _sq_rect()
    canvas.create_rectangle(x0,y0,x1,y1, outline=SQ, width=BORDER_W, fill="")

    # ── layer 3: ref lines — full boundary-to-boundary, white ────────────
    for r in ref_line_segs:
        ends = _extend_to_boundary(*r["xy1"], *r["xy2"])
        if ends is None: continue
        px1,py1 = _px(*ends[0]); px2,py2 = _px(*ends[1])
        canvas.create_line(px1,py1,px2,py2, fill=WHITE, width=2.0)

    # ── layer 4: new crease — full length, bold blue ──────────────────────
    if nc1 is not None:
        px1,py1 = _px(*nc1); px2,py2 = _px(*nc2)
        canvas.create_line(px1,py1,px2,py2, fill=BLUE, width=2.5)

    # ── layer 5: ref vertex circles (white hollow, on top of border) ──────
    for r in ref_verts:
        px,py = _px(*r["xy"])
        R = 7
        canvas.create_oval(px-R,py-R,px+R,py+R, outline=WHITE, width=2, fill="")

    # ── layer 6: target vertex dot (final copy only) ──────────────────────
    if is_final_copy and target_xy is not None:
        px,py = _px(*target_xy)
        r = 4
        canvas.create_oval(px-r,py-r,px+r,py+r, fill=GREEN, outline="")


# ── main window ───────────────────────────────────────────────────────────

GAP    = 32
LABEL_H= 52
COLS   = 5

def show_steps(conn, result):
    tfn      = result.transform_fn
    ancestry = result.ancestry
    steps    = _load_steps(conn, ancestry, tfn)

    # Duplicate last step — no new crease shown, just target dot
    if steps:
        steps = steps + [dict(steps[-1], _target=True)]

    uv = result.matched_user
    target_xy = _v4d_xy_from_obj(uv)

    n      = len(steps)
    n_cols = min(COLS, n)
    n_rows = math.ceil(n / n_cols)

    root = tk.Tk()
    root.title(f"Node #{result.node_id}  depth={result.depth}  "
               f"transform={result.transform_name}")
    root.configure(bg=BG)
    root.resizable(True, True)

    outer = tk.Frame(root, bg=BG)
    outer.pack(fill="both", expand=True)
    vc  = tk.Canvas(outer, bg=BG, highlightthickness=0,
                    width=min(n_cols*(CELL+GAP)+GAP, 1300),
                    height=min(n_rows*(CELL+LABEL_H+GAP)+GAP, 800))
    vsb = tk.Scrollbar(outer, orient="vertical",   command=vc.yview)
    hsb = tk.Scrollbar(outer, orient="horizontal", command=vc.xview)
    vc.configure(xscrollcommand=hsb.set, yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y")
    hsb.pack(side="bottom", fill="x")
    vc.pack(side="left", fill="both", expand=True)

    frame = tk.Frame(vc, bg=BG)
    vc.create_window(0, 0, anchor="nw", window=frame)
    frame.bind("<Configure>", lambda e: vc.configure(scrollregion=vc.bbox("all")))

    for idx, step in enumerate(steps):
        is_tc  = step.get("_target", False)
        row    = idx // n_cols
        col    = idx % n_cols
        num    = idx + 1

        cell = tk.Frame(frame, bg=BG)
        cell.grid(row=row, column=col, padx=GAP//2, pady=GAP//2, sticky="n")

        c = tk.Canvas(cell, width=CELL, height=CELL,
                      bg=BG, highlightthickness=0)
        c.pack()

        draw_step(c, step,
                  is_final_copy=is_tc,
                  target_xy=target_xy if is_tc else None)

        if is_tc:
            txt = "Target vertex."
            clr = WHITE
        else:
            txt = _instruction(step["fn"], step["refs"])
            clr = WHITE

        tk.Label(cell,
                 text=f"{num}. {txt}",
                 bg=BG, fg=clr,
                 font=("Helvetica", 9),
                 wraplength=CELL,
                 justify="left").pack(anchor="w", pady=(4,0))

    root.mainloop()

# ── entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":

    def _is_int(s):
        try: int(s); return True
        except: return False

    args = sys.argv[1:]
    if args and not _is_int(args[0]):
        db_path = args.pop(0)
    else:
        db_path = "cp_tree.db"

    if len(args) != 6:
        print("Usage: python cp_steps.py [db_path] ax bx cx  ay by cy")
        sys.exit(1)

    ax,bx,cx_ = int(args[0]),int(args[1]),int(args[2])
    ay,by,cy_ = int(args[3]),int(args[4]),int(args[5])

    import math as _m
    xf=(ax+bx*_m.sqrt(2))/cx_; yf=(ay+by*_m.sqrt(2))/cy_
    print(f"Looking up ({ax}+{bx}√2)/{cx_}, ({ay}+{by}√2)/{cy_}  ≈  ({xf:.5g},{yf:.5g})")

    from cp_lookup import lookup_vertex
    conn = sqlite3.connect(db_path)
    try:
        results = lookup_vertex(conn, ax, bx, cx_, ay, by, cy_)
        if not results:
            print("No match found.")
            sys.exit(0)
        r = results[0]
        print(f"Node #{r.node_id}  depth={r.depth}  transform={r.transform_name}")
        show_steps(conn, r)
    except ValueError as e:
        print(f"Error: {e}")
    finally:
        conn.close()