import { state } from './state.js';
import { makeSvg } from './renderers.js';
import { setStatus } from './utils.js';
import { isMobileLayout } from './layout.js';

const editorSvg = document.getElementById("editorSvg");
const deleteNodeBtn = document.getElementById("deleteNodeBtn");
const mobileEditorControls = document.getElementById("mobileEditorControls");
const moveNodeUpBtn = document.getElementById("moveNodeUpBtn");
const moveNodeDownBtn = document.getElementById("moveNodeDownBtn");
const moveNodeLeftBtn = document.getElementById("moveNodeLeftBtn");
const moveNodeRightBtn = document.getElementById("moveNodeRightBtn");
const PAN_DRAG_THRESHOLD = 4;
const MIN_ZOOM = 0.35;
const MAX_ZOOM = 2.8;
const ZOOM_STEP = 1.12;

const activePointers = new Map();
let activeDragPointerId = null;
let pinchGesture = null;

function clamp(value, minValue, maxValue) {
  return Math.min(Math.max(value, minValue), maxValue);
}

// Replace getWorldPointFromScreen with an SVG-aware version
function getWorldPointFromSvg(svgX, svgY) {
  return {
    x: (svgX - state.panOffset.x) / state.zoom,
    y: (svgY - state.panOffset.y) / state.zoom,
  };
}

function updateTrackedPointer(event) {
  activePointers.set(event.pointerId, {
    pointerId: event.pointerId,
    pointerType: event.pointerType,
    clientX: event.clientX,
    clientY: event.clientY,
  });
}

function removeTrackedPointer(pointerId) {
  activePointers.delete(pointerId);
  if (activeDragPointerId === pointerId) {
    activeDragPointerId = null;
  }
}

function getActiveTouchPointers() {
  return [...activePointers.values()].filter((pointer) => pointer.pointerType === "touch");
}
// Convert touch coordinates to SVG space before calculating the pinch center
function getPinchCenter(pointerA, pointerB) {
  const ptA = getSvgPoint(pointerA);
  const ptB = getSvgPoint(pointerB);
  return {
    x: (ptA.x + ptB.x) / 2,
    y: (ptA.y + ptB.y) / 2,
  };
}

// Add a helper to calculate pinch distance in SVG space
function getPinchDistance(pointerA, pointerB) {
  const ptA = getSvgPoint(pointerA);
  const ptB = getSvgPoint(pointerB);
  return Math.hypot(ptB.x - ptA.x, ptB.y - ptA.y);
}

function beginPinchGesture() {
  const touchPointers = getActiveTouchPointers();
  if (touchPointers.length < 2) return;
  const [pointerA, pointerB] = touchPointers;
  const center = getPinchCenter(pointerA, pointerB);
  pinchGesture = {
    startDistance: getPinchDistance(pointerA, pointerB),
    startZoom: state.zoom,
    anchorWorld: getWorldPointFromSvg(center.x, center.y),
  };
  state.backgroundGesture = null;
  state.draggingNode = null;
  activeDragPointerId = null;
  state.isPanning = true;
  renderEditor();
}

function updatePinchGesture() {
  const touchPointers = getActiveTouchPointers();
  if (!pinchGesture || touchPointers.length < 2) {
    pinchGesture = null;
    return;
  }

  const [pointerA, pointerB] = touchPointers;
  const center = getPinchCenter(pointerA, pointerB);
  const currentDistance = getPinchDistance(pointerA, pointerB);
  const nextZoom = clamp(pinchGesture.startZoom * (currentDistance / pinchGesture.startDistance), MIN_ZOOM, MAX_ZOOM);

  state.zoom = nextZoom;
  state.panOffset.x = center.x - pinchGesture.anchorWorld.x * state.zoom;
  state.panOffset.y = center.y - pinchGesture.anchorWorld.y * state.zoom;
  renderEditor();
}

function shouldUseDirectDrag() {
  return !isMobileLayout();
}

export function getSvgPoint(event) {
  const point = editorSvg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const ctm = editorSvg.getScreenCTM();
  return ctm ? point.matrixTransform(ctm.inverse()) : { x: 0, y: 0 };
}

