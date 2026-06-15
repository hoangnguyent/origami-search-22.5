import { state } from './js/state.js';
import * as Editor from './js/editor.js';
import * as TreeActions from './js/treeActions.js';
import * as Results from './js/results.js';
import * as Detail from './js/detail.js';
import * as Utils from './js/utils.js';
import { Locales } from './js/locales.js';

const themeSelect = document.getElementById("themeSelect");
const languageSelect = document.getElementById("languageSelect");
const editorSvgEl = document.getElementById("editorSvg");
const deleteNodeBtn = document.getElementById("deleteNodeBtn");
const moveNodeUpBtn = document.getElementById("moveNodeUpBtn");
const moveNodeDownBtn = document.getElementById("moveNodeDownBtn");
const moveNodeLeftBtn = document.getElementById("moveNodeLeftBtn");
const moveNodeRightBtn = document.getElementById("moveNodeRightBtn");
const NODE_NUDGE_STEP = 20;

const themeToggleBtn = document.getElementById("themeToggleBtn");
const donateBtn = document.getElementById("donateBtn");
const discordBtn = document.getElementById("discordBtn");
const languageBtn = document.getElementById("languageBtn");

const donateModal = document.getElementById("donateModal");
const discordModal = document.getElementById("discordModal");
const languageModal = document.getElementById("languageModal");

function setupModal(openBtn, modalEl, closeBtnId) {
  if (!openBtn || !modalEl) return;
  const closeBtn = document.getElementById(closeBtnId);
  
  openBtn.addEventListener("click", () => modalEl.classList.remove("hidden"));
  if (closeBtn) closeBtn.addEventListener("click", () => modalEl.classList.add("hidden"));
  modalEl.addEventListener("click", (e) => {
    if (e.target === modalEl) modalEl.classList.add("hidden");
  });
}
setupModal(donateBtn, donateModal, "closeDonateModal");
setupModal(discordBtn, discordModal, "closeDiscordModal");
setupModal(languageBtn, languageModal, "closeLanguageModal");

if (themeToggleBtn) {
  themeToggleBtn.addEventListener("click", () => {
    const currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
    const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
    Utils.applyTheme(nextTheme, true);
  });
}

// ==========================================
// i18n Dictionary Setup
// ==========================================
let currentLang = localStorage.getItem('explori_lang') || 'en';

const langButtons = document.querySelectorAll('.lang-btn');
if (langButtons.length > 0) {
  langButtons.forEach(btn => {
    btn.addEventListener('click', (e) => {
      const selectedLang = e.currentTarget.getAttribute('data-lang');
      applyLanguage(selectedLang);
      document.getElementById('languageModal').classList.add('hidden');
    });
  });
}
export function applyLanguage(lang) {
  console.log("changing language")
  // Check if the language exists AND actually has translations inside it
  if (!Locales[lang] || Object.keys(Locales[lang]).length === 0) {
    console.warn(`[i18n] Language '${lang}' is empty or missing. Falling back to English.`);
    lang = 'en'; 
  }
  
  currentLang = lang;
  localStorage.setItem('explori_lang', lang);

  const dict = Locales[lang];

  // 1. Update standard text content
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (dict[key]) el.textContent = dict[key];
  });

  // 2. Update tooltip titles
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    const key = el.getAttribute('data-i18n-title');
    if (dict[key]) el.title = dict[key];
  });

  // 3. Update screen reader aria-labels
  document.querySelectorAll('[data-i18n-aria]').forEach(el => {
    const key = el.getAttribute('data-i18n-aria');
    if (dict[key]) el.setAttribute('aria-label', dict[key]);
  });
  
  // 4. NEW: Visually update the buttons in the Language Modal
  document.querySelectorAll('.lang-btn').forEach(btn => {
    if (btn.getAttribute('data-lang') === lang) {
      btn.classList.remove('secondary'); // Highlight the active language
    } else {
      btn.classList.add('secondary');    // Dim the inactive languages
    }
  });

  // 5. NEW: Sync the dropdown in the Settings Modal
  const langSelect = document.getElementById('languageSelect');
  if (langSelect) {
    langSelect.value = lang;
  }
  
  // Update HTML lang attribute
  document.documentElement.lang = lang;
}
applyLanguage(currentLang);
// ==========================================

