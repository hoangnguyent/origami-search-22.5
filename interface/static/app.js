const SVG_NS = "http://www.w3.org/2000/svg";

const state = {
  nodes: {
    0: { id: 0, x: 300, y: 280 },
    1: { id: 1, x: 500, y: 280 },
  },
  edges: [{ u: 0, v: 1 }],
  nextNodeId: 2,
  selectedNode: 1,
  draggingNode: null,
  queryResult: null,
  activeResultIndex: null,
};
const red = "#ff6b6b";
const blue = "#4dabf7";
const grey = "#9aa8bf";
const black = "#2b3a4a";
const white = "#f0f3f7";
const COLORS = {
  rm: red,
  rv: blue,
  av: blue,
  hm: red,
  hv: blue,
  h: grey,
  v: blue,
  m: red,
  b: white,
};
const editorSvg = document.getElementById("editorSvg");
const resultsGrid = document.getElementById("resultsGrid");
const detailGrid = document.getElementById("detailGrid");
const statusEl = document.getElementById("status");
const resultSummary = document.getElementById("resultSummary");
const detailSummary = document.getElementById("detailSummary");
const tokenInput = document.getElementById("authToken");

tokenInput.value = localStorage.getItem("search22_interface_token") || "";
tokenInput.addEventListener("input", () => {
  localStorage.setItem("search22_interface_token", tokenInput.value);
});

document.getElementById("runQuery").addEventListener("click", runQuery);
document.getElementById("resetTree").addEventListener("click", resetTree);
document.addEventListener("keydown", onKeyDown);
editorSvg.addEventListener("mousedown", onEditorMouseDown);
window.addEventListener("mousemove", onEditorMouseMove);
window.addEventListener("mouseup", onEditorMouseUp);

renderEditor();

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.style.color = isError ? "var(--danger)" : "var(--muted)";
}

function resetTree() {
  state.nodes = {
    0: { id: 0, x: 300, y: 280 },
    1: { id: 1, x: 500, y: 280 },
  };
  state.edges = [{ u: 0, v: 1 }];
  state.nextNodeId = 2;
  state.selectedNode = 1;
  state.draggingNode = null;
  renderEditor();
  setStatus("Tree reset.");
}

function onKeyDown(event) {
  if (event.key === "Escape") {
    state.selectedNode = null;
    state.draggingNode = null;
    renderEditor();
  }

  if (event.key === "Backspace" && state.selectedNode !== null) {
    const target = state.selectedNode;
    delete state.nodes[target];
    state.edges = state.edges.filter((edge) => edge.u !== target && edge.v !== target);
    state.selectedNode = null;
    state.draggingNode = null;
    renderEditor();
  }
}

function getSvgPoint(event) {
  const point = editorSvg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const ctm = editorSvg.getScreenCTM();
  if (!ctm) {
    return { x: 0, y: 0 };
  }
  const local = point.matrixTransform(ctm.inverse());
  return { x: local.x, y: local.y };
}