export function getClosestNode(x, y, hitRadius = 18) {
  let closest = null;
  let minDistance = Infinity;
  for (const node of Object.values(state.nodes)) {
    const distance = Math.hypot(node.x - x, node.y - y);
    if (distance < hitRadius && distance < minDistance) {
      minDistance = distance;
      closest = node;
    }
  }
  return closest;
}
export function onEditorMouseDown(event) {
  if (event.pointerType === "mouse" && event.button !== 0) return;
  updateTrackedPointer(event);
  if (editorSvg && editorSvg.setPointerCapture) {
    try { editorSvg.setPointerCapture(event.pointerId); } catch {}
  }
  const { x, y } = getSvgPoint(event);
  const { x: worldX, y: worldY } = getWorldPointFromSvg(x, y);
  const hit = getClosestNode(worldX, worldY);
  
  if (hit) {
    state.selectedNode = hit.id;
    state.backgroundGesture = null;
    state.isPanning = false;
    if (shouldUseDirectDrag() && event.pointerType !== "touch") {
      state.draggingNode = hit.id;
      activeDragPointerId = event.pointerId;
    } else {
      state.draggingNode = null;
      activeDragPointerId = null;
    }
    renderEditor();
    if (event.pointerType === "touch") {
      beginPinchGesture();
    }
    return;
  }

  state.backgroundGesture = {
    startClientX: event.clientX, // Kept for threshold checking
    startClientY: event.clientY,
    startSvgX: x,                // New: SVG-specific start coordinates
    startSvgY: y,
    startPanX: state.panOffset.x,
    startPanY: state.panOffset.y,
    worldX,
    worldY,
  };
  state.isPanning = false;

  if (state.selectedNode !== null) {
    event.preventDefault();
  }

  if (event.pointerType === "touch") {
    beginPinchGesture();
  }
}

export function onEditorMouseMove(event) {
  updateTrackedPointer(event);

  if (pinchGesture) {
    updatePinchGesture();
    return;
  }

  if (state.draggingNode !== null) {
    if (event.pointerType === "touch" || event.pointerId !== activeDragPointerId) return;
    const { x, y } = getSvgPoint(event);
    const { x: worldX, y: worldY } = getWorldPointFromSvg(x, y);
    state.nodes[state.draggingNode].x = worldX;
    state.nodes[state.draggingNode].y = worldY;
    renderEditor();
    return;
  }

  if (!state.backgroundGesture) return;

  // Threshold check remains in screen pixels for consistent feel across zoom levels
  const dxClient = event.clientX - state.backgroundGesture.startClientX;
  const dyClient = event.clientY - state.backgroundGesture.startClientY;
  const movedEnough = Math.hypot(dxClient, dyClient) >= PAN_DRAG_THRESHOLD;
  if (!state.isPanning && !movedEnough) return;

  // Actual pan offset must be applied using SVG coordinates
  const { x, y } = getSvgPoint(event);
  const dxSvg = x - state.backgroundGesture.startSvgX;
  const dySvg = y - state.backgroundGesture.startSvgY;

  state.isPanning = true;
  state.panOffset.x = state.backgroundGesture.startPanX + dxSvg;
  state.panOffset.y = state.backgroundGesture.startPanY + dySvg;
  renderEditor();
}

export function onEditorWheel(event) {
  if (!editorSvg) return;
  event.preventDefault();

  const { x: svgX, y: svgY } = getSvgPoint(event);
  const worldPoint = getWorldPointFromSvg(svgX, svgY);
  const zoomDirection = event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
  const nextZoom = clamp(state.zoom * zoomDirection, MIN_ZOOM, MAX_ZOOM);

  state.zoom = nextZoom;
  state.panOffset.x = svgX - worldPoint.x * state.zoom;
  state.panOffset.y = svgY - worldPoint.y * state.zoom;
  renderEditor();
}