const resultsThumbInput = document.getElementById("resultsThumbMode");
const displayModeBtns = document.querySelectorAll(".results-head-left .thumb-mode-btn"); 
if (resultsThumbInput && displayModeBtns.length > 0) {
  displayModeBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      displayModeBtns.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      resultsThumbInput.value = btn.dataset.mode;
      
      if (typeof state !== 'undefined' && state.queryResult && typeof Results !== 'undefined') {
        Results.renderResults();
      }
    });
  });
}

const dbBtns = document.querySelectorAll('.db-toggle-group .thumb-mode-btn'); 
if (dbBtns.length > 0) {
  dbBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const isActive = btn.classList.contains('active');
      const activeCount = document.querySelectorAll('.db-toggle-group .thumb-mode-btn.active').length;
      
      if (isActive && activeCount === 1) return; 
      btn.classList.toggle('active');
    });
  });
}

function selectedDbConfigs() {
  const configs = [];
  const isDiag = document.getElementById("dbDiagBtn")?.classList.contains("active");
  const isBook = document.getElementById("dbBookBtn")?.classList.contains("active");
  const isAsym = document.getElementById("dbAsymBtn")?.classList.contains("active");

  if (isDiag) configs.push({N:2, symmetry: "diag"}, { N: 3, symmetry: "diag" }, { N: 4, symmetry: "diag" }, { N: 5, symmetry: "diag" });
  if (isBook) configs.push({N:2, symmetry: "book"}, { N: 3, symmetry: "book" }, { N: 4, symmetry: "book" }, { N: 5, symmetry: "book" }, { N: 6, symmetry: "book" });
  if (isAsym) configs.push({N:2, symmetry: "none"}, { N: 3, symmetry: "none" }, { N: 4, symmetry: "none" });

  return configs;
}

async function runQuery() {
  // Grab the current dictionary for dynamic JS strings
  const dict = Locales[currentLang] || Locales['en'];

  try {
    const tree = Editor.serializeTree();
    if (tree.edges.length < 4) {
      Utils.setStatus(dict.errorTreeTooSimple, true);
      return;
    }

    Utils.setStatus(dict.statusQuerying);
    state.isQueryLoading = true;
    Results.renderResults();
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
      try { 
        data = JSON.parse(rawText); 
      } catch { 
        const textStart = rawText.trim().toLowerCase();
        if (textStart.startsWith("<!doctype") || textStart.startsWith("<html")) {
          data = { error: dict.errorTimeout };
        } else {
          data = { error: dict.errorServer + rawText.slice(0, 100) }; 
        }
      }
    }
    
    if (!response.ok) throw new Error(data.error || `${dict.errorQueryFailed} ${response.status})`);

    const tf = Date.now();
    const totalFrontendMs = tf - t0;
    const prof = data.profiling || {};
    const backendTotalMs = prof.backend_total_ms || 0;
    const networkMs = Math.max(0, totalFrontendMs - backendTotalMs);

    console.log("📊 --- Query Profiling Breakdown ---");
    console.log(`Total Roundtrip Time : ${totalFrontendMs}ms`);
    console.log(` ├── Network / Browser : ${networkMs.toFixed(1)}ms`);
    console.log(` └── Backend Server    : ${backendTotalMs.toFixed(1)}ms`);
    console.log(`      ├── Setup        : ${prof.backend_setup_ms?.toFixed(1)}ms`);
    console.log(`      ├── DB Query     : ${prof.db_query_ms?.toFixed(1)}ms`);
    console.log(`      └── Post-Process : ${prof.post_processing_ms?.toFixed(1)}ms`);
    console.log("----------------------------------");
    
    state.isQueryLoading = false;
    state.queryResult = data;
    state.queryNodeCount = Math.max(1, tree.nodes.length || 0);
    Results.renderResults();
    const resultSummary = document.getElementById("resultSummary");
    if (resultSummary) resultSummary.textContent = `${data.results.length} ${dict.resultsLoaded}`;
    const n = Number(document.getElementById("resultCount").value || 5);
    
    Utils.setStatus(`${dict.querySuccess1} ${n} ${dict.querySuccess2} ${(totalFrontendMs/1000).toFixed(2)}s`);

  } catch (error) {
    state.isQueryLoading = false;
    Utils.setStatus(error.message, true);
    Results.renderResults();
  }
}

