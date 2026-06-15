import * as Utils from './js/utils.js';
import { makeSvg, renderCpSvg, renderPackingSvg, renderFoldSvg, renderGraphSvg, renderHeatSvg, transformX, transformY, fitScale } from './js/renderers.js';
// --- Global UI & Modal Wiring ---
const themeToggleBtn = document.getElementById("themeToggleBtn");
const donateBtn = document.getElementById("donateBtn");
const discordBtn = document.getElementById("discordBtn");
const languageBtn = document.getElementById("languageBtn");
const shareBtn = document.getElementById("sharebtn");

const donateModal = document.getElementById("donateModal");
const discordModal = document.getElementById("discordModal");
const languageModal = document.getElementById("languageModal");
const shareModal = document.getElementById("shareModal");

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
setupModal(shareBtn, shareModal, "closeShareModal");
if (shareBtn && shareUrlInput) {
    shareBtn.addEventListener("click", () => {
        shareUrlInput.value = window.location.href; // Grabs the exact current page URL
        if (copyFeedback) copyFeedback.style.opacity = '0'; // Reset the "copied" text
    });
}

// 2. Write to clipboard and show feedback when copied
if (copyLinkBtn && shareUrlInput) {
    copyLinkBtn.addEventListener("click", async () => {
        try {
            await navigator.clipboard.writeText(shareUrlInput.value);
            
            // Show "Copied!" feedback
            if (copyFeedback) {
                copyFeedback.style.opacity = '1';
                setTimeout(() => {
                    copyFeedback.style.opacity = '0';
                }, 2500); // Fade out after 2.5 seconds
            }
        } catch (err) {
            console.error("Failed to copy link:", err);
        }
    });
}


if (themeToggleBtn) {
  themeToggleBtn.addEventListener("click", () => {
    const currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
    const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
    Utils.applyTheme(nextTheme, true);
    
    // NOTE: We do not need to redraw the SVGs here. 
    // renderers.js assigns CSS classes (e.g., .cp-m, .cp-v), so styles.css handles the light/dark flip natively!
  });
}


const exportFoldBtn = document.getElementById("exportFoldBtn");
const exportDebugBtn = document.getElementById("exportDebugBtn");

if (exportFoldBtn) {
  exportFoldBtn.addEventListener("click", () => {Utils.exportFold(window.currentResult.cp);});
}

