"""Local HTTP server for the browser-based query interface."""

from __future__ import annotations

import json
import math
import os
import pickle
import uuid
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import networkx as nx

from database.tilings.faiss_cache import DIMENSION, compute_hkt_signature, get_t_scales
from database.tilings.query import query_tilings
from src.engine.tree import EIG_COUNT, RESOLUTION, extract_eigenvalues, get_proportional_tree_pos
from src.engine.fold225 import PLOT_COLORS, ALPHA

from interface.serialization import (
    REPO_ROOT,
    load_db_scale_payload,
    serialize_cp,
    serialize_fold,
    serialize_graph,
    serialize_result_pickle,
    serialize_tree,
)


STATIC_DIR = Path(__file__).resolve().parent / "static"
TOKEN = os.environ.get("SEARCH22_INTERFACE_TOKEN", "").strip()
QUERY_CACHE: dict[str, dict[str, Any]] = {}
QUERY_CACHE_LOCK = threading.Lock()


def _vertex_to_xy(value: Any) -> list[float]:
    if hasattr(value, "x") and hasattr(value, "y") and hasattr(value, "z") and hasattr(value, "w"):
        sqrt2 = math.sqrt(2.0) / 2.0
        x = float(value.x) + sqrt2 * (float(value.y) - float(value.w))
        y = float(value.z) + sqrt2 * (float(value.y) + float(value.w))
        return [x, y]
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return [float(value[0]), float(value[1])]
    if hasattr(value, "to_cartesian"):
        x, y = value.to_cartesian()
        return [float(x), float(y)]
    raise TypeError(f"Unsupported position value: {type(value)!r}")


def _require_token(handler: BaseHTTPRequestHandler) -> bool:
    if not TOKEN:
        return True
    received = handler.headers.get("X-Interface-Token", "").strip()
    return received == TOKEN


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    content_length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(content_length) if content_length else b"{}"
    return json.loads(raw.decode("utf-8"))


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


def _send_bytes(handler: BaseHTTPRequestHandler, content_type: str, body: bytes) -> None:
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _build_query_graph(tree_payload: dict[str, Any]) -> nx.Graph:
    graph = nx.Graph()

    for node in tree_payload.get("nodes", []):
        node_id = int(node["id"])
        pos = node.get("pos")
        if pos is None and "x" in node and "y" in node:
            pos = [float(node["x"]), float(node["y"])]
        graph.add_node(node_id, pos=pos)

    nodes_by_id = {int(node["id"]): node for node in tree_payload.get("nodes", [])}
    for edge in tree_payload.get("edges", []):
        u = int(edge["u"])
        v = int(edge["v"])
        x1, y1 = nodes_by_id[u]["x"], nodes_by_id[u]["y"]
        x2, y2 = nodes_by_id[v]["x"], nodes_by_id[v]["y"]
        length = float(edge.get("length", math.hypot(x1 - x2, y1 - y2)))
        if length < 1e-5:
            length = 1e-5
        graph.add_edge(u, v, length=length, weight=1.0 / length)

    return graph


def _normalize_db_configs(payload: dict[str, Any]) -> list[tuple[int, str]]:
    raw = payload.get("db_configs") or payload.get("dbs") or []
    normalized: list[tuple[int, str]] = []

    for item in raw:
        if isinstance(item, dict):
            normalized.append((int(item["N"]), str(item["symmetry"])))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            normalized.append((int(item[0]), str(item[1])))
        elif isinstance(item, str):
            parts = item.replace("_", " ").split()
            if len(parts) >= 2:
                normalized.append((int(parts[0]), parts[1]))

    return normalized


def _serialize_query_tree(query_tree: nx.Graph) -> dict[str, Any]:
    pos = {
        node: [float(coord[0]), float(coord[1])]
        for node, coord in nx.get_node_attributes(query_tree, "pos").items()
        if coord is not None
    }
    if not pos:
        pos = {
            node: [float(coord[0]), float(coord[1])]
            for node, coord in get_proportional_tree_pos(query_tree).items()
        }
    return serialize_tree(query_tree, pos=pos)


def _serialize_tree_with_preserved_pos(graph: nx.Graph) -> dict[str, Any]:
    if isinstance(graph, tuple):
        graph = graph[0]

    pos = {
        node: [float(coord[0]), float(coord[1])]
        for node, coord in nx.get_node_attributes(graph, "pos").items()
        if coord is not None
    }
    if not pos:
        raw_pos = nx.spring_layout(graph, seed=42)
        pos = {
            node: [float(coord[0]), float(coord[1])]
            for node, coord in raw_pos.items()
        }
        
    return serialize_tree(graph, pos=pos)