function onKeyDown(event) {
  if (event.target.tagName === 'INPUT' || event.target.tagName === 'TEXTAREA' || event.target.tagName === 'SELECT') return;

  const detailModalEl = document.getElementById("detailModal");
  if (event.key === "Escape") {
    if (detailModalEl && !detailModalEl.classList.contains("hidden")) {
      Detail.closeDetailModal();
      return;
    }
    state.selectedNode = null;
    state.draggingNode = null;
    Editor.renderEditor();
    return;
  }

  if (state.selectedNode !== null) {
    if (event.key === "Backspace" || event.key === "Delete") {
      event.preventDefault();
      TreeActions.deleteSelectedNode();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      TreeActions.moveSelectedNode(0, -NODE_NUDGE_STEP);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      TreeActions.moveSelectedNode(0, NODE_NUDGE_STEP);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      TreeActions.moveSelectedNode(-NODE_NUDGE_STEP, 0);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      TreeActions.moveSelectedNode(NODE_NUDGE_STEP, 0);
    }
  }
}

function onDocumentClick(event) {
  if (!editorSvgEl) return;
  if (event.target instanceof Node && editorSvgEl.contains(event.target)) return;
  
  const controlsEl = document.getElementById("mobileEditorControls");
  if (controlsEl && event.target instanceof Node && controlsEl.contains(event.target)) return;

  if (state.selectedNode === null) return;
  state.selectedNode = null;
  Editor.renderEditor();
}

document.getElementById("runQuery").addEventListener("click", runQuery);
document.getElementById("resetTree").addEventListener("click", Editor.resetTree);
const closeModalBtn = document.getElementById("closeModal"); if (closeModalBtn) closeModalBtn.addEventListener("click", Detail.closeDetailModal);
const prevBtn = document.getElementById("detailPrevBtn"); if (prevBtn) prevBtn.addEventListener("click", () => Detail.navigateDetail(-1));
const nextBtn = document.getElementById("detailNextBtn"); if (nextBtn) nextBtn.addEventListener("click", () => Detail.navigateDetail(1));
const detailModalEl = document.getElementById("detailModal"); if (detailModalEl) detailModalEl.addEventListener("click", (e) => { if (e.target === detailModalEl) Detail.closeDetailModal(); });

document.addEventListener("keydown", onKeyDown);
document.addEventListener("click", onDocumentClick);
if (editorSvgEl) {
  editorSvgEl.addEventListener("pointerdown", Editor.onEditorMouseDown);
  editorSvgEl.addEventListener("pointermove", Editor.onEditorMouseMove);
  editorSvgEl.addEventListener("pointerup", Editor.onEditorMouseUp);
  editorSvgEl.addEventListener("pointercancel", Editor.onEditorMouseUp);
  editorSvgEl.addEventListener("wheel", Editor.onEditorWheel, { passive: false });
}
document.getElementById("randomTreeBtn").addEventListener("click", Editor.generateRandomTree);
if (deleteNodeBtn) deleteNodeBtn.addEventListener("click", TreeActions.deleteSelectedNode);
if (moveNodeUpBtn) moveNodeUpBtn.addEventListener("click", () => TreeActions.moveSelectedNode(0, -NODE_NUDGE_STEP));
if (moveNodeDownBtn) moveNodeDownBtn.addEventListener("click", () => TreeActions.moveSelectedNode(0, NODE_NUDGE_STEP));
if (moveNodeLeftBtn) moveNodeLeftBtn.addEventListener("click", () => TreeActions.moveSelectedNode(-NODE_NUDGE_STEP, 0));
if (moveNodeRightBtn) moveNodeRightBtn.addEventListener("click", () => TreeActions.moveSelectedNode(NODE_NUDGE_STEP, 0));
window.addEventListener("resize", () => Editor.renderEditor());

Utils.applyTheme(Utils.readStoredThemePreference() || 'system', false);
Utils.syncDetailViewPreferences();
Editor.renderEditor();