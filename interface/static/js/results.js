import { state } from './state.js';
import { makeSvg, renderCpSvg, renderPackingSvg, renderFoldSvg, renderGraphSvg } from './renderers.js';
import { getMatchQuality, symmetry_abbr } from './utils.js';
import { Locales } from './locales.js';

const resultsGrid = document.getElementById("resultsGrid");
const resultSummary = document.getElementById("resultSummary");
const resultsThumbModeSelect = document.getElementById("resultsThumbMode");
const loadingGifUrl = new URL("./../assets/robot_loading.svg", import.meta.url).href;

function renderLoadingResults() {
  const lang = localStorage.getItem('explori_lang') || 'en';
  const dict = Locales[lang] || Locales['en'];

  resultsGrid.replaceChildren();

  const loadingCard = document.createElement("div");
  loadingCard.className = "results-loading";
  loadingCard.innerHTML = `
    <img src="${loadingGifUrl}" alt="" aria-hidden="true" />
    <p>${dict.fetchingResults}</p>
  `;

  resultsGrid.appendChild(loadingCard);
  if (resultSummary) resultSummary.textContent = dict.fetchingResults;
}

export function renderResults() {
  if (state.isQueryLoading) {
    renderLoadingResults();
    return;
  }

  resultsGrid.replaceChildren();
  if (!state.queryResult) return;
  const thumbMode = resultsThumbModeSelect?.value || "cp";

  state.queryResult.results.forEach((result, index) => {
    const card = document.createElement("article");
    card.className = "result-card";
    card.addEventListener("click", () => renderDetail(result, index));

    const thumb = document.createElement("div");
    thumb.className = "thumb";
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
    const norm = (Math.sqrt(result.heat.query.reduce((sum, val) => sum + val * val, 0)));
    const quality = getMatchQuality(result.distance/norm, state.queryNodeCount);
    
    const lang = localStorage.getItem('explori_lang') || 'en';
    const dict = Locales[lang] || Locales['en'];

    // Apply the class and the universal English key to the entire meta container
    meta.className = "result-meta match-quality";
    meta.dataset.quality = quality.key; 

    // Use the translated label for the display text
    meta.innerHTML = `
      <div><strong>${dict.option} ${result.rank ?? index + 1}</strong></div>
      <div>${dict.matchQuality}: ${quality.label}</div>
      <div>${dict.id}: ${result.N}${symmetry_abbr[result.symmetry]}.${result.tiling_id}</div>
    `;

    card.appendChild(thumb);
    card.appendChild(meta);
    resultsGrid.appendChild(card);
  });
  
  const lang = localStorage.getItem('explori_lang') || 'en';
  const dict = Locales[lang] || Locales['en'];
  resultSummary.textContent = `${state.queryResult.results.length} ${dict.resultsLoaded}`;
}

// Placeholder; will be imported dynamically by app.js to avoid circular dependency
export let renderDetail = () => {};
export function registerDetailRenderer(fn) { renderDetail = fn; }
