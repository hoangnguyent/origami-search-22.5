const SVG_NS = "http://www.w3.org/2000/svg";

const state = {
  nodes: {
    0: { id: 0, x: 400, y: 280 },
  },
  edges: [],
  nextNodeId: 1,
  selectedNode: 0,
  draggingNode: null,
  queryResult: null,
  queryNodeCount: 1,
  currentDetailResult: null, // Track the open modal result
  currentDetailIndex: null,
  detailViewModes: {
    left: "cp",
    right: "tree",
  },
};

const editorSvg = document.getElementById("editorSvg");
const resultsGrid = document.getElementById("resultsGrid");
const statusEl = document.getElementById("status");
const resultSummary = document.getElementById("resultSummary");
const detailModal = document.getElementById("detailModal");
const modalGrid = document.getElementById("modalGrid");
const modalTitle = document.getElementById("modalTitle");
const modalMeta = document.getElementById("modalMeta");
const detailPrevBtn = document.getElementById("detailPrevBtn");
const detailNextBtn = document.getElementById("detailNextBtn");
const settingsModal = document.getElementById("settingsModal");
const settingsBtn = document.getElementById("settingsBtn");
const closeSettingsModal = document.getElementById("closeSettingsModal");
const themeSelect = document.getElementById("themeSelect");
const languageSelect = document.getElementById("languageSelect");
const resultsThumbModeSelect = document.getElementById("resultsThumbMode");

const THEME_STORAGE_KEY = "search225-theme-preference";
const DETAIL_LEFT_VIEW_KEY = "search225-detail-left-view";
const DETAIL_RIGHT_VIEW_KEY = "search225-detail-right-view";
const systemThemeQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;

function readStoredDetailView(key, fallback) {
  try {
    const stored = localStorage.getItem(key);
    return stored || fallback;
  } catch {
    return fallback;
  }
}

function persistDetailView(side, value) {
  try {
    localStorage.setItem(side === "left" ? DETAIL_LEFT_VIEW_KEY : DETAIL_RIGHT_VIEW_KEY, value);
  } catch {}
}

function syncDetailViewPreferences() {
  state.detailViewModes.left = readStoredDetailView(DETAIL_LEFT_VIEW_KEY, state.detailViewModes.left);
  state.detailViewModes.right = readStoredDetailView(DETAIL_RIGHT_VIEW_KEY, state.detailViewModes.right);
}

function readStoredThemePreference() {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return stored === 'light' || stored === 'dark' || stored === 'system' ? stored : null;
  } catch { return null; }
}

function getEffectiveTheme(themePreference) {
  if (themePreference === 'light' || themePreference === 'dark') return themePreference;
  return systemThemeQuery && systemThemeQuery.matches ? 'light' : 'dark';
}

function applyTheme(themePreference, persist = true) {
  const normalized = themePreference === 'light' || themePreference === 'dark' ? themePreference : 'system';
  const effective = getEffectiveTheme(normalized);
  document.documentElement.dataset.theme = effective;
  if (themeSelect) themeSelect.value = normalized;
  if (persist) {
    try { localStorage.setItem(THEME_STORAGE_KEY, normalized); } catch {}
  }
}

function setThemePreference(themePreference) { applyTheme(themePreference, true); }

function closeDetailModal() {
  detailModal.classList.add("hidden");
  detailModal.setAttribute("aria-hidden", "true");
  state.currentDetailResult = null;
  state.currentDetailIndex = null;
}

function updateDetailView(side, value) {
  if (state.detailViewModes[side] === value) return;
  state.detailViewModes[side] = value;
  persistDetailView(side, value);
  if (state.currentDetailResult && state.currentDetailIndex !== null) {
    renderDetail(state.currentDetailResult, state.currentDetailIndex);
  }
}

function navigateDetail(step) {
  if (!state.queryResult?.results?.length || state.currentDetailIndex === null) return;
  const nextIndex = state.currentDetailIndex + step;
  if (nextIndex < 0 || nextIndex >= state.queryResult.results.length) return;
  renderDetail(state.queryResult.results[nextIndex], nextIndex);
}

