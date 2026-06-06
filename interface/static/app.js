import { state } from './state.js';
import * as Editor from './editor.js';
import * as TreeActions from './treeActions.js';
import * as Results from './results.js';
import * as Detail from './detail.js';
import * as Utils from './utils.js';

// const resultsThumbModeSelect = document.getElementById("resultsThumbMode");
// const settingsModal = document.getElementById("settingsModal");
// const settingsBtn = document.getElementById("settingsBtn");
// const closeSettingsModal = document.getElementById("closeSettingsModal");
const themeSelect = document.getElementById("themeSelect");
const languageSelect = document.getElementById("languageSelect");
const editorSvgEl = document.getElementById("editorSvg");
const deleteNodeBtn = document.getElementById("deleteNodeBtn");
const moveNodeUpBtn = document.getElementById("moveNodeUpBtn");
const moveNodeDownBtn = document.getElementById("moveNodeDownBtn");
const moveNodeLeftBtn = document.getElementById("moveNodeLeftBtn");
const moveNodeRightBtn = document.getElementById("moveNodeRightBtn");
const NODE_NUDGE_STEP = 20; //for mobile tree editing

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
// Wire up the Modals
setupModal(donateBtn, donateModal, "closeDonateModal");
setupModal(discordBtn, discordModal, "closeDiscordModal");
setupModal(languageBtn, languageModal, "closeLanguageModal");

// Wire up the 1-click Theme Toggle
if (themeToggleBtn) {
  themeToggleBtn.addEventListener("click", () => {
    // Read the current theme from the HTML tag, default to dark if not explicitly light
    const currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
    const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
    Utils.applyTheme(nextTheme, true);
  });
}

// Language selection logic (placeholder hook)
document.querySelectorAll('.lang-btn').forEach(btn => {
  btn.addEventListener('click', (e) => {
    const selectedLang = e.currentTarget.dataset.lang;
    try { localStorage.setItem('search225-language-preference', selectedLang); } catch {}
    
    // Update active button styling visually
    document.querySelectorAll('.lang-btn').forEach(b => b.classList.add('secondary'));
    e.currentTarget.classList.remove('secondary');
    
    // Close modal
    if (languageModal) languageModal.classList.add('hidden');
  });
});

// NEW SEGMENTED CONTROL LOGIC
const resultsThumbInput = document.getElementById("resultsThumbMode");
const thumbModeBtns = document.querySelectorAll(".thumb-mode-btn");

if (resultsThumbInput && thumbModeBtns.length > 0) {
  thumbModeBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      // 1. Remove active class from all buttons
      thumbModeBtns.forEach(b => b.classList.remove("active"));
      
      // 2. Indent the clicked button
      btn.classList.add("active");
      
      // 3. Sync the hidden input so the renderer knows what to draw
      resultsThumbInput.value = btn.dataset.mode;
      
      // 4. Fire the re-render if we have data
      if (state.queryResult) {
        Results.renderResults();
      }
    });
  });
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
      try { 
        data = JSON.parse(rawText); 
      } catch { 
        // Gracefully catch HTML timeout pages (e.g., Cloudflare 502/504 errors)
        const textStart = rawText.trim().toLowerCase();
        if (textStart.startsWith("<!doctype") || textStart.startsWith("<html")) {
          data = { error: "The search timed out. Try simplifying the tree or requesting fewer results." };
        } else {
          data = { error: "An unexpected server error occurred: " + rawText.slice(0, 100) }; 
        }
      }
    }
    
    if (!response.ok) throw new Error(data.error || `Query failed (Status: ${response.status})`);


    const tf = Date.now();
    const totalFrontendMs = tf - t0;
    const prof = data.profiling || {};
    const backendTotalMs = prof.backend_total_ms || 0;
    const networkMs = Math.max(0, totalFrontendMs - backendTotalMs);

    // Print detailed breakdown to the browser console
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
    if (resultSummary) resultSummary.textContent = `${data.results.length} result(s) loaded.`;
    const n = Number(document.getElementById("resultCount").value || 5);
    const isDiagOnly = !document.getElementById("diagToggle").checked;
    
    // Update UI status to show a quick summary of the bottleneck
    Utils.setStatus(`Successfully queried ${n} patterns in ${(totalFrontendMs/1000).toFixed(2)}s`);

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
