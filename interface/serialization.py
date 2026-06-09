"""Serialization helpers for the browser interface.

The backend keeps the heavy Python objects and emits a browser-safe JSON view
alongside a compressed pickle payload for fidelity and round-tripping.
"""

from __future__ import annotations

import base64
import pickle
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]


def _pair_to_list(value: Any) -> list[float]:
    def _to_numbers(x, y):
        fx = float(x)
        fy = float(y)
        # Preserve integer grid points as ints for topology clarity
        if abs(fx - round(fx)) < 1e-9 and abs(fy - round(fy)) < 1e-9:
            return [int(round(fx)), int(round(fy))]
        return [fx, fy]

    if isinstance(value, np.ndarray):
        return _to_numbers(value[0], value[1])
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return _to_numbers(value[0], value[1])
    if hasattr(value, "x") and hasattr(value, "y"):
        return _to_numbers(value.x, value.y)
    if hasattr(value, "to_cartesian"):
        x, y = value.to_cartesian()
        return _to_numbers(x, y)
    raise TypeError(f"Unsupported coordinate value: {type(value)!r}")


def serialize_graph(graph: nx.Graph, pos: dict[Any, Any] | None = None) -> dict[str, Any]:
    nodes = []
    node_ids = list(graph.nodes())

    for node_id in node_ids:
        # Normalize node ids to JSON-friendly stable values (integers kept as-is,
        # other types coerced to strings). This ensures edges reference the same ids
        # and the frontend can reliably match nodes to edges.
        if isinstance(node_id, int):
            nid = node_id
        else:
            nid = str(node_id)
        node_payload = {"id": nid}
        if pos and node_id in pos:
            node_payload["pos"] = _pair_to_list(pos[node_id])
        nodes.append(node_payload)

    edges = []
    for u, v, data in graph.edges(data=True):
        # Ensure edge endpoints use the same normalized id form as nodes above.
        u_id = u if isinstance(u, int) else str(u)
        v_id = v if isinstance(v, int) else str(v)
        edge_payload = {"u": u_id, "v": v_id}
        if "length" in data:
            edge_payload["length"] = float(data["length"])
        if "weight" in data:
            edge_payload["weight"] = float(data["weight"])
        edges.append(edge_payload)

    return {"nodes": nodes, "edges": edges}


def serialize_cp(cp: Any) -> dict[str, Any]:
    """
    Serializes the crease pattern sending exact 4D mathematical coordinates 
    as integer arrays rather than pre-computing Cartesian floats.
    """
    return {
        "vertices": [
            [
                vert.x.num, vert.x.den,
                vert.y.num, vert.y.den,
                vert.z.num, vert.z.den,
                vert.w.num, vert.w.den,
            ]
            for vert in cp.vertices
        ],
        "edges": [
            [v1_idx, v2_idx, line_type] 
            for v1_idx, v2_idx, line_type in cp.edges
        ]
    }

def serialize_fold(fold: Any) -> dict[str, Any]:
    faces, multiplicities = fold.render()
    return {
        "faces": [
            [[float(x), float(y)] for x, y in face]
            for face in faces
        ],
        "multiplicities": [int(value) for value in multiplicities],
    }


def serialize_tree(graph: nx.Graph, pos: dict[Any, Any] | None = None) -> dict[str, Any]:
    return serialize_graph(graph, pos=pos)


def serialize_result_pickle(payload: Any) -> str:
    raw = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    return base64.b64encode(raw).decode("ascii")


def load_db_scale_payload(prefix: Path | str) -> dict[str, Any]:
    import pickle as _pickle

    data_path = Path(f"{prefix}_data.pkl")
    with data_path.open("rb") as handle:
        cache_data = _pickle.load(handle)
    return {
        "mu": cache_data["mu"],
        "sigma": cache_data["sigma"],
    }