// Event Listeners
document.getElementById("runQuery").addEventListener("click", runQuery);
document.getElementById("resetTree").addEventListener("click", resetTree);
document.getElementById("closeModal").addEventListener("click", closeDetailModal);
if (detailPrevBtn) detailPrevBtn.addEventListener("click", () => navigateDetail(-1));
if (detailNextBtn) detailNextBtn.addEventListener("click", () => navigateDetail(1));
if (detailModal) detailModal.addEventListener("click", (event) => {
  if (event.target === detailModal) closeDetailModal();
});
// Settings modal wiring
if (settingsBtn) settingsBtn.addEventListener("click", () => settingsModal && settingsModal.classList.remove("hidden"));
if (closeSettingsModal) closeSettingsModal.addEventListener("click", () => settingsModal && settingsModal.classList.add("hidden"));
if (settingsModal) settingsModal.addEventListener("click", (e) => { if (e.target === settingsModal) settingsModal.classList.add("hidden"); });
if (themeSelect) themeSelect.addEventListener("change", () => setThemePreference(themeSelect.value));
if (languageSelect) languageSelect.addEventListener("change", () => { try { localStorage.setItem('search225-language-preference', languageSelect.value); } catch {} });
if (resultsThumbModeSelect) {
  resultsThumbModeSelect.addEventListener("change", () => {
    if (state.queryResult) renderResults();
  });
}
document.addEventListener("keydown", onKeyDown);
editorSvg.addEventListener("mousedown", onEditorMouseDown);
window.addEventListener("mousemove", onEditorMouseMove);
window.addEventListener("mouseup", onEditorMouseUp);
document.getElementById("randomTreeBtn").addEventListener("click", generateRandomTree);