def _serialize_topology_graph(graph: nx.Graph, seed: int = 42) -> dict[str, Any]:
    if graph.number_of_nodes() == 0:
        return {"nodes": [], "edges": []}
    # Prefer explicit integer/grid node positions when available (stored in node attribute 'pos')
    node_pos = nx.get_node_attributes(graph, "pos")
    if node_pos:
        # Ensure positions are pairs and usable; otherwise fall back to layout
        ok = True
        for v, p in node_pos.items():
            try:
                if not (isinstance(p, (list, tuple)) and len(p) >= 2):
                    ok = False
                    break
            except Exception:
                ok = False
                break
        if ok:
            return serialize_graph(graph, pos=node_pos)

    pos = nx.spring_layout(graph, seed=seed)
    return serialize_graph(graph, pos=pos)


def _serialize_solved_tiling(graph: nx.Graph, pos_solved: dict[Any, Any]) -> dict[str, Any]:
    pos = {node: _vertex_to_xy(value) for node, value in pos_solved.items()}
    return serialize_graph(graph, pos=pos)


def _sanitize_for_pickle(obj: Any) -> Any:
    """Recursively convert non-pickle-friendly objects to basic Python types."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    
    if isinstance(obj, dict):
        return {k: _sanitize_for_pickle(v) for k, v in obj.items()}
    
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_pickle(item) for item in obj]
    
    # Convert Vertex4D and similar objects with coordinates
    if hasattr(obj, "x") and hasattr(obj, "y") and hasattr(obj, "z") and hasattr(obj, "w"):
        return _vertex_to_xy(obj)
    
    # Convert objects with to_cartesian method
    if hasattr(obj, "to_cartesian"):
        x, y = obj.to_cartesian()
        return [float(x), float(y)]
    
    # For NetworkX graphs, convert to adjacency structure
    if isinstance(obj, nx.Graph):
        return {
            "nodes": list(obj.nodes()),
            "edges": [(u, v, dict(data)) for u, v, data in obj.edges(data=True)],
        }
    
    # For CP objects and other custom objects, try to convert to dict
    if hasattr(obj, "__dict__"):
        return _sanitize_for_pickle(obj.__dict__)
    
    # Last resort: return string representation
    return str(obj)


def _build_heat_profile(query_tree: nx.Graph, result_tree: nx.Graph, N: int, symmetry: str) -> dict[str, Any]:
    prefix = REPO_ROOT / f"database/tilings/faiss_cache/db_{N}_{symmetry}"
    cache_data = load_db_scale_payload(prefix)
    mu = cache_data["mu"]
    sigma = cache_data["sigma"]
    t_scales = get_t_scales(dim=DIMENSION)

    query_eigs = extract_eigenvalues(query_tree, eig_count=EIG_COUNT, resolution=RESOLUTION)
    query_hkt = compute_hkt_signature(query_eigs, dim=DIMENSION)

    result_eigs = extract_eigenvalues(result_tree, eig_count=EIG_COUNT, resolution=RESOLUTION)
    result_hkt = compute_hkt_signature(result_eigs, dim=DIMENSION)

    normalized_query = ((query_hkt - mu) / sigma).tolist()
    normalized_result = ((result_hkt - mu) / sigma).tolist()

    return {
        "t_scales": [float(value) for value in t_scales],
        "query": [float(value) for value in normalized_query],
        "result": [float(value) for value in normalized_result],
    }


def build_response_bundle(query_tree: nx.Graph, results: list[dict[str, Any]], db_configs: list[tuple[int, str]]) -> dict[str, Any]:
    query_tree_payload = _serialize_query_tree(query_tree)
    ui_results: list[dict[str, Any]] = []

    for res in results:
        tree_graph = res["tree"]
        ui_results.append(
            {
                "rank": res.get("rank"),
                "distance": float(res.get("distance", 0.0)),
                "N": res.get("N"),
                "symmetry": res.get("symmetry"),
                "topology_id": res.get("topology_id"),
                "tiling_id": res.get("tiling_id"),
                "topology": _serialize_topology_graph(res["G_raw"]),
                "solved_tiling": _serialize_solved_tiling(res["G_solved"], res["pos_solved"]),
                "cp": serialize_cp(res["cp"]),
                "fold": serialize_fold(res["fold"]),
                "tree": _serialize_tree_with_preserved_pos(tree_graph),
                "packing": serialize_cp(res["packing"]),
                "heat": _build_heat_profile(query_tree, tree_graph, res["N"], res["symmetry"]),
            }
        )

    bundle = {
        "query_id": str(uuid.uuid4()),
        "db_configs": [{"N": N, "symmetry": sym} for N, sym in db_configs],
        "query_tree": query_tree_payload,
        "results": ui_results,
        "visual_constants": {"plot_colors": PLOT_COLORS, "alpha": ALPHA},
        "bundle_pickle_b64": serialize_result_pickle(
            _sanitize_for_pickle({
                "query_tree": query_tree,
                "db_configs": db_configs,
                "results": results,
            })
        ),
    }

    with QUERY_CACHE_LOCK:
        QUERY_CACHE[bundle["query_id"]] = bundle

    return bundle


class InterfaceHandler(BaseHTTPRequestHandler):
    server_version = "SEARCH22Interface/0.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/index.html"}:
            html = (STATIC_DIR / "index.html").read_bytes()
            _send_bytes(self, "text/html; charset=utf-8", html)
            return
            
        if self.path in {"/favicon.png", "/favicon.ico"}:
            file_path = STATIC_DIR / self.path.lstrip("/")
            if file_path.exists():
                body = file_path.read_bytes()
                ctype = "image/png" if self.path.endswith(".png") else "image/x-icon"
                _send_bytes(self, ctype, body)
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Favicon not found")
            return
        if self.path == "/about.html":
            html = (STATIC_DIR / "about.html").read_bytes()
            _send_bytes(self, "text/html; charset=utf-8", html)
            return

        if self.path == "/about.css":
            css = (STATIC_DIR / "about.css").read_bytes()
            _send_bytes(self, "text/css; charset=utf-8", css)
            return

        if self.path == "/app.js":
            js = (STATIC_DIR / "app.js").read_bytes()
            _send_bytes(self, "application/javascript; charset=utf-8", js)
            return

        if self.path == "/styles.css":
            css = (STATIC_DIR / "styles.css").read_bytes()
            _send_bytes(self, "text/css; charset=utf-8", css)
            return
        
        if self.path.endswith(".svg"):
            # Construct the full path to the file within the static directory
            file_path = STATIC_DIR / self.path.lstrip("/")
            if file_path.exists():
                svg_content = file_path.read_bytes()
                _send_bytes(self, "image/svg+xml", svg_content)
                return

        # Serve any files requested under /interface/static/... by mapping to STATIC_DIR
        if self.path.startswith("/interface/static/"):
            rel = self.path[len("/interface/static/"):].lstrip("/")
            file_path = STATIC_DIR / rel
            if file_path.exists() and file_path.is_file():
                body = file_path.read_bytes()
                # crude content-type mapping
                if file_path.suffix == ".js":
                    ctype = "application/javascript; charset=utf-8"
                elif file_path.suffix == ".css":
                    ctype = "text/css; charset=utf-8"
                elif file_path.suffix == ".svg":
                    ctype = "image/svg+xml"
                elif file_path.suffix == ".json":
                    ctype = "application/json; charset=utf-8"
                elif file_path.suffix == ".png":
                    ctype = "image/png"
                elif file_path.suffix == ".ico":
                    ctype = "image/x-icon"
                else:
                    ctype = "application/octet-stream"
                _send_bytes(self, ctype, body)
                return

        if self.path == "/api/config":
            _send_json(
                self,
                HTTPStatus.OK,
                {
                    "auth_required": bool(TOKEN),
                    "db_options": [
                        {"N": 3, "symmetry": "none", "label": "3 none"},
                        {"N": 4, "symmetry": "diag", "label": "4 diag"},
                        {"N": 4, "symmetry": "none", "label": "4 none"},
                        {"N": 5, "symmetry": "diag", "label": "5 diag"},
                    ],
                },
            )
            return

        if self.path.startswith("/api/result/"):
            query_id = self.path.rstrip("/").rsplit("/", 1)[-1]
            with QUERY_CACHE_LOCK:
                cached = QUERY_CACHE.get(query_id)
            if cached is None:
                self.send_error(HTTPStatus.NOT_FOUND, "Unknown query id")
                return
            _send_json(self, HTTPStatus.OK, cached)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/query":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        if not _require_token(self):
            self.send_error(HTTPStatus.UNAUTHORIZED, "Missing or invalid interface token")
            return

        payload = _read_json(self)
        query_tree = _build_query_graph(payload["tree"])
        if not nx.is_connected(query_tree):
            _send_json(
                self,
                HTTPStatus.BAD_REQUEST,
                {"error": "The drawn graph is disconnected. Please connect all nodes."},
            )
            return

        db_configs = _normalize_db_configs(payload)
        if not db_configs:
            db_configs = [(4, "diag"), (4, "none"), (3, "none"), (5, "diag")]

        n_results = int(payload.get("n", 5))
        results = query_tilings(query_tree, db_configs=db_configs, n=n_results)
        response = build_response_bundle(query_tree, results, db_configs)
        _send_json(self, HTTPStatus.OK, response)


def main() -> None:
    host = os.environ.get("SEARCH22_INTERFACE_HOST", "127.0.0.1")
    port = int(os.environ.get("SEARCH22_INTERFACE_PORT", "8000"))
    server = ThreadingHTTPServer((host, port), InterfaceHandler)
    print(f"SEARCH-22.5 interface listening on http://{host}:{port}")
    if TOKEN:
        print("Interface token enabled via SEARCH22_INTERFACE_TOKEN")
    server.serve_forever()


if __name__ == "__main__":
    main()