export function onEditorMouseUp(event) {
  if (event) {
    removeTrackedPointer(event.pointerId);
    if (editorSvg && editorSvg.releasePointerCapture) {
      try { editorSvg.releasePointerCapture(event.pointerId); } catch {}
    }
  }

  if (pinchGesture && getActiveTouchPointers().length < 2) {
    pinchGesture = null;
    state.isPanning = false;
  }

  const shouldCreateNode = state.backgroundGesture && !state.isPanning && state.selectedNode !== null;

  if (state.draggingNode !== null) {
    setStatus("Ready");
  }
  state.draggingNode = null;

  if (shouldCreateNode) {
    const newNode = { id: state.nextNodeId, x: state.backgroundGesture.worldX, y: state.backgroundGesture.worldY };
    state.nodes[newNode.id] = newNode;
    state.edges.push({ u: state.selectedNode, v: newNode.id });
    state.nextNodeId += 1;
    renderEditor();
    setStatus("Ready");
  } else if (state.isPanning) {
    setStatus("Ready");
  }

  state.backgroundGesture = null;
  state.isPanning = false;
}

export function makeSvgLocal(tag, attrs = {}) { return makeSvg(tag, attrs); }
export function renderEditor() {
  editorSvg.replaceChildren();
  editorSvg.classList.toggle("is-panning", state.isPanning);
  editorSvg.classList.toggle("has-dragged-node", state.draggingNode !== null);
  editorSvg.classList.toggle("has-selection", state.selectedNode !== null);
  if (mobileEditorControls) {
    mobileEditorControls.classList.toggle("has-selection", state.selectedNode !== null);
  }
  if (deleteNodeBtn) deleteNodeBtn.disabled = state.selectedNode === null;
  if (moveNodeUpBtn) moveNodeUpBtn.disabled = state.selectedNode === null;
  if (moveNodeDownBtn) moveNodeDownBtn.disabled = state.selectedNode === null;
  if (moveNodeLeftBtn) moveNodeLeftBtn.disabled = state.selectedNode === null;
  if (moveNodeRightBtn) moveNodeRightBtn.disabled = state.selectedNode === null;

  // FIX: Make the background rect span infinitely to support letterboxing/pillarboxing
  editorSvg.appendChild(makeSvg("rect", { x: -5000, y: -5000, width: 10000, height: 10000, fill: "transparent", class: "editor-background" }));

  const content = makeSvg("g", { transform: `translate(${state.panOffset.x} ${state.panOffset.y}) scale(${state.zoom})` });

  for (const edge of state.edges) {
    const start = state.nodes[edge.u];
    const end = state.nodes[edge.v];
    content.appendChild(makeSvg("line", { x1: start.x, y1: start.y, x2: end.x, y2: end.y, class: "edge", "vector-effect": "non-scaling-stroke" }));
  }

  for (const node of Object.values(state.nodes)) {
    content.appendChild(makeSvg("circle", {
      cx: node.x, cy: node.y, r: 12 / state.zoom,
      class: node.id === state.selectedNode ? "node selected-node" : "node tree-node",
      "vector-effect": "non-scaling-stroke",
    }));
  }

  editorSvg.appendChild(content);
}

export function serializeTree() {
  const nodes = Object.values(state.nodes)
    .map((n) => ({ id: n.id, x: n.x, y: n.y }))
    .sort((a, b) => a.id - b.id);
  const edges = state.edges.map((e) => {
    const start = state.nodes[e.u];
    const end = state.nodes[e.v];
    const length = Math.max(Math.hypot(start.x - end.x, start.y - end.y) / 60, 1e-5);
    return { u: e.u, v: e.v, length };
  });
  return { nodes, edges };
}

export function resetTree() {
  state.nodes = { 0: { id: 0, x: 400, y: 280 } };
  state.edges = [];
  state.nextNodeId = 1;
  state.selectedNode = 0;
  state.draggingNode = null;
  state.zoom = 1;
  state.panOffset = { x: 0, y: 0 };
  state.isPanning = false;
  state.backgroundGesture = null;
  renderEditor();
  setStatus("Tree reset.");
}

