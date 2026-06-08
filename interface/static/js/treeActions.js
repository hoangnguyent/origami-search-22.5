import { state } from './state.js';
import * as Editor from './editor.js';
import { setStatus } from './utils.js';

export function deleteSelectedNode() {
  if (state.selectedNode === null) return false;

  const target = state.selectedNode;
  const incidentEdges = state.edges.filter((edge) => edge.u === target || edge.v === target);
  if (incidentEdges.length==0){
    setStatus("Cannot delete the last vertex",true);
    return false
  }
  if (incidentEdges.length > 2) {
    setStatus("Cannot delete a vertex with more than 2 connections", true);
    return false;
  }

  if (incidentEdges.length === 2) {
    const neighbors = incidentEdges.map((edge) => (edge.u === target ? edge.v : edge.u));
    delete state.nodes[target];
    state.edges = state.edges.filter((edge) => edge.u !== target && edge.v !== target);
    state.edges.push({ u: neighbors[0], v: neighbors[1] });
    state.selectedNode = null;
  } else if (incidentEdges.length === 1) {
    const [edge] = incidentEdges;
    const nextSelected = edge.u === target ? edge.v : edge.u;
    delete state.nodes[target];
    state.edges = state.edges.filter((edge) => edge.u !== target && edge.v !== target);
    state.selectedNode = nextSelected ?? null;
  } else {
    delete state.nodes[target];
    state.edges = state.edges.filter((edge) => edge.u !== target && edge.v !== target);
    state.selectedNode = null;
  }

  state.draggingNode = null;
  Editor.renderEditor();
  setStatus("Ready");
  return true;
}

export function moveSelectedNode(dx, dy) {
  if (state.selectedNode === null) return false;
  const node = state.nodes[state.selectedNode];
  if (!node) return false;
  node.x += dx / state.zoom;
  node.y += dy / state.zoom;
  Editor.renderEditor();
  setStatus("Ready");
  return true;
}