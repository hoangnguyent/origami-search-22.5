import { state } from './state.js';
import * as Editor from './editor.js';
import * as TreeActions from './treeActions.js';
import * as Results from './results.js';
import * as Detail from './detail.js';
import * as Utils from './utils.js';

const resultsThumbModeSelect = document.getElementById("resultsThumbMode");
const settingsModal = document.getElementById("settingsModal");
const settingsBtn = document.getElementById("settingsBtn");
const closeSettingsModal = document.getElementById("closeSettingsModal");
const themeSelect = document.getElementById("themeSelect");
const languageSelect = document.getElementById("languageSelect");
const editorSvgEl = document.getElementById("editorSvg");
const deleteNodeBtn = document.getElementById("deleteNodeBtn");
const moveNodeUpBtn = document.getElementById("moveNodeUpBtn");
const moveNodeDownBtn = document.getElementById("moveNodeDownBtn");
const moveNodeLeftBtn = document.getElementById("moveNodeLeftBtn");
const moveNodeRightBtn = document.getElementById("moveNodeRightBtn");
const NODE_NUDGE_STEP = 12;

function selectedDbConfigs() {
  const isDiagOnly = !document.getElementById("diagToggle").checked;
  if (isDiagOnly) {
    return [
      { N: 3, symmetry: "diag" },
      { N: 4, symmetry: "diag" },
      { N: 5, symmetry: "diag" }
    ];
  } else {
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
    const tree = Editor.serializeTree();
    if (tree.edges.length < 4) {
      Utils.setStatus("Tree is too simple. Add at least 4 edges before running a query.", true);
      return;
    }

    Utils.setStatus("Querying backend...");
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
      try { data = JSON.parse(rawText); } catch { data = { error: rawText.slice(0, 200) }; }
    }
    if (!response.ok) throw new Error(data.error || "Query failed");
    state.isQueryLoading = false;
    state.queryResult = data;
    state.queryNodeCount = Math.max(1, tree.nodes.length || 0);
    const tf = Date.now();
    Results.renderResults();
    const resultSummary = document.getElementById("resultSummary");
    if (resultSummary) resultSummary.textContent = `${data.results.length} result(s) loaded.`;
    const n = Number(document.getElementById("resultCount").value || 5);
    const isDiagOnly = !document.getElementById("diagToggle").checked;
    Utils.setStatus(`Successfully queried ${n} crease patterns. Database size: ${isDiagOnly? "1,235,954": "1,803,458"}. Query time: ${((tf-t0)/1000).toFixed(2)}s`);

  } catch (error) {
    state.isQueryLoading = false;
    Utils.setStatus(error.message, true);
    Results.renderResults();
  }
}

function onKeyDown(event) {
  // Prevent catching arrow keys if typing in an input
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

  // Hook up physical arrow keys to move the node
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
  // Ignore clicks inside the canvas
  if (event.target instanceof Node && editorSvgEl.contains(event.target)) return;
  
  // Ignore clicks inside the on-screen arrow controls so it doesn't deselect
  const controlsEl = document.getElementById("mobileEditorControls");
  if (controlsEl && event.target instanceof Node && controlsEl.contains(event.target)) return;

  if (state.selectedNode === null) return;
  state.selectedNode = null;
  Editor.renderEditor();
}

// Wire DOM events
document.getElementById("runQuery").addEventListener("click", runQuery);
document.getElementById("resetTree").addEventListener("click", Editor.resetTree);
const closeModalBtn = document.getElementById("closeModal"); if (closeModalBtn) closeModalBtn.addEventListener("click", Detail.closeDetailModal);
const prevBtn = document.getElementById("detailPrevBtn"); if (prevBtn) prevBtn.addEventListener("click", () => Detail.navigateDetail(-1));
const nextBtn = document.getElementById("detailNextBtn"); if (nextBtn) nextBtn.addEventListener("click", () => Detail.navigateDetail(1));
const detailModalEl = document.getElementById("detailModal"); if (detailModalEl) detailModalEl.addEventListener("click", (e) => { if (e.target === detailModalEl) Detail.closeDetailModal(); });
if (settingsBtn) settingsBtn.addEventListener("click", () => settingsModal && settingsModal.classList.remove("hidden"));
if (closeSettingsModal) closeSettingsModal.addEventListener("click", () => settingsModal && settingsModal.classList.add("hidden"));
if (settingsModal) settingsModal.addEventListener("click", (e) => { if (e.target === settingsModal) settingsModal.classList.add("hidden"); });
if (themeSelect) themeSelect.addEventListener("change", () => Utils.applyTheme(themeSelect.value, true));
if (languageSelect) languageSelect.addEventListener("change", () => { try { localStorage.setItem('search225-language-preference', languageSelect.value); } catch {} });
if (resultsThumbModeSelect) { resultsThumbModeSelect.addEventListener("change", () => { if (state.queryResult) Results.renderResults(); }); }
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
document.getElementById("downloadCpBtn").addEventListener("click", Detail.exportCurrentCp);
window.addEventListener("resize", () => Editor.renderEditor());

// Initialize theme, preferences and initial render
Utils.applyTheme(Utils.readStoredThemePreference() || 'system', false);
Utils.syncDetailViewPreferences();
Editor.renderEditor();