if (exportDebugBtn) {
  exportDebugBtn.addEventListener("click", () => {
      Utils.exportJson(window.currentResult);
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
// --- Coordinate Math & Ray Casting ---
function getSafeBounds(model, compMap = null) {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    
    // 1. Prefer compMap if available (These are guaranteed to be pure [x, y] floats from Python)
    if (compMap && compMap.length > 0) {
        for (const facet of compMap) {
            for (const pt of facet.vertices) {
                if (pt[0] < minX) minX = pt[0];
                if (pt[0] > maxX) maxX = pt[0];
                if (pt[1] < minY) minY = pt[1];
                if (pt[1] > maxY) maxY = pt[1];
            }
        }
        return { minX, maxX, minY, maxY };
    }

    // 2. Fallback for Graph Nodes
    const pts = model.vertices || (model.nodes ? model.nodes.map(n => n.pos || [n.x, n.y]) : []);
    for (const pt of pts) {
        if (!pt || typeof pt === 'string') continue; // Skip unparsed stringified Python objects
        
        const x = pt[0] !== undefined ? pt[0] : pt.x;
        const y = pt[1] !== undefined ? pt[1] : pt.y;
        
        if (x !== undefined && y !== undefined) {
            if (x < minX) minX = x;
            if (x > maxX) maxX = x;
            if (y < minY) minY = y;
            if (y > maxY) maxY = y;
        }
    }
    return { minX, maxX, minY, maxY };
}

function getSvgCoords(event, svgEl) {
    const pt = svgEl.createSVGPoint();
    pt.x = event.clientX;
    pt.y = event.clientY;
    const svgP = pt.matrixTransform(svgEl.getScreenCTM().inverse());
    return { x: svgP.x, y: svgP.y };
}

function isPointInPolygon(point, vs) {
    let x = point.x, y = point.y;
    let inside = false;
    for (let i = 0, j = vs.length - 1; i < vs.length; j = i++) {
        let xi = vs[i][0], yi = vs[i][1];
        let xj = vs[j][0], yj = vs[j][1];
        let intersect = ((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi);
        if (intersect) inside = !inside;
    }
    return inside;
}

// --- Interactive Highlighting Logic ---
function highlightComponent(compId, result) {
    // 1. Clear previous highlights
    document.getElementById('packing-highlight-layer')?.remove();
    document.querySelectorAll('#target-tree line.edge').forEach(line => {
        line.style.stroke = '';
        line.style.strokeWidth = '';
    });

    if (compId === null || compId === undefined) return;

    // 2. Highlight Tree Edge
    const targetLine = document.querySelector(`#target-tree line.edge[data-comp-id="${compId}"]`);
    if (targetLine) {
        targetLine.style.stroke = 'var(--danger, #ff6b8a)';
        targetLine.style.strokeWidth = '12'; // Thick visual highlight
        targetLine.parentNode.appendChild(targetLine); // Bring visual line to front
        
        // Bring hitbox to the front so it doesn't get buried and remains clickable
        const hitbox = document.querySelector(`#target-tree line.edge-hitbox[data-comp-id="${compId}"]`);
        if (hitbox) hitbox.parentNode.appendChild(hitbox);
    }

    // 3. Highlight Packing Facets
    const packingSvg = document.querySelector('#target-packing svg');
    if (packingSvg && result && result.comp_map) {
        const vb = packingSvg.getAttribute('viewBox').split(' ').map(Number);
        const w = vb[2] || 1000;
        const h = vb[3] || 1000;

        const layer = makeSvg('g', { id: 'packing-highlight-layer' });
        const facets = result.comp_map.filter(f => f.comp_id === compId);
        
        // Pass comp_map to guarantee clean float bounds for perfect scaling
        const bounds = getSafeBounds(result.packing, result.comp_map);
        const scale = fitScale(bounds, w, h);

        facets.forEach(facet => {
            const points = facet.vertices.map(v => {
                const sx = transformX(v[0], bounds, scale, w);
                const sy = transformY(v[1], bounds, scale, h);
                return `${sx},${sy}`;
            }).join(' ');

            layer.appendChild(makeSvg('polygon', {
                points: points,
                fill: 'rgba(255, 107, 138, 0.4)',
                // stroke: 'var(--danger, #ff6b8a)',
                'stroke-width': '0',
                'pointer-events': 'none' // Crucial: prevents overlay from blocking future clicks
            }));
        });
        packingSvg.appendChild(layer);
    }
}

// --- Dynamic SVG Rendering ---
function populatePanel(containerId, renderFn, data, options = {}) {
    if (!data) return null;
    const container = document.getElementById(containerId);
    if (!container) return null;
    container.innerHTML = ''; 
    
    let viewBox = options.viewBox || "0 0 1000 1000";
    // if (renderFn === renderHeatSvg) viewBox = "0 0 420 240";

    const svg = makeSvg("svg", { 
        viewBox: viewBox, 
        style: "width: 100%; height: 100%; display: block; overflow: visible;" 
    });
    
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
    const cpSvg = populatePanel("target-cp", renderCpSvg, result.cp, {w: 1000, h: 1000});
    setupInteractiveSvg(cpSvg, "CP", result);
    
    const packingSvg = populatePanel("target-packing", renderPackingSvg, result.packing, {w: 1000, h: 1000});
    setupInteractiveSvg(packingSvg, "Packing", result);
    
    const treeSvg = populatePanel("target-tree", renderGraphSvg, result.tree, {w: 1000, h: 1000, nodeFill: "#8cffc1"});
    setupInteractiveSvg(treeSvg, "Tree", result);
    
    populatePanel("target-topology", renderGraphSvg, result.topology, {w: 1000, h: 1000, nodeFill: "#a7c7ff"});
    populatePanel("target-tiling", renderGraphSvg, result.solved_tiling, {w: 1000, h: 1000, nodeFill: "#8cffc1"});
    populatePanel("target-fold", renderFoldSvg, result.fold, {w: 1000, h: 1000});
    
    populatePanel("target-heat", renderHeatSvg, result.heat);
}

function setupInteractiveSvg(svg, name, result) { 
    if (!svg) return;
    svg.style.cursor = name === "Tree" ? "pointer" : "crosshair";

    // --- MASSIVE CLICK WINDOW FOR TREE EDGES ---
    if (name === "Tree") {
        // Wait briefly for the DOM to append the SVG children
        setTimeout(() => {
            const edges = svg.querySelectorAll('line.edge');
            edges.forEach(edge => {
                // Prevent infinite duplication on re-renders
                if (edge.nextSibling && edge.nextSibling.classList && edge.nextSibling.classList.contains('edge-hitbox')) return;
                
                const fatBox = edge.cloneNode(true);
                fatBox.setAttribute('stroke', 'transparent');
                fatBox.setAttribute('stroke-width', '25'); // Invisible 25px wide click target
                fatBox.setAttribute('class', 'edge-hitbox');
                fatBox.style.cursor = 'pointer';
                
                // Insert directly above the visual line
                edge.parentNode.insertBefore(fatBox, edge.nextSibling);
            });
        }, 50);
    }

    svg.addEventListener("click", (e) => {
        const pt = getSvgCoords(e, svg);

        if (name === "Packing" && result && result.comp_map) {
            const vb = svg.getAttribute('viewBox').split(' ').map(Number);
            const w = vb[2] || 1000;
            const h = vb[3] || 1000;
            
            // Bypass corrupt Vertex4 strings using the clean comp_map floats
            const bounds = getSafeBounds(result.packing, result.comp_map);
            const scale = fitScale(bounds, w, h);
            
            let clickedCompId = null;

            for (const facet of result.comp_map) {
                // Pre-map mathematical vertices to the exact visual SVG coordinates
                const svgPolygon = facet.vertices.map(v => [
                    transformX(v[0], bounds, scale, w),
                    transformY(v[1], bounds, scale, h)
                ]);

                if (isPointInPolygon(pt, svgPolygon)) {
                    clickedCompId = facet.comp_id;
                    break;
                }
            }
            highlightComponent(clickedCompId, result);
        }

        if (name === "Tree" && result) {
            // Trigger on either the visual edge OR the invisible fat hitbox
            const edge = e.target.closest('line.edge, line.edge-hitbox');
            if (edge) {
                const compId = parseInt(edge.getAttribute('data-comp-id'));
                if (!isNaN(compId)) highlightComponent(compId, result);
            } else {
                highlightComponent(null, result);
            }
        }
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

    const luckyBtn = document.createElement('button');
        luckyBtn.id = 'luckyBtn';
        luckyBtn.textContent = "View random crease pattern";
        luckyBtn.style.display = 'block';
        luckyBtn.style.margin = '2rem auto'; 
        
        luckyBtn.addEventListener('click', () => {
            const randomId = Math.floor(Math.random() * 1000000) + 1;
            window.location.search = `?id=5d${randomId}`;
        });

    const showError = (msg) => {
        if (titleEl) {
            titleEl.textContent = msg;
            titleEl.style.color = 'var(--danger, #ff6b8a)';
        }
        
        const spinner = document.getElementById('loadingSpinner');
        if (spinner) spinner.remove();

        // Inject the "I'm feeling lucky" button directly into the main element
        if (!document.getElementById('luckyBtn') && mainEl) {
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
        console.log(result.refs)
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
        mainEl.appendChild(luckyBtn);

    } catch (err) {
        showError(`Error: ${err.message}`);
    }
}

// Start sequence
initView();