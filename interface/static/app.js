import { state } from './js/state.js';
import * as Editor from './js/editor.js';
import * as TreeActions from './js/treeActions.js';
import * as Results from './js/results.js';
import * as Detail from './js/detail.js';
import * as Utils from './js/utils.js';
import { Locales } from './js/locales.js';

// ==========================================
// 1. Theme & Initialization
// ==========================================
Utils.applyTheme(Utils.readStoredThemePreference() || 'system', false);
Utils.syncDetailViewPreferences();
Editor.renderEditor();

// ==========================================
// 2. i18n Dictionary Setup
// ==========================================
let currentLang = localStorage.getItem('explori_lang') || 'en';

export function applyLanguage(lang) {
  if (!Locales[lang] || Object.keys(Locales[lang]).length === 0) {
    console.warn(`[i18n] Language '${lang}' is empty or missing. Falling back to English.`);
    lang = 'en'; 
  }
  
  currentLang = lang;
  localStorage.setItem('explori_lang', lang);
  const dict = Locales[lang];

  document.querySelectorAll('[data-i18n]').forEach(el => {
    if (dict[el.getAttribute('data-i18n')]) el.textContent = dict[el.getAttribute('data-i18n')];
  });
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    if (dict[el.getAttribute('data-i18n-title')]) el.title = dict[el.getAttribute('data-i18n-title')];
  });
  document.querySelectorAll('[data-i18n-aria]').forEach(el => {
    if (dict[el.getAttribute('data-i18n-aria')]) el.setAttribute('aria-label', dict[el.getAttribute('data-i18n-aria')]);
  });
  
  document.querySelectorAll('.lang-btn').forEach(btn => {
    btn.classList.toggle('secondary', btn.getAttribute('data-lang') !== lang);
  });

  const langSelect = document.getElementById('languageSelect');
  if (langSelect) langSelect.value = lang;
  document.documentElement.lang = lang;
}

applyLanguage(currentLang);

// ==========================================
// 3. Core Logic & Queries
// ==========================================

function selectedDbConfigs() {
  const configs = [];
  document.querySelectorAll('.db-cb:checked').forEach(cb => {
    const n = parseInt(cb.dataset.n, 10);
    const sym = cb.dataset.sym;
    configs.push({ N: n, symmetry: sym });
    
    // Edge case exception: 5 book includes 6 book
    if (n === 5 && sym === "book") {
      configs.push({ N: 6, symmetry: "book" });
    }
  });
  return configs;
}

async function runQuery() {
  const dict = Locales[currentLang] || Locales['en'];

  try {
    const tree = Editor.serializeTree();
    if (tree.edges.length < 4) {
      Utils.setStatus(dict.errorTreeTooSimple, true);
      return;
    }
    const dbConfigs = selectedDbConfigs();
    if (dbConfigs.length === 0) {
      Utils.setStatus(dict.errorNoDbConfig, true);
      return;
    }
    Utils.setStatus(dict.statusQuerying);
    state.isQueryLoading = true;
    Results.renderResults();
    const t0 = Date.now();
    
    const payload = {
      tree,
      db_configs: selectedDbConfigs(),
      n: Number(document.getElementById("resultCount")?.value || 5),
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
    
    state.isQueryLoading = false;
    state.queryResult = data;
    state.queryNodeCount = Math.max(1, tree.nodes.length || 0);
    Results.renderResults();
    
    const resultSummary = document.getElementById("resultSummary");
    if (resultSummary) resultSummary.textContent = `${data.results.length} ${dict.resultsLoaded}`;
    
    Utils.setStatus(`${dict.querySuccess1} ${payload.n} ${dict.querySuccess2} ${(totalFrontendMs/1000).toFixed(2)}s`);

  } catch (error) {
    state.isQueryLoading = false;
    Utils.setStatus(error.message, true);
    Results.renderResults();
  }
}

// ==========================================
// 4. Keyboard & Global Interactions
// ==========================================
function onKeyDown(event) {
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) return;

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

  if (state.selectedNode !== null && (event.key === "Backspace" || event.key === "Delete")) {
    event.preventDefault();
    Editor.History.saveState();
    TreeActions.deleteSelectedNode();
  }
}

function onDocumentClick(event) {
  const editorSvgEl = document.getElementById("editorSvg");
  if (!editorSvgEl) return;
  if (event.target instanceof Node && editorSvgEl.contains(event.target)) return;
  if (state.selectedNode === null) return;
  state.selectedNode = null;
  Editor.renderEditor();
}

// ==========================================
// 5. Event Binding Pipeline
// ==========================================
const bind = (id, event, handler) => {
  const el = document.getElementById(id);
  if (el) el.addEventListener(event, handler);
};

// Global Listeners
document.addEventListener("keydown", onKeyDown);
document.addEventListener("click", onDocumentClick);
window.addEventListener("resize", () => Editor.renderEditor());