function getClosestNode(x, y, hitRadius = 18) {
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

function onEditorMouseDown(event) {
  if (event.button !== 0) return;
  const { x, y } = getSvgPoint(event);
  const hit = getClosestNode(x, y);
  if (hit) {
    state.selectedNode = hit.id;
    state.draggingNode = hit.id;
    renderEditor();
    return;
  }

  if (state.selectedNode !== null) {
    const newNode = { id: state.nextNodeId, x, y };
    state.nodes[newNode.id] = newNode;
    state.edges.push({ u: state.selectedNode, v: newNode.id });
    state.nextNodeId += 1;
    state.selectedNode = newNode.id;
    renderEditor();
  }
}

function onEditorMouseMove(event) {
  if (state.draggingNode === null) return;
  const { x, y } = getSvgPoint(event);
  state.nodes[state.draggingNode].x = x;
  state.nodes[state.draggingNode].y = y;
  renderEditor();
}

function onEditorMouseUp() {
  state.draggingNode = null;
}

function renderEditor() {
  editorSvg.replaceChildren();
  editorSvg.appendChild(makeSvg("rect", { x: 0, y: 0, width: 800, height: 560, rx: 18, fill: "transparent" }));

  for (const edge of state.edges) {
    const start = state.nodes[edge.u];
    const end = state.nodes[edge.v];
    editorSvg.appendChild(makeSvg("line", { x1: start.x, y1: start.y, x2: end.x, y2: end.y, class: "edge" }));
  }

  for (const node of Object.values(state.nodes)) {
    editorSvg.appendChild(makeSvg("circle", {
      cx: node.x,
      cy: node.y,
      r: 12,
      class: node.id === state.selectedNode ? "node selected-node" : "node tree-node",
    }));

    const label = makeSvg("text", {
      x: node.x,
      y: node.y - 18,
      fill: "#d6def0",
      "font-size": 12,
      "text-anchor": "middle",
    });
    label.textContent = String(node.id);
    editorSvg.appendChild(label);
  }
}

function makeSvg(tag, attrs = {}) {
  const element = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function serializeTree() {
  const nodes = Object.values(state.nodes)
    .map((node) => ({ id: node.id, x: node.x, y: node.y }))
    .sort((a, b) => a.id - b.id);

  const edges = state.edges.map((edge) => {
    const start = state.nodes[edge.u];
    const end = state.nodes[edge.v];
    const length = Math.max(Math.hypot(start.x - end.x, start.y - end.y) / 60, 1e-5);
    return { u: edge.u, v: edge.v, length };
  });

  return { nodes, edges };
}

function selectedDbConfigs() {
  return [...document.querySelectorAll(".db-check")]
    .filter((input) => input.checked)
    .map((input) => ({ N: Number(input.dataset.n), symmetry: input.dataset.sym }));
}

async function runQuery() {
  try {
    setStatus("Querying backend...");
    const payload = {
      tree: serializeTree(),
      db_configs: selectedDbConfigs(),
      n: Number(document.getElementById("resultCount").value || 5),
    };

    const headers = { "Content-Type": "application/json" };
    if (tokenInput.value.trim()) {
      headers["X-Interface-Token"] = tokenInput.value.trim();
    }

    const response = await fetch("/api/query", {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    const rawText = await response.text();
    let data = {};
    if (rawText) {
      try {
        data = JSON.parse(rawText);
      } catch {
        data = { error: rawText.slice(0, 200) };
      }
    }
    if (!response.ok) {
      throw new Error(data.error || "Query failed");
    }

    state.queryResult = data;
    // store visual constants (colors, alpha) from server for exact parity
    state.visual = data.visual_constants || { plot_colors: {}, alpha: 0.1 };
    state.activeResultIndex = null;
    renderResults();
    renderDetail(null);
    resultSummary.textContent = `${data.results.length} result(s) loaded from ${data.db_configs.length} database(s).`;
    setStatus("Query complete.");
  } catch (error) {
    setStatus(error.message, true);
  }
}

function renderResults() {
  resultsGrid.replaceChildren();
  if (!state.queryResult) return;

  state.queryResult.results.forEach((result, index) => {
    const card = document.createElement("article");
    card.className = "result-card" + (state.activeResultIndex === index ? " active" : "");
    card.addEventListener("click", () => {
      state.activeResultIndex = index;
      renderResults();
      renderDetail(result);
    });

    const thumb = document.createElement("div");
    thumb.className = "thumb";
    const svg = makeSvg("svg", { viewBox: "0 0 220 220", class: "detail-svg" });
    renderCpSvg(svg, result.cp, 220, 220);
    thumb.appendChild(svg);

    const meta = document.createElement("div");
    meta.className = "result-meta";
    meta.innerHTML = `
      <div><strong>Rank ${result.rank ?? index + 1}</strong> · dist ${Number(result.distance).toFixed(5)}</div>
      <div>${result.N} ${result.symmetry}</div>
      <div>tiling ${result.tiling_id} · topology ${result.topology_id}</div>
    `;

    card.appendChild(thumb);
    card.appendChild(meta);
    resultsGrid.appendChild(card);
  });
}

function renderDetail(result) {
  detailGrid.replaceChildren();
  if (!result) {
    detailSummary.textContent = "Select a result.";
    return;
  }

  detailSummary.textContent = `Rank ${result.rank ?? "?"} · dist ${Number(result.distance).toFixed(5)} · ${result.N} ${result.symmetry}`;

  detailGrid.appendChild(panelSvg("Topology", result.topology, (svg) => renderGraphSvg(svg, result.topology, { nodeFill: "#a7c7ff" })));
  detailGrid.appendChild(panelSvg("Solved Tiling", result.solved_tiling, (svg) => renderGraphSvg(svg, result.solved_tiling, { nodeFill: "#8cffc1" })));
  detailGrid.appendChild(panelSvg("Crease Pattern", result.cp, (svg) => renderCpSvg(svg, result.cp, 420, 240)));
  detailGrid.appendChild(panelSvg("Folded State", result.fold, (svg) => renderFoldSvg(svg, result.fold)));
  detailGrid.appendChild(panelSvg("Resulting Tree", result.tree, (svg) => renderGraphSvg(svg, result.tree, { nodeFill: "#8cffc1" })));
  detailGrid.appendChild(panelSvg("Heat Profile", result.heat, (svg) => renderHeatSvg(svg, result.heat)));

  const meta = document.createElement("section");
  meta.className = "detail-card wide";
  meta.innerHTML = `
    <h3>Metadata</h3>
    <div class="detail-text">${escapeHtml(JSON.stringify({
      rank: result.rank,
      distance: result.distance,
      N: result.N,
      symmetry: result.symmetry,
      topology_id: result.topology_id,
      tiling_id: result.tiling_id,
    }, null, 2))}</div>
  `;
  detailGrid.appendChild(meta);
}

function panelSvg(title, payload, renderer) {
  const card = document.createElement("section");
  card.className = "detail-card";
  const heading = document.createElement("h3");
  heading.textContent = title;
  const svg = makeSvg("svg", { viewBox: "0 0 420 240", class: "detail-svg" });
  renderer(svg, payload);
  card.appendChild(heading);
  card.appendChild(svg);
  return card;
}
function renderCpSvg(svg, cp, width, height) {
  const bounds = boundsFromSegments(cp.segments);
  const scale = fitScale(bounds, width, height);
  
  // FIX: Force the frontend to use YOUR dark-mode colors, ignoring the backend
  const palette = COLORS; 

  function mapTypeToColor(rawType) {
    const t = (rawType == null) ? "" : String(rawType).trim().toLowerCase();
    
    if (palette[t]) return palette[t];
    
    // Common aliases
    if (t === "ax" || t === "aux") return palette.av || "#4dabf7";
    
    // Clean waterfall heuristics
    if (t.includes("m")) return palette.m; // Mountain (Red)
    if (t.includes("v")) return palette.v; // Valley (Blue)
    if (t.includes("b")) return palette.b; // Border (White)
    if (t.includes("h")) return palette.h; // Hinge (Grey)
    
    return palette.h || "#9aa8bf"; // Default unknown
  }

  for (const segment of cp.segments) {
    const stroke = mapTypeToColor(segment.type);
    // Refined stroke-width logic for better visual hierarchy
    const isThin = segment.type === "h" || String(segment.type).toLowerCase().includes("h");
    
    svg.appendChild(makeSvg("line", {
      x1: transformX(segment.x1, bounds, scale, width),
      y1: transformY(segment.y1, bounds, scale, height),
      x2: transformX(segment.x2, bounds, scale, width),
      y2: transformY(segment.y2, bounds, scale, height),
      stroke: stroke,
      "stroke-width": isThin ? 1.2 : 2.2,
      "stroke-linecap": "round",
      opacity: 0.85,
    }));
  }
}

function renderFoldSvg(svg, fold) {
  const faces = fold.faces || [];
  const bounds = boundsFromFaces(faces);
  const scale = fitScale(bounds, 420, 240);
  
  // FIX: Decouple alpha from the backend. Use a crisp dark-mode friendly base alpha.
  const BASE_ALPHA = 0.12; 
  
  faces.forEach((face, index) => {
    const mult = fold.multiplicities?.[index] || 1;
    // Mathematically stack the alpha for overlapping faces
    const alphaVal = 1 - Math.pow(1 - BASE_ALPHA, mult);
    
    svg.appendChild(makeSvg("polygon", {
      points: face.map((point) => `${transformX(point[0], bounds, scale, 420)},${transformY(point[1], bounds, scale, 240)}`).join(" "),
      fill: `rgba(122, 211, 255, ${alphaVal})`,
      stroke: "rgba(255,255,255,0.30)", // Subdued stroke for dark mode
      "stroke-width": 0.5, // Added a slight stroke to define overlapping boundaries
    }));
  });
}

function renderGraphSvg(svg, graph, { nodeFill = "#9ed6ff" } = {}) {
  const bounds = boundsFromGraph(graph);
  const scale = fitScale(bounds, 420, 240);
  for (const edge of graph.edges) {
    const start = pointForNode(graph, edge.u);
    const end = pointForNode(graph, edge.v);
    svg.appendChild(makeSvg("line", {
      x1: transformX(start[0], bounds, scale, 420),
      y1: transformY(start[1], bounds, scale, 240),
      x2: transformX(end[0], bounds, scale, 420),
      y2: transformY(end[1], bounds, scale, 240),
      class: "edge",
    }));
  }
  for (const node of graph.nodes) {
    if (!node.pos) continue;
    svg.appendChild(makeSvg("circle", {
      cx: transformX(node.pos[0], bounds, scale, 420),
      cy: transformY(node.pos[1], bounds, scale, 240),
      r: 4,
      fill: nodeFill,
      class: "node",
    }));
  }
}

function renderHeatSvg(svg, heat) {
  const width = 420;
  const height = 240;
  const margin = 28;
  const xValues = heat.t_scales || [];
  const query = heat.query || [];
  const result = heat.result || [];
  if (!xValues.length || !query.length || !result.length) {
    svg.appendChild(makeSvg("text", { x: 18, y: 32, fill: "#9daccc", "font-size": 14 })).textContent = "Heat profile unavailable.";
    return;
  }
  const xMin = Math.min(...xValues);
  const xMax = Math.max(...xValues);
  const yValues = [...query, ...result];
  const yMin = Math.min(...yValues);
  const yMax = Math.max(...yValues);

  drawAxes(svg, width, height, margin);
  // Draw semilog-x ticks and labels
  const safeMin = Math.max(xMin, 1e-12);
  const logMin = Math.log10(safeMin);
  const logMax = Math.log10(Math.max(xMax, safeMin * 10));
  const powMin = Math.floor(logMin);
  const powMax = Math.ceil(logMax);
  for (let p = powMin; p <= powMax; p++) {
    const val = Math.pow(10, p);
    if (val < safeMin || val > xMax) continue;
    const px = margin + ((Math.log10(val) - logMin) / (logMax - logMin)) * (width - margin * 2);
    // Tick
    svg.appendChild(makeSvg("line", { x1: px, y1: height - margin, x2: px, y2: height - margin + 6, stroke: "#2b3a4a", "stroke-width": 1 }));
    // Label
    svg.appendChild(makeSvg("text", { x: px + 4, y: height - margin + 18, fill: "#9daccc", "font-size": 11 })).textContent = `10^${p}`;
  }

  svg.appendChild(makePolyline(xValues, query, xMin, xMax, yMin, yMax, width, height, margin, "#e6e8ef"));
  svg.appendChild(makePolyline(xValues, result, xMin, xMax, yMin, yMax, 2*width, height, margin, "#7ad3ff"));
}

function drawAxes(svg, width, height, margin) {
  svg.appendChild(makeSvg("line", { x1: margin, y1: height - margin, x2: width - margin, y2: height - margin, class: "gridline" }));
  svg.appendChild(makeSvg("line", { x1: margin, y1: margin, x2: margin, y2: height - margin, class: "gridline" }));
}

function makePolyline(xValues, yValues, xMin, xMax, yMin, yMax, width, height, margin, color) {
  const safeMin = Math.max(xMin, 1e-12);
  const logSpan = Math.max(Math.log10(Math.max(xMax, safeMin * 10)) - Math.log10(safeMin), 1e-9);
  const ySpan = Math.max(yMax - yMin, 1e-9);
  const points = xValues.map((x, index) => {
    const safeX = Math.max(x, safeMin);
    const px = margin + ((Math.log10(safeX) - Math.log10(safeMin)) / logSpan) * (width - margin * 2);
    const py = height - margin - ((yValues[index] - yMin) / ySpan) * (height - margin * 2);
    return `${px},${py}`;
  }).join(" ");
  return makeSvg("polyline", { points, fill: "none", stroke: color, "stroke-width": 2.3, "stroke-linejoin": "round", "stroke-linecap": "round" });
}

function boundsFromSegments(segments) {
  const xs = [];
  const ys = [];
  for (const segment of segments) {
    xs.push(segment.x1, segment.x2);
    ys.push(segment.y1, segment.y2);
  }
  return boundsFromArrays(xs, ys);
}

function boundsFromGraph(graph) {
  const xs = [];
  const ys = [];
  for (const node of graph.nodes) {
    if (node.pos) {
      xs.push(node.pos[0]);
      ys.push(node.pos[1]);
    }
  }
  return boundsFromArrays(xs, ys);
}

function boundsFromFaces(faces) {
  const xs = [];
  const ys = [];
  for (const face of faces) {
    for (const point of face) {
      xs.push(point[0]);
      ys.push(point[1]);
    }
  }
  return boundsFromArrays(xs, ys);
}

function boundsFromArrays(xs, ys) {
  if (!xs.length || !ys.length) {
    return { minX: 0, maxX: 1, minY: 0, maxY: 1 };
  }
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  return { minX, maxX, minY, maxY };
}

function fitScale(bounds, width, height) {
  const padding = 20;
  const dx = Math.max(bounds.maxX - bounds.minX, 1e-6);
  const dy = Math.max(bounds.maxY - bounds.minY, 1e-6);
  return Math.min((width - padding * 2) / dx, (height - padding * 2) / dy);
}

function transformX(x, bounds, scale, width) {
  const padding = 20;
  return padding + (x - bounds.minX) * scale + (width - 2 * padding - (bounds.maxX - bounds.minX) * scale) / 2;
}

function transformY(y, bounds, scale, height) {
  const padding = 20;
  return height - (padding + (y - bounds.minY) * scale + (height - 2 * padding - (bounds.maxY - bounds.minY) * scale) / 2);
}

function pointForNode(graph, nodeId) {
  const found = graph.nodes.find((node) => node.id === nodeId);
  return found && found.pos ? found.pos : [0, 0];
}

function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
