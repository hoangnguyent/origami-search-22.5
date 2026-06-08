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
  const quality = getMatchQuality(result.distance, state.queryNodeCount);
  modalMeta.dataset.quality = quality;
  modalMeta.classList.add("match-quality");
  modalMeta.textContent = `Match quality: ${quality} • Normalized distance: ${(result.distance*Math.exp(state.queryNodeCount)/1000).toFixed(4)} • Tiling ID: ${result.N}${symmetry_abbr[result.symmetry]}.${result.tiling_id}`;
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
  const viewLink = document.getElementById("viewPatternLink");
  if (viewLink) {
    const N = result.N || "4";
    const sym = result.symmetry || "none";
    const symChar = sym === "diag" ? "d" : sym === "book" ? "b" : "n";
    const tilingId = result.tiling_id || "0";
    viewLink.href = `/view?id=${N}${symChar}${tilingId}`;
  }
  detailModal.classList.remove("hidden");
  detailModal.setAttribute("aria-hidden", "false");
}

registerDetailRenderer(renderDetail);