export function generateRandomTree() {
  const targetLeaves = parseInt(document.getElementById("randomNodeCount").value, 10) || 6;
  if (targetLeaves < 2) return;
  state.nodes = { 0: { id: 0, x: 400, y: 280 } };
  state.edges = [];
  state.nextNodeId = 1;
  state.selectedNode = null;
  state.draggingNode = null;
  state.zoom = 1;
  state.panOffset = { x: 0, y: 0 };
  state.isPanning = false;
  state.backgroundGesture = null;

  const margin = 40;
  const width = 800;
  const height = 560;

  function ccw(A, B, C) { return (C.y - A.y) * (B.x - A.x) > (B.y - A.y) * (C.x - A.x); }
  function intersects(p1, p2, p3, p4) { 
    return ccw(p1, p3, p4) !== ccw(p2, p3, p4) && ccw(p1, p2, p3) !== ccw(p1, p2, p4); 
  }

  function getLeafCount() {
    if (state.edges.length === 0) return 1;
    const degrees = {};
    for (const node of Object.values(state.nodes)) degrees[node.id] = 0;
    for (const edge of state.edges) { degrees[edge.u]++; degrees[edge.v]++; }
    return Object.values(degrees).filter(deg => deg === 1).length;
  }

  let attempts = 0;
  const maxAttempts = 3000;
  while (getLeafCount() < targetLeaves && attempts < maxAttempts) {
    attempts++;
    const existingIds = Object.keys(state.nodes);
    const parentId = existingIds[Math.floor(Math.random() * existingIds.length)];
    const parentNode = state.nodes[parentId];
    const angle = Math.random() * Math.PI * 2;
    const dist = 30 + Math.random() * 45; 
    const nx = parentNode.x + dist * Math.cos(angle);
    const ny = parentNode.y + dist * Math.sin(angle);
    const newNode = { x: nx, y: ny };
    if (nx < margin || nx > width - margin || ny < margin || ny > height - margin) continue;
    let tooClose = false;
    for (const n of Object.values(state.nodes)) {
      if (Math.hypot(n.x - nx, n.y - ny) < 25) { tooClose = true; break; }
    }
    if (tooClose) continue;
    let crossing = false;
    for (const edge of state.edges) {
      if (edge.u == parentId || edge.v == parentId) continue;
      const uNode = state.nodes[edge.u];
      const vNode = state.nodes[edge.v];
      if (intersects(parentNode, newNode, uNode, vNode)) { crossing = true; break; }
    }
    if (crossing) continue;
    const newNodeId = state.nextNodeId++;
    state.nodes[newNodeId] = { id: newNodeId, x: nx, y: ny };
    state.edges.push({ u: parseInt(parentId, 10), v: newNodeId });
  }

  let changed = true;
  while (changed) {
    changed = false;
    const degrees = {};
    const neighbors = {};
    for (const id of Object.keys(state.nodes)) { degrees[id] = 0; neighbors[id] = []; }
    for (const edge of state.edges) { degrees[edge.u]++; degrees[edge.v]++; neighbors[edge.u].push(edge.v); neighbors[edge.v].push(edge.u); }
    for (const idStr of Object.keys(state.nodes)) {
      const id = parseInt(idStr, 10);
      if (degrees[id] === 2) {
        const u = neighbors[id][0];
        const v = neighbors[id][1];
        delete state.nodes[id];
        state.edges = state.edges.filter(e => 
          !( (e.u === id && e.v === u) || (e.u === u && e.v === id) || 
             (e.u === id && e.v === v) || (e.u === v && e.v === id) )
        );
        state.edges.push({ u: u, v: v });
        changed = true;
        break;
      }
    }
  }

  const remainingIds = Object.keys(state.nodes);
  state.selectedNode = remainingIds.length > 0 ? parseInt(remainingIds[0], 10) : null;
  renderEditor();
  const finalLeaves = getLeafCount();
  if (finalLeaves < targetLeaves) {
    setStatus(`Stopped at ${finalLeaves} leaf nodes (canvas got too crowded).`);
  } else {
    setStatus(`Generated random uniaxial tree with ${finalLeaves} leaf nodes.`);
  }
}