document.getElementById("downloadCpBtn").addEventListener("click", () => {
  if (!state.currentDetailResult) return;
  
  const cp = state.currentDetailResult.cp;
  const vertices = [];
  const vMap = new Map();
  const edges_vertices = [];
  const edges_assignment = [];
  
  // Helper to deduplicate vertices using stringified coordinates (avoids float fuzziness)
  function getVertexId(x, y) {
    const key = x.toFixed(6) + "," + y.toFixed(6);
    if (vMap.has(key)) return vMap.get(key);
    
    const id = vertices.length;
    vertices.push([x, y]);
    vMap.set(key, id);
    return id;
  }
  
  // Hardcoded assignments per your exact specification
  function getFoldType(rawType) {
    if (!rawType) return "F";
    const t = String(rawType).trim().toLowerCase();
    
    if (t === "b") return "B";
    if (t === "rm" || t === "m" || t === "hm") return "M";
    if (t === "rv" || t === "av" || t === "v" || t === "hv") return "V";
    if (t === "h" || t === "aux" || t === "ax") return "F";
    
    // Fallbacks just in case
    if (t.includes("m")) return "M";
    if (t.includes("v")) return "V";
    return "F";
  }

  // Process all segments into the FOLD graph structure
  cp.segments.forEach(seg => {
    const u = getVertexId(seg.x1, seg.y1);
    const v = getVertexId(seg.x2, seg.y2);
    
    edges_vertices.push([u, v]);
    edges_assignment.push(getFoldType(seg.type));
  });

  // Construct the .fold JSON
  const foldData = {
    file_spec: 1.1,
    file_creator: "SEARCH 22.5",
    vertices_coords: vertices,
    edges_vertices: edges_vertices,
    edges_assignment: edges_assignment
  };

  // Create a Blob and trigger the file download
  const blob = new Blob([JSON.stringify(foldData, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  
  // Create a descriptive filename with Tiling ID
  const rank = state.currentDetailResult.rank || 1;
  const N = state.currentDetailResult.N || "N";
  const sym = state.currentDetailResult.symmetry || "sym";
  const tilingId = state.currentDetailResult.tiling_id || "unknown"; // FIX: Grab the tiling ID
  
  a.href = url;
  // FIX: Added the tiling ID to the download filename
  a.download = `cp${N}${sym}${tilingId}.fold`; 
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
});

// Apply stored or system theme before initial render
applyTheme(readStoredThemePreference() || 'system', false);
syncDetailViewPreferences();
renderEditor();

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.style.color = isError ? "var(--danger)" : "var(--muted)";
}

function resetTree() {
  state.nodes = { 0: { id: 0, x: 400, y: 280 } };
  state.edges = [];
  state.nextNodeId = 1;
  state.selectedNode = 0;
  state.draggingNode = null;
  renderEditor();
  setStatus("Tree reset.");
}
function generateRandomTree() {
  const targetLeaves = parseInt(document.getElementById("randomNodeCount").value, 10) || 6;
  if (targetLeaves < 2) return;

  // Clear existing tree
  state.nodes = { 0: { id: 0, x: 400, y: 280 } };
  state.edges = [];
  state.nextNodeId = 1;
  state.selectedNode = null;
  state.draggingNode = null;

  const margin = 40;
  const width = 800;
  const height = 560;

  // Helpers for planar checking
  function ccw(A, B, C) { return (C.y - A.y) * (B.x - A.x) > (B.y - A.y) * (C.x - A.x); }
  function intersects(p1, p2, p3, p4) { 
    return ccw(p1, p3, p4) !== ccw(p2, p3, p4) && ccw(p1, p2, p3) !== ccw(p1, p2, p4); 
  }

  // Helper to count current leaf nodes
  function getLeafCount() {
    if (state.edges.length === 0) return 1;
    const degrees = {};
    for (const node of Object.values(state.nodes)) degrees[node.id] = 0;
    for (const edge of state.edges) {
      degrees[edge.u]++;
      degrees[edge.v]++;
    }
    return Object.values(degrees).filter(deg => deg === 1).length;
  }

  let attempts = 0;
  const maxAttempts = 3000;

  // --- PHASE 1: GENERATION ---
  while (getLeafCount() < targetLeaves && attempts < maxAttempts) {
    attempts++;
    
    const existingIds = Object.keys(state.nodes);
    const parentId = existingIds[Math.floor(Math.random() * existingIds.length)];
    const parentNode = state.nodes[parentId];

    const angle = Math.random() * Math.PI * 2;
    // Keep edges slightly shorter to allow more branching in the canvas
    const dist = 30 + Math.random() * 45; 

    const nx = parentNode.x + dist * Math.cos(angle);
    const ny = parentNode.y + dist * Math.sin(angle);
    const newNode = { x: nx, y: ny };

    // Bounds check
    if (nx < margin || nx > width - margin || ny < margin || ny > height - margin) continue;

    // Proximity check
    let tooClose = false;
    for (const n of Object.values(state.nodes)) {
      if (Math.hypot(n.x - nx, n.y - ny) < 25) { tooClose = true; break; }
    }
    if (tooClose) continue;

    // Planarity check
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

  // --- PHASE 2: SIMPLIFICATION (Remove Degree-2 Nodes) ---
  let changed = true;
  while (changed) {
    changed = false;
    const degrees = {};
    const neighbors = {};
    
    // Build incidence maps
    for (const id of Object.keys(state.nodes)) {
      degrees[id] = 0;
      neighbors[id] = [];
    }
    for (const edge of state.edges) {
      degrees[edge.u]++;
      degrees[edge.v]++;
      neighbors[edge.u].push(edge.v);
      neighbors[edge.v].push(edge.u);
    }

    // Find and collapse the first degree-2 node we see
    for (const idStr of Object.keys(state.nodes)) {
      const id = parseInt(idStr, 10);
      if (degrees[id] === 2) {
        const u = neighbors[id][0];
        const v = neighbors[id][1];
        
        // Delete the node
        delete state.nodes[id];
        
        // Filter out the two edges connecting to it
        state.edges = state.edges.filter(e => 
          !( (e.u === id && e.v === u) || (e.u === u && e.v === id) || 
             (e.u === id && e.v === v) || (e.u === v && e.v === id) )
        );
        
        // Bridge the neighbors
        state.edges.push({ u: u, v: v });
        
        changed = true;
        break; // Break to safely rebuild the degrees map for the next pass
      }
    }
  }

  // Focus a remaining node and render
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

function onKeyDown(event) {
  if (event.key === "Escape") {
    // Close modal on Escape
    if (!detailModal.classList.contains("hidden")) {
      closeDetailModal();
      return;
    }
    state.selectedNode = null;
    state.draggingNode = null;
    renderEditor();
  }

  if ((event.key === "Backspace" || event.key === "Delete") && state.selectedNode !== null) {
    event.preventDefault();

    const target = state.selectedNode;

    const incidentEdges = state.edges.filter((edge) => edge.u === target || edge.v === target);

    if (incidentEdges.length > 2) {
      setStatus("Cannot delete a vertex with more than 2 connections", true);
      return;
    }

    if (incidentEdges.length === 2) {
      const neighbors = incidentEdges.map((edge) => (edge.u === target ? edge.v : edge.u));
      delete state.nodes[target];
      state.edges = state.edges.filter((edge) => edge.u !== target && edge.v !== target);
      state.edges.push({ u: neighbors[0], v: neighbors[1] });
      state.selectedNode = null;
      state.draggingNode = null;
      renderEditor();
      return;
    }

    if (incidentEdges.length === 1) {
      const [edge] = incidentEdges;
      const nextSelected = edge.u === target ? edge.v : edge.u;
      delete state.nodes[target];
      state.edges = state.edges.filter((edge) => edge.u !== target && edge.v !== target);
      state.selectedNode = nextSelected ?? null;
      state.draggingNode = null;
      renderEditor();
      return;
    }

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
  return ctm ? point.matrixTransform(ctm.inverse()) : { x: 0, y: 0 };
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
  
  // 1. If clicking an existing node, select and prepare to drag it
  if (hit) {
    state.selectedNode = hit.id;
    state.draggingNode = hit.id;
    renderEditor();
    return;
  }

  // 2. If clicking empty space with a node selected, branch off it
  if (state.selectedNode !== null) {
    const newNode = { id: state.nextNodeId, x, y };
    state.nodes[newNode.id] = newNode;
    state.edges.push({ u: state.selectedNode, v: newNode.id });
    state.nextNodeId += 1;
    
    // THE FIX: If Shift is held down, do NOT advance the selection.
    // This leaves the hub node selected so you can keep clicking to form a star.
    if (!event.shiftKey) {
      state.selectedNode = newNode.id;
    }
    
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
      cx: node.x, cy: node.y, r: 12,
      class: node.id === state.selectedNode ? "node selected-node" : "node tree-node",
    }));
    // const label = makeSvg("text", { x: node.x, y: node.y - 18, fill: "#d6def0", "font-size": 12, "text-anchor": "middle" });
    // label.textContent = String(node.id);
    // editorSvg.appendChild(label);
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

function selectedDbConfigs() {
  const isDiagOnly = !document.getElementById("diagToggle").checked;
  if (isDiagOnly) {
    return [
      { N: 3, symmetry: "diag" },
      { N: 4, symmetry: "diag" },
      { N: 5, symmetry: "diag" }
    ];
  } else {
    // If not diag only, load everything
    return [
      { N: 3, symmetry: "diag" },
      { N: 3, symmetry: "none" },
      { N: 4, symmetry: "diag" },
      { N: 4, symmetry: "none" },
      { N: 4, symmetry: "book" },
      { N: 5, symmetry: "diag" }
    ];
  }
}

async function runQuery() {
  try {
    const tree = serializeTree();
    if (tree.edges.length < 4) {
      setStatus("Tree is too simple. Add at least 4 edges before running a query.", true);
      return;
    }

    setStatus("Querying backend...");
    const t0 = Date.now();
    const payload = {
      tree,
      db_configs: selectedDbConfigs(),
      n: Number(document.getElementById("resultCount").value || 5),
    };

    const response = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const rawText = await response.text();
    let data = {};
    if (rawText) {
      try { data = JSON.parse(rawText); } 
      catch { data = { error: rawText.slice(0, 200) }; }
    }
    if (!response.ok) throw new Error(data.error || "Query failed");
    
    state.queryResult = data;
    state.queryNodeCount = Math.max(1, tree.nodes.length || 0);
    const tf = Date.now()
    renderResults();
    resultSummary.textContent = `${data.results.length} result(s) loaded.`;
    const n = Number(document.getElementById("resultCount").value || 5);
    const isDiagOnly = !document.getElementById("diagToggle").checked;
    setStatus(`Successfully queried ${n} crease patterns. Database size: ${isDiagOnly? "586,695": "958,770"}. Query time: ${((tf-t0)/1000).toFixed(2)}s`);

  } catch (error) {
    setStatus(error.message, true);
  }
}

function renderResults() {
  resultsGrid.replaceChildren();
  if (!state.queryResult) return;
  const thumbMode = resultsThumbModeSelect?.value || "cp";

  state.queryResult.results.forEach((result, index) => {
    const card = document.createElement("article");
    card.className = "result-card";
    
    // Clicking thumbnail opens the detail modal
    card.addEventListener("click", () => renderDetail(result, index));

    const thumb = document.createElement("div");
    thumb.className = "thumb";
    
    // FIX: Using the new thumb-svg class so it fills the 1:1 square
    const svg = makeSvg("svg", { viewBox: "0 0 220 220", class: "thumb-svg" });
    if (thumbMode === "tree" && result.tree) {
      renderGraphSvg(svg, result.tree, { nodeFill: "#8cffc1", width: 220, height: 220 });
    } else if (thumbMode === "packing" && result.packing) {
      renderPackingSvg(svg, result.packing, 220, 220);
    } else if (thumbMode === "fold" && result.fold) {
      renderFoldSvg(svg, result.fold, 220, 220)
    }
    else {
      renderCpSvg(svg, result.cp, 220, 220);
    }
    thumb.appendChild(svg);

    const meta = document.createElement("div");
    const quality = getMatchQuality(result.distance);
    meta.className = "result-meta";
    meta.dataset.quality = quality;
    meta.classList.add("match-quality");
    meta.innerHTML = `
      <div><strong>Option ${result.rank ?? index + 1}</strong></div>
      <div>Match quality: ${quality}</div>
      <div>ID: ${result.N}${symmetry_abbr[result.symmetry]}.${result.tiling_id}</div>
    `;

    card.appendChild(thumb);
    card.appendChild(meta);
    resultsGrid.appendChild(card);
  });
}
const symmetry_abbr = {
  "diag":"d",
  "book":"b",
  "none":"n"
}

function getMatchQuality(distance) {
  const value = Number(distance*state.queryNodeCount**2);
  if (!Number.isFinite(value)) return "unknown";
  if (value < 50) return "Perfect";
  if (value < 150) return "Good";
  if (value < 500) return "Acceptable";
  if (value < 1500.0) return "Poor";
  return "Terrible";
}

function renderDetail(result, index) {
  state.currentDetailResult = result; // Save active result for export
  state.currentDetailIndex = index;
  
  modalGrid.replaceChildren();
  if (!result) return;

  modalTitle.textContent = `Option ${result.rank ?? index + 1}`;
  const quality = getMatchQuality(result.distance);
  modalMeta.dataset.quality = quality;
  modalMeta.classList.add("match-quality");
  modalMeta.textContent = `Match quality: ${quality} • Normalized distance: ${(result.distance*state.queryNodeCount**2).toFixed(4)} • Tiling ID: ${result.N}${symmetry_abbr[result.symmetry]}.${result.tiling_id}`;

  renderDetailBody(result);
  updateDetailNavButtons();

  detailModal.classList.remove("hidden");
  detailModal.setAttribute("aria-hidden", "false");
}

function updateDetailNavButtons() {
  if (!detailPrevBtn || !detailNextBtn) return;
  const total = state.queryResult?.results?.length || 0;
  detailPrevBtn.disabled = state.currentDetailIndex === null || state.currentDetailIndex <= 0;
  detailNextBtn.disabled = state.currentDetailIndex === null || state.currentDetailIndex >= total - 1;
}

function renderDetailBody(result) {
  const leftPane = buildDetailPane({
    side: "left",
    activeValue: state.detailViewModes.left,
    options: [
      { value: "cp", label: "Crease pattern" },
      { value: "packing", label: "Packing" },
    ],
    renderActive: (svg, currentValue) => {
      if (currentValue === "packing" && result.packing) {
        renderPackingSvg(svg, result.packing, 420, 260);
      } else {
        renderCpSvg(svg, result.cp, 420, 260);
      }
    },
  });

  const rightPane = buildDetailPane({
    side: "right",
    activeValue: state.detailViewModes.right,
    options: [
      { value: "tree", label: "Tree" },
      { value: "fold", label: "Folded form" },
    ],
    renderActive: (svg, currentValue) => {
      if (currentValue === "fold" && result.fold) {
        renderFoldSvg(svg, result.fold);
      } else {
        renderGraphSvg(svg, result.tree, { nodeFill: "#8cffc1", width: 420, height: 260 });
      }
    },
  });

  modalGrid.appendChild(leftPane);
  modalGrid.appendChild(rightPane);
}

function buildDetailPane({ side, activeValue, options, renderActive }) {
  const panel = document.createElement("section");
  panel.className = "detail-panel";

  const toggleGroup = document.createElement("div");
  toggleGroup.className = "detail-toggle-group";

  options.forEach((option) => {
    const label = document.createElement("label");
    label.className = "detail-toggle-option";
    const input = document.createElement("input");
    input.type = "radio";
    input.name = `detail-${side}-mode`;
    input.value = option.value;
    input.checked = option.value === activeValue;
    input.addEventListener("change", () => {
      if (input.checked) updateDetailView(side, option.value);
    });
    const span = document.createElement("span");
    span.textContent = option.label;
    label.appendChild(input);
    label.appendChild(span);
    toggleGroup.appendChild(label);
  });

  const body = document.createElement("div");
  body.className = "detail-panel-body";
  const svg = makeSvg("svg", { viewBox: "0 0 420 260", class: "detail-svg" });
  renderActive(svg, activeValue);
  body.appendChild(svg);

  panel.appendChild(body);
  panel.appendChild(toggleGroup);
  return panel;
}

// --------------------------------------------------------
// Render Utilities 
// --------------------------------------------------------

function renderCpSvg(svg, cp, width, height, options = {}) {
  const bounds = boundsFromSegments(cp.segments);
  const scale = fitScale(bounds, width, height);
  for (const segment of cp.segments) {
    const mv = (segment.type == null) ? "" : String(segment.type).trim().toLowerCase();
    const strokeWidth = mv === "h" ? 1 : 2;
    const classes = ["cp-segment", `cp-${mv}`];
    // if (mv === "h") classes.push(hingeClass);

    const line = makeSvg("line", {
      x1: transformX(segment.x1, bounds, scale, width),
      y1: transformY(segment.y1, bounds, scale, height),
      x2: transformX(segment.x2, bounds, scale, width),
      y2: transformY(segment.y2, bounds, scale, height),
      class: classes.join(" "),
    });
    line.setAttribute('stroke-width', String(strokeWidth));
    // line.setAttribute('stroke-linecap', 'round');
    line.style.strokeWidth = String(strokeWidth);
    // line.style.strokeLinecap = "round";
    svg.appendChild(line);
  }
}

function renderPackingSvg(svg, cp, width, height, options = {}) {
  const bounds = boundsFromSegments(cp.segments);
  const scale = fitScale(bounds, width, height);
  for (const segment of cp.segments) {
    const mv = (segment.type == null) ? "" : String(segment.type).trim().toLowerCase();
    const strokeWidth = mv === "h" ? 2.5 : mv === "b"? 2: 0.7;
    const classes = ["cp-segment", `packing-${mv}`];
    // if (mv === "h") classes.push(hingeClass);

    const line = makeSvg("line", {
      x1: transformX(segment.x1, bounds, scale, width),
      y1: transformY(segment.y1, bounds, scale, height),
      x2: transformX(segment.x2, bounds, scale, width),
      y2: transformY(segment.y2, bounds, scale, height),
      class: classes.join(" "),
    });
    line.setAttribute('stroke-width', String(strokeWidth));
    // line.setAttribute('stroke-linecap', 'round');
    line.style.strokeWidth = String(strokeWidth);
    // line.style.strokeLinecap = "round";
    svg.appendChild(line);
  }
}

function renderFoldSvg(svg, fold) {
  const faces = fold.faces || [];
  const bounds = boundsFromFaces(faces);
  const scale = fitScale(bounds, 420, 240);
  const BASE_ALPHA = 0.12; 
  
  faces.forEach((face, index) => {
    const mult = fold.multiplicities?.[index] || 1;
    const alphaVal = 1 - Math.pow(1 - BASE_ALPHA, mult);
    
    svg.appendChild(makeSvg("polygon", {
      points: face.map((point) => `${transformX(point[0], bounds, scale, 420)},${transformY(point[1], bounds, scale, 240)}`).join(" "),
      fill: `rgba(122, 211, 255, ${alphaVal})`,
      stroke: "rgba(255,255,255,0.30)", 
      "stroke-width": 0.5, 
    }));
  });
}

function renderGraphSvg(svg, graph, { nodeFill = "#9ed6ff", width = 420, height = 240 } = {}) {
  const bounds = boundsFromGraph(graph);
  const scale = fitScale(bounds, width, height);
  for (const edge of graph.edges) {
    const start = pointForNode(graph, edge.u);
    const end = pointForNode(graph, edge.v);
    svg.appendChild(makeSvg("line", {
      x1: transformX(start[0], bounds, scale, width),
      y1: transformY(start[1], bounds, scale, height),
      x2: transformX(end[0], bounds, scale, width),
      y2: transformY(end[1], bounds, scale, height),
      class: "edge",
    }));
  }
  for (const node of graph.nodes) {
    if (!node.pos) continue;
    svg.appendChild(makeSvg("circle", {
      cx: transformX(node.pos[0], bounds, scale, width),
      cy: transformY(node.pos[1], bounds, scale, height),
      r: 4, fill: nodeFill, class: "node",
    }));
  }
}
function renderHeatSvg(svg, heat) {
  const width = 420, height = 240, margin = 28;
  const xValues = heat.t_scales || [], query = heat.query || [], result = heat.result || [];
  if (!xValues.length || !query.length || !result.length) return;
  
  const xMin = Math.min(...xValues), xMax = Math.max(...xValues);
  const yValues = [...query, ...result];
  const yMin = Math.min(...yValues), yMax = Math.max(...yValues);

  drawAxes(svg, width, height, margin);
  const safeMin = Math.max(xMin, 1e-12);
  const logMin = Math.log10(safeMin), logMax = Math.log10(Math.max(xMax, safeMin * 10));
  
  for (let p = Math.floor(logMin); p <= Math.ceil(logMax); p++) {
    const val = Math.pow(10, p);
    if (val < safeMin || val > xMax) continue;
    const px = margin + ((Math.log10(val) - logMin) / (logMax - logMin)) * (width - margin * 2);
    svg.appendChild(makeSvg("line", { x1: px, y1: height - margin, x2: px, y2: height - margin + 6, stroke: "#2b3a4a", "stroke-width": 1 }));
    svg.appendChild(makeSvg("text", { x: px + 4, y: height - margin + 18, fill: "#9daccc", "font-size": 11 })).textContent = `10^${p}`;
  }

  // Draw lines with two distinct blues
  const colorQuery = "#5b7b9e"; // Muted slate blue
  const colorResult = "#7ad3ff"; // Vibrant cyan
  
  svg.appendChild(makePolyline(xValues, query, xMin, xMax, yMin, yMax, width, height, margin, colorQuery));
  svg.appendChild(makePolyline(xValues, result, xMin, xMax, yMin, yMax, width, height, margin, colorResult));

  // Build the Legend
  const legend = makeSvg("g", {});
  
  // Query Legend
  legend.appendChild(makeSvg("line", { x1: width - 80, y1: 20, x2: width - 60, y2: 20, stroke: colorQuery, "stroke-width": 2.3 }));
  const qText = makeSvg("text", { x: width - 55, y: 24, fill: "#9daccc", "font-size": 11 });
  qText.textContent = "Query";
  legend.appendChild(qText);
  
  // Result Legend
  legend.appendChild(makeSvg("line", { x1: width - 80, y1: 36, x2: width - 60, y2: 36, stroke: colorResult, "stroke-width": 2.3 }));
  const rText = makeSvg("text", { x: width - 55, y: 40, fill: "#9daccc", "font-size": 11 });
  rText.textContent = "Result";
  legend.appendChild(rText);

  svg.appendChild(legend);
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
    const px = margin + ((Math.log10(Math.max(x, safeMin)) - Math.log10(safeMin)) / logSpan) * (width - margin * 2);
    const py = height - margin - ((yValues[index] - yMin) / ySpan) * (height - margin * 2);
    return `${px},${py}`;
  }).join(" ");
  return makeSvg("polyline", { points, fill: "none", stroke: color, "stroke-width": 2.3, "stroke-linejoin": "round", "stroke-linecap": "round" });
}

function boundsFromSegments(segments) {
  const xs = [], ys = [];
  segments.forEach(s => { xs.push(s.x1, s.x2); ys.push(s.y1, s.y2); });
  return boundsFromArrays(xs, ys);
}
function boundsFromGraph(graph) {
  const xs = [], ys = [];
  graph.nodes.forEach(n => { if (n.pos) { xs.push(n.pos[0]); ys.push(n.pos[1]); }});
  return boundsFromArrays(xs, ys);
}
function boundsFromFaces(faces) {
  const xs = [], ys = [];
  faces.forEach(f => f.forEach(p => { xs.push(p[0]); ys.push(p[1]); }));
  return boundsFromArrays(xs, ys);
}
function boundsFromArrays(xs, ys) {
  if (!xs.length || !ys.length) return { minX: 0, maxX: 1, minY: 0, maxY: 1 };
  return { minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys) };
}
function fitScale(b, w, h) {
  const pad = 20, dx = Math.max(b.maxX - b.minX, 1e-6), dy = Math.max(b.maxY - b.minY, 1e-6);
  return Math.min((w - pad * 2) / dx, (h - pad * 2) / dy);
}
function transformX(x, b, s, w) { return 20 + (x - b.minX) * s + (w - 40 - (b.maxX - b.minX) * s) / 2; }
function transformY(y, b, s, h) { return h - (20 + (y - b.minY) * s + (h - 40 - (b.maxY - b.minY) * s) / 2); }
function pointForNode(g, id) { const f = g.nodes.find(n => n.id === id); return f && f.pos ? f.pos : [0, 0]; }

/*
TODO: 
- kamada kawai or better arrangement of display trees, nonoverlapping edges. straighten out the random trees. for symmetric trees, display which ones are across the line of symmetry vs on
- click to see closeup of cp, or export as png
- display packing
- change the Z(t) profile time range to capture similarity better. back to the drawing board for the math

This tool definitely won't be a one and done/plug and chug, even for simple trees. you should look at the top few results which are in the ballpark, but at that point, the foldability/flap accessibility makes a much bigger difference

Why does larger tree take longer query?

fix canonicalization issues. also I think distance tends to be further for the larger N dbs bc of the way its normalized. so it's not as fair. ex: fish base has distance 1 in 3 diag but 16 in 4 diag

cannot do a combination of smooth and detailed. the point splits have to come manually

For full app: enable different constraint selection or drag around, and see tree update in real time. compute refs
text to tree, or image to tree?
*/
