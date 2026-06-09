import { state } from './state.js';
import { makeSvg, renderCpSvg, renderPackingSvg, renderFoldSvg, renderGraphSvg } from './renderers.js';
import { persistDetailView, getMatchQuality, symmetry_abbr } from './utils.js';
import { registerDetailRenderer } from './results.js';

const detailModal = document.getElementById("detailModal");
const modalGrid = document.getElementById("modalGrid");
const modalTitle = document.getElementById("modalTitle");
const modalMeta = document.getElementById("modalMeta");
const detailPrevBtn = document.getElementById("detailPrevBtn");
const detailNextBtn = document.getElementById("detailNextBtn");

export function closeDetailModal() {
  detailModal.classList.add("hidden");
  detailModal.setAttribute("aria-hidden", "true");
  state.currentDetailResult = null;
  state.currentDetailIndex = null;
}

export function updateDetailView(side, value) {
  if (state.detailViewModes[side] === value) return;
  state.detailViewModes[side] = value;
  persistDetailView(side, value);
  if (state.currentDetailResult && state.currentDetailIndex !== null) {
    renderDetail(state.currentDetailResult, state.currentDetailIndex);
  }
}

export function navigateDetail(step) {
  if (!state.queryResult?.results?.length || state.currentDetailIndex === null) return;
  const nextIndex = state.currentDetailIndex + step;
  if (nextIndex < 0 || nextIndex >= state.queryResult.results.length) return;
  renderDetail(state.queryResult.results[nextIndex], nextIndex);
}

export function updateDetailNavButtons() {
  if (!detailPrevBtn || !detailNextBtn) return;
  const total = state.queryResult?.results?.length || 0;
  detailPrevBtn.disabled = state.currentDetailIndex === null || state.currentDetailIndex <= 0;
  detailNextBtn.disabled = state.currentDetailIndex === null || state.currentDetailIndex >= total - 1;
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
    input.addEventListener("change", () => { if (input.checked) updateDetailView(side, option.value); });
    const span = document.createElement("span");
    span.textContent = option.label;
    label.appendChild(input);
    label.appendChild(span);
    toggleGroup.appendChild(label);
  });

  const body = document.createElement("div");
  body.className = "detail-panel-body";
  
  // UPDATE: Change viewBox to a square 400x400
  const svg = makeSvg("svg", { viewBox: "0 0 400 400", class: "detail-svg" });
  renderActive(svg, activeValue);
  body.appendChild(svg);

  panel.appendChild(body);
  panel.appendChild(toggleGroup);
  return panel;
}

export function renderDetail(result, index) {
  state.currentDetailResult = result;
  state.currentDetailIndex = index;
  modalGrid.replaceChildren();
  if (!result) return;
  modalTitle.textContent = `Option ${result.rank ?? index + 1}`;
  
  // how close was the query to the origin? lower magnitude means on average closer L2 distance even if match isn't as good
  const norm = (Math.sqrt(result.heat.query.reduce((sum, val) => sum + val * val, 0)));
  const quality = getMatchQuality(result.distance/norm, state.queryNodeCount);
  modalMeta.dataset.quality = quality;
  modalMeta.classList.add("match-quality");
  modalMeta.textContent = `Match quality: ${quality} • Distance: ${(result.distance/norm).toFixed(4)} • Tiling ID: ${result.N}${symmetry_abbr[result.symmetry]}.${result.tiling_id}`;
  
  const leftPane = buildDetailPane({
    side: "left",
    activeValue: state.detailViewModes.left,
    options: [ { value: "cp", label: "Crease pattern" }, { value: "packing", label: "Packing" } ],
    renderActive: (svg, currentValue) => {
      if (currentValue === "packing" && result.packing) {
        // UPDATE: Pass 400, 400
        renderPackingSvg(svg, result.packing, 400, 400);
      } else {
        // UPDATE: Pass 400, 400
        renderCpSvg(svg, result.cp, 400, 400);
      }
    },
  });

  const rightPane = buildDetailPane({
    side: "right",
    activeValue: state.detailViewModes.right,
    options: [ { value: "tree", label: "Tree" }, { value: "fold", label: "Folded form" } ],
    renderActive: (svg, currentValue) => {
      if (currentValue === "fold" && result.fold) {
        // Pass 400, 400 so it matches the viewBox square
        renderFoldSvg(svg, result.fold, 400, 400); 
      } else {
        renderGraphSvg(svg, result.tree, { nodeFill: "#8cffc1", width: 400, height: 400 });
      }
    },
  });

  modalGrid.appendChild(leftPane);
  modalGrid.appendChild(rightPane);
  updateDetailNavButtons();
  detailModal.classList.remove("hidden");
  detailModal.setAttribute("aria-hidden", "false");
}

// Register ourselves with results so clicking thumbnails can open details
registerDetailRenderer(renderDetail);

// Download handler wired by app.js, but expose an export for convenience
export function exportCurrentCp() {
  if (!state.currentDetailResult) return;
  const cp = state.currentDetailResult.cp;
  const vertices = [];
  const vMap = new Map();
  const edges_vertices = [];
  const edges_assignment = [];
  function getVertexId(x, y) {
    const key = x.toFixed(6) + "," + y.toFixed(6);
    if (vMap.has(key)) return vMap.get(key);
    const id = vertices.length;
    vertices.push([x, y]);
    vMap.set(key, id);
    return id;
  }
  function getFoldType(rawType) {
    if (!rawType) return "F";
    const t = String(rawType).trim().toLowerCase();
    if (t === "b") return "B";
    if (t === "rm" || t === "m" || t === "hm") return "M";
    if (t === "rv" || t === "av" || t === "v" || t === "hv") return "V";
    if (t === "h" || t === "aux" || t === "ax") return "F";
    if (t.includes("m")) return "M";
    if (t.includes("v")) return "V";
    return "F";
  }
  cp.segments.forEach(seg => {
    const u = getVertexId(seg.x1, seg.y1);
    const v = getVertexId(seg.x2, seg.y2);
    edges_vertices.push([u, v]);
    edges_assignment.push(getFoldType(seg.type));
  });
  const foldData = { file_spec: 1.1, file_creator: "SEARCH 22.5", vertices_coords: vertices, edges_vertices: edges_vertices, edges_assignment: edges_assignment };
  const blob = new Blob([JSON.stringify(foldData, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const rank = state.currentDetailResult.rank || 1;
  const N = state.currentDetailResult.N || "N";
  const sym = state.currentDetailResult.symmetry || "sym";
  const tilingId = state.currentDetailResult.tiling_id || "unknown";
  a.href = url;
  a.download = `${N}${sym=="diag"?"d":sym=="book"?"b":"n"}-${tilingId}.fold`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
