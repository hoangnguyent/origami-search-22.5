import * as Utils from './js/utils.js';
import { makeSvg, renderCpSvg, renderPackingSvg, renderFoldSvg, renderGraphSvg, renderHeatSvg } from './js/renderers.js';

// --- Global UI & Modal Wiring ---
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
    
    // NOTE: We do not need to redraw the SVGs here. 
    // renderers.js assigns CSS classes (e.g., .cp-m, .cp-v), so styles.css handles the light/dark flip natively!
  });
}

// --- Coordinate Math ---
function getCPCoords(event, svgEl) {
    const rect = svgEl.getBoundingClientRect();
    // Normalize click to 0.0 -> 1.0 based on CSS rendered size
    const normX = (event.clientX - rect.left) / rect.width;
    const normY = (event.clientY - rect.top) / rect.height;
    
    // Origami unit square is (0,0) at bottom-left. Screen is top-left.
    return {
        x: normX,
        y: 1.0 - normY
    };
}

// --- Dynamic SVG Rendering ---
function populatePanel(containerId, renderFn, data, options = {}) {
    if (!data) return null;
    
    // Find the panel directly by its ID
    const container = document.getElementById(containerId);
    if (!container) return null;
    
    // Clear placeholders or previously drawn SVGs
    container.innerHTML = ''; 
    
    let viewBox = options.viewBox || "0 0 1000 1000";
    if (renderFn === renderHeatSvg) viewBox = "0 0 420 240";

    const svg = makeSvg("svg", { 
        viewBox: viewBox, 
        style: "width: 100%; height: 100%; display: block; overflow: visible;" 
    });
    
    // Route to the correct renderer signature
    if (renderFn === renderHeatSvg) {
        renderFn(svg, data);
    } else if (renderFn === renderGraphSvg) {
        renderFn(svg, data, { nodeFill: options.nodeFill || "#8cffc1", width: options.w || 1000, height: options.h || 1000 });
    } else {
        renderFn(svg, data, options.w || 1000, options.h || 1000);
    }
    
    container.appendChild(svg);
    return svg;
}

function drawAllPanels(result) {
    // Tier 1 & 2: Interactive SVG Panels
    const cpSvg = populatePanel("target-cp", renderCpSvg, result.cp, {w: 1000, h: 1000});
    setupInteractiveSvg(cpSvg, "CP");
    
    const packingSvg = populatePanel("target-packing", renderPackingSvg, result.packing, {w: 1000, h: 1000});
    setupInteractiveSvg(packingSvg, "Packing");
    
    const treeSvg = populatePanel("target-tree", renderGraphSvg, result.tree, {w: 1000, h: 1000, nodeFill: "#8cffc1"});
    setupInteractiveSvg(treeSvg, "Tree");
    
    // Tier 3: Static Information SVGs
    populatePanel("target-topology", renderGraphSvg, result.topology, {w: 1000, h: 1000, nodeFill: "#a7c7ff"});
    populatePanel("target-tiling", renderGraphSvg, result.solved_tiling, {w: 1000, h: 1000, nodeFill: "#8cffc1"});
    populatePanel("target-fold", renderFoldSvg, result.fold, {w: 1000, h: 1000});
    
    if (result.heat) {
        // Assuming you add an id="target-heat" div somewhere in the future
        populatePanel("target-heat", renderHeatSvg, result.heat);
    }
}

function setupInteractiveSvg(svg, name) {
    if (!svg) return;
    const coordDisplay = document.getElementById("clickCoordDisplay");
    
    svg.style.cursor = "crosshair";
    
    svg.addEventListener("mousemove", (e) => {
        const pt = getCPCoords(e, svg);
        if (coordDisplay) coordDisplay.textContent = `(x: ${pt.x.toFixed(4)}, y: ${pt.y.toFixed(4)})`;
    });
    
    svg.addEventListener("mouseleave", () => {
        if (coordDisplay) coordDisplay.textContent = `(x: -, y: -)`;
    });

    svg.addEventListener("click", (e) => {
        const pt = getCPCoords(e, svg);
        console.log(`[${name}] Clicked at exact coords:`, pt);
        // TODO: Add interactive overlay/highlight logic here
    });
}

// --- View Logic ---
async function initView() {
    Utils.applyTheme(Utils.readStoredThemePreference() || 'system', false);

    const urlParams = new URLSearchParams(window.location.search);
    const fullId = urlParams.get('id') || '';
    const titleEl = document.getElementById("viewTitle");
    const mainEl = document.querySelector("main"); // Target the main body area directly

    // Reset title styling and text
    if (titleEl) {
        titleEl.style.color = ''; 
        titleEl.textContent = "Loading Pattern...";
    }

    // Hide all layout panels AND export buttons completely so the page is empty
    const layoutContainers = document.querySelectorAll('.dash-row-2col, .dash-row-3col, .panel, button[id*="download"], button[id*="export"], .export-btn');
    layoutContainers.forEach(c => c.style.display = 'none');

    const loadingImg = document.createElement('img');
    loadingImg.id = 'loadingSpinner';
    loadingImg.src = '/assets/loading.svg';
    loadingImg.style.display = 'block';
    loadingImg.style.margin = '15vh auto'; // Center heavily in the empty main space
    loadingImg.style.width = '400px';
    
    // Inject directly into the main element
    if (mainEl) mainEl.appendChild(loadingImg);

    const showError = (msg) => {
        if (titleEl) {
            titleEl.textContent = msg;
            titleEl.style.color = 'var(--danger, #ff6b8a)';
        }
        
        const spinner = document.getElementById('loadingSpinner');
        if (spinner) spinner.remove();

        // Inject the "I'm feeling lucky" button directly into the main element
        if (!document.getElementById('luckyBtn') && mainEl) {
            const luckyBtn = document.createElement('button');
            luckyBtn.id = 'luckyBtn';
            luckyBtn.textContent = "View random crease pattern";
            luckyBtn.style.display = 'block';
            luckyBtn.style.margin = '2rem auto'; 
            
            luckyBtn.addEventListener('click', () => {
                const randomId = Math.floor(Math.random() * 1000000) + 1;
                window.location.search = `?id=5d${randomId}`;
            });
            
            mainEl.appendChild(luckyBtn);
        }
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
        if (titleEl) {
            const symTitle = sym.charAt(0).toUpperCase() + sym.slice(1);
            titleEl.textContent = `Pattern ${N}${symChar}${tilingId}`;
        }
        
        // Remove loading spinner and reveal containers and export buttons
        const spinner = document.getElementById('loadingSpinner');
        if (spinner) spinner.remove();
        layoutContainers.forEach(c => c.style.display = '');

        // Save globally
        window.currentResult = result;
        drawAllPanels(result);

    } catch (err) {
        showError(`Error: ${err.message}`);
    }
}

// Start sequence
initView();