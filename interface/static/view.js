import * as Utils from './js/utils.js';

// --- Global UI & Modal Wiring (Matches app.js) ---
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
    // Re-draw the canvas so the border color flips if necessary
    if (window.currentCpData) {
        drawCP(document.getElementById("cpCanvas"), window.currentCpData);
    }
  });
}

// --- View Logic ---
async function initView() {
    // Initialize Theme
    Utils.applyTheme(Utils.readStoredThemePreference() || 'system', false);

    const urlParams = new URLSearchParams(window.location.search);
    const fullId = urlParams.get('id') || '';
    const titleEl = document.getElementById("viewTitle");
    const canvas = document.getElementById("cpCanvas");
    const container = canvas.parentElement;

    // Reset title styling and text
    titleEl.style.color = ''; 
    titleEl.textContent = "Loading Pattern...";

    // Hide canvas and show loading spinner
    canvas.style.display = 'none';
    const loadingImg = document.createElement('img');
    loadingImg.id = 'loadingSpinner';
    loadingImg.src = '/assets/loading.svg';
    loadingImg.style.display = 'block';
    loadingImg.style.margin = '3rem auto';
    loadingImg.style.width = '400px';
    container.appendChild(loadingImg);

    // Helper to gracefully handle and style errors
    const showError = (msg) => {
        titleEl.textContent = msg;
        titleEl.style.color = '#ff5555'; // Danger color
        const spinner = document.getElementById('loadingSpinner');
        if (spinner) spinner.remove();
    };

    // 1. Strict Parsing: Checks for [1 digit N][n, b, or d][1+ digit ID]
    const match = fullId.match(/^(\d)([nbd])(\d+)$/i);
    if (!match) {
        showError("Error: Invalid Pattern ID provided.");
        return;
    }

    const N = parseInt(match[1], 10);
    const symChar = match[2].toLowerCase();
    const tilingId = match[3];

    let sym = 'none';
    if (symChar === 'd') sym = 'diag';
    else if (symChar === 'b') sym = 'book';

    try {
        const response = await fetch(`/api/fetch_tiling?id=${tilingId}&N=${N}&sym=${sym}`);
        if (!response.ok) throw new Error("Pattern not found or server error.");
        
        const data = await response.json();
        const result = data.results && data.results[0];
        
        if (!result || !result.cp) throw new Error("Pattern data is corrupted.");

        // Format a nice title
        const symTitle = sym.charAt(0).toUpperCase() + sym.slice(1);
        titleEl.textContent = `Crease Pattern ${fullId}`;
        
        // Remove loading spinner and reveal canvas
        const spinner = document.getElementById('loadingSpinner');
        if (spinner) spinner.remove();
        canvas.style.display = 'block';

        // Save globally so theme toggle can re-render it
        window.currentCpData = result.cp;
        drawCP(canvas, result.cp);

    } catch (err) {
        showError(`Error: ${err.message}`);
    }
}

function drawCP(canvas, cp) {
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    
    ctx.clearRect(0, 0, w, h);

    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';

    // Matches your app.js / python CP color standards
    const colors = {
        m: "#ff6b6b",
        rm: "#ff6b6b",
        v: "#4dabf7",
        rv: "#4dabf7",
        h: "#9aa8bf",
        b: isDark ? "#f0f3f7" : "#111111" // Adapts bounding box to light/dark mode
    };

    const segments = cp.segments || [];

    // Sort segments so thick borders draw first (underneath), then hinges, then mountains/valleys on top
    const sortOrder = { 'b': 0, 'h': 1, 'v': 2, 'rv': 2, 'm': 3, 'rm': 3 };
    segments.sort((a, b) => sortOrder[a.type || 'h'] - sortOrder[b.type || 'h']);

    segments.forEach(seg => {
        const type = (seg.type || 'h').toLowerCase();
        const color = colors[type] || colors.h;
        
        // Dynamic Line Weights (Border is thickest)
        const lw = type === 'b' ? 5 : (['m', 'v', 'rm', 'rv'].includes(type) ? 3 : 1.5);

        ctx.beginPath();
        // Origami math coords (0,0) are bottom-left. Canvas is top-left.
        // We invert the Y coordinate mathematically via (1 - y)
        ctx.moveTo(seg.x1 * w, (1 - seg.y1) * h); 
        ctx.lineTo(seg.x2 * w, (1 - seg.y2) * h);
        
        ctx.strokeStyle = color;
        ctx.lineWidth = lw;
        ctx.lineCap = "round";
        ctx.stroke();
    });
}

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


// Start sequence
initView();