// SVG Listeners
const editorSvgEl = document.getElementById("editorSvg");
if (editorSvgEl) {
  editorSvgEl.addEventListener("pointerdown", Editor.onEditorMouseDown);
  editorSvgEl.addEventListener("pointermove", Editor.onEditorMouseMove);
  editorSvgEl.addEventListener("pointerup", Editor.onEditorMouseUp);
  editorSvgEl.addEventListener("pointercancel", Editor.onEditorMouseUp);
  editorSvgEl.addEventListener("wheel", Editor.onEditorWheel, { passive: false });
}

// Modals
function setupModal(openBtnId, modalId, closeBtnId) {
  const modal = document.getElementById(modalId);
  bind(openBtnId, "click", () => modal?.classList.remove("hidden"));
  bind(closeBtnId, "click", () => modal?.classList.add("hidden"));
  modal?.addEventListener("click", (e) => { if (e.target === modal) modal.classList.add("hidden"); });
}
setupModal("donateBtn", "donateModal", "closeDonateModal");
setupModal("discordBtn", "discordModal", "closeDiscordModal");
setupModal("languageBtn", "languageModal", "closeLanguageModal");

// Detail Nav
bind("closeModal", "click", Detail.closeDetailModal);
bind("detailPrevBtn", "click", () => Detail.navigateDetail(-1));
bind("detailNextBtn", "click", () => Detail.navigateDetail(1));
const detailModal = document.getElementById("detailModal");
detailModal?.addEventListener("click", (e) => { if (e.target === detailModal) Detail.closeDetailModal(); });

// Controls
bind("runQuery", "click", runQuery);
bind("randomTreeBtn", "click", Editor.generateRandomTree);
// bind("resetTree", "click", Editor.resetTree);
bind("resetTree", "click", () => {
  Editor.History.saveState(); 
  Editor.resetTree();
});
bind("deleteNodeBtn", "click", TreeActions.deleteSelectedNode);

// Tree Upload/Download
bind("downloadTreeBtn", "click", Editor.downloadTree);
bind("uploadTreeBtn", "click", () => document.getElementById("treeFileInput")?.click());
bind("undoBtn", "click", Editor.History.undo);
bind("redoBtn", "click", Editor.History.redo);

// Detail Modal "Search for Neighbors" Listener
bind("searchNeighborsBtn", "click", () => {
  if (state.currentDetailResult && state.currentDetailResult.tree) {
    Editor.loadTreeFromResult(state.currentDetailResult.tree);
    Detail.closeDetailModal();
    window.scrollTo({ top: 0, behavior: 'smooth' }); // Smoothly scroll the user back up to the editor
  }
});

const treeFileInput = document.getElementById("treeFileInput");
if (treeFileInput) {
  treeFileInput.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (event) => Editor.loadTreeState(event.target.result);
    reader.readAsText(file);
    e.target.value = ""; 
  });
}

// Top Bar Tools
bind("themeToggleBtn", "click", () => {
  const currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
  Utils.applyTheme(currentTheme === 'dark' ? 'light' : 'dark', true);
});

document.querySelectorAll('.lang-btn').forEach(btn => {
  btn.addEventListener('click', (e) => {
    applyLanguage(e.currentTarget.getAttribute('data-lang'));
    document.getElementById('languageModal')?.classList.add('hidden');
  });
});

// UI Toggles
const resultsThumbInput = document.getElementById("resultsThumbMode");
const displayModeBtns = document.querySelectorAll(".results-head-left .thumb-mode-btn"); 
displayModeBtns.forEach(btn => {
  btn.addEventListener("click", () => {
    displayModeBtns.forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    if (resultsThumbInput) resultsThumbInput.value = btn.dataset.mode;
    if (typeof state !== 'undefined' && state.queryResult && typeof Results !== 'undefined') Results.renderResults();
  });
});
// ==========================================
// Advanced Settings Modal Wiring
// ==========================================

// 1. Let the built-in helper handle opening and closing via the X button and background click
setupModal("advancedDbBtn", "advancedDbModal", "closeAdvancedDbModal");

// 2. When "Done" is clicked in the modal, close it and sync the outside column buttons
bind("doneAdvancedDbBtn", "click", () => {
  ['diag', 'book', 'none'].forEach(sym => {
    const anyChecked = document.querySelector(`.db-cb[data-sym="${sym}"]:checked`);
    const btn = document.querySelector(`.thumb-mode-btn[data-db="${sym}"]`);
    if (btn) btn.classList.toggle('active', !!anyChecked);
  });
  document.getElementById("advancedDbModal")?.classList.add("hidden");
});

// 3. When outside column buttons are clicked, toggle the entire column of checkboxes
document.querySelectorAll('.db-toggle-group .thumb-mode-btn:not(#advancedDbBtn)').forEach(btn => {
  btn.addEventListener('click', () => {
    const sym = btn.dataset.db;
    const isActive = btn.classList.contains('active');
    
    // Prevent unchecking the very last active column button
    const activeCount = document.querySelectorAll('.db-toggle-group .thumb-mode-btn.active:not(#advancedDbBtn)').length;
    if (isActive && activeCount === 1) return; 

    btn.classList.toggle('active');
    const newState = btn.classList.contains('active');
    
    // Set all checkboxes in that column to true/false
    document.querySelectorAll(`.db-cb[data-sym="${sym}"]`).forEach(cb => {
      cb.checked = newState;
    });
  });
});