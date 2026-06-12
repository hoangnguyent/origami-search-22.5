import { state } from './state.js';
import { makeSvg, renderCpSvg, renderPackingSvg, renderFoldSvg, renderGraphSvg } from './renderers.js';
import { persistDetailView, getMatchQuality, symmetry_abbr } from './utils.js';
import { registerDetailRenderer } from './results.js';
import { Locales } from './locales.js';

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
  const lang = localStorage.getItem('explori_lang') || 'en';
  const dict = Locales[lang] || Locales['en'];

  state.currentDetailResult = result;
  state.currentDetailIndex = index;
  modalGrid.replaceChildren();
  if (!result) return;
  modalTitle.textContent = `${dict.option} ${result.rank ?? index + 1}`;
  
  const norm = (Math.sqrt(result.heat.query.reduce((sum, val) => sum + val * val, 0)));
  const quality = getMatchQuality(result.distance/norm, state.queryNodeCount);
  
  // Use the universal English key for the CSS styling
  modalMeta.dataset.quality = quality.key; 
  modalMeta.classList.add("match-quality");
  
  // Use the translated label for the display text
  modalMeta.textContent = `${dict.matchQuality}: ${quality.label} • ${dict.distance}: ${(result.distance/norm).toFixed(4)} • ${dict.tilingId}: ${result.N}${symmetry_abbr[result.symmetry]}.${result.tiling_id}`;
  
  const leftPane = buildDetailPane({
    side: "left",
    activeValue: state.detailViewModes.left,
    // Note: Reusing the dict.thumbCp and dict.thumbPacking translations here!
    options: [ { value: "cp", label: dict.thumbCp }, { value: "packing", label: dict.thumbPacking } ],
    renderActive: (svg, currentValue) => {
      if (currentValue === "packing" && result.packing) {
        renderPackingSvg(svg, result.packing, 400, 400);
      } else {
        renderCpSvg(svg, result.cp, 400, 400);
      }
    },
  });

  const rightPane = buildDetailPane({
    side: "right",
    activeValue: state.detailViewModes.right,
    options: [ { value: "tree", label: dict.thumbTree }, { value: "fold", label: dict.thumbFold } ],
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
