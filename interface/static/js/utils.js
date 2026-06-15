import { state } from './state.js';
import { Locales } from './locales.js';

export const THEME_STORAGE_KEY = "search225-theme-preference";
export const DETAIL_LEFT_VIEW_KEY = "search225-detail-left-view";
export const DETAIL_RIGHT_VIEW_KEY = "search225-detail-right-view";
const systemThemeQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;

// let currentLang = localStorage.getItem('explori_lang') || 'en';

// const langButtons = document.querySelectorAll('.lang-btn');
// if (langButtons.length > 0) {
//   langButtons.forEach(btn => {
//     btn.addEventListener('click', (e) => {
//       const selectedLang = e.currentTarget.getAttribute('data-lang');
//       applyLanguage(selectedLang);
//       document.getElementById('languageModal').classList.add('hidden');
//     });
//   });
// }
// export function applyLanguage(lang) {
//   console.log("changing language")
//   // Check if the language exists AND actually has translations inside it
//   if (!Locales[lang] || Object.keys(Locales[lang]).length === 0) {
//     console.warn(`[i18n] Language '${lang}' is empty or missing. Falling back to English.`);
//     lang = 'en'; 
//   }
  
//   currentLang = lang;
//   localStorage.setItem('explori_lang', lang);

//   const dict = Locales[lang];

//   // 1. Update standard text content
//   document.querySelectorAll('[data-i18n]').forEach(el => {
//     const key = el.getAttribute('data-i18n');
//     if (dict[key]) el.textContent = dict[key];
//   });

//   // 2. Update tooltip titles
//   document.querySelectorAll('[data-i18n-title]').forEach(el => {
//     const key = el.getAttribute('data-i18n-title');
//     if (dict[key]) el.title = dict[key];
//   });

//   // 3. Update screen reader aria-labels
//   document.querySelectorAll('[data-i18n-aria]').forEach(el => {
//     const key = el.getAttribute('data-i18n-aria');
//     if (dict[key]) el.setAttribute('aria-label', dict[key]);
//   });
  
//   // 4. NEW: Visually update the buttons in the Language Modal
//   document.querySelectorAll('.lang-btn').forEach(btn => {
//     if (btn.getAttribute('data-lang') === lang) {
//       btn.classList.remove('secondary'); // Highlight the active language
//     } else {
//       btn.classList.add('secondary');    // Dim the inactive languages
//     }
//   });

//   // 5. NEW: Sync the dropdown in the Settings Modal
//   const langSelect = document.getElementById('languageSelect');
//   if (langSelect) {
//     langSelect.value = lang;
//   }
  
//   // Update HTML lang attribute
//   document.documentElement.lang = lang;
// }


export function readStoredDetailView(key, fallback) {
  try { const stored = localStorage.getItem(key); return stored || fallback; } catch { return fallback; }
}

export function persistDetailView(side, value) {
  try { localStorage.setItem(side === "left" ? DETAIL_LEFT_VIEW_KEY : DETAIL_RIGHT_VIEW_KEY, value); } catch {}
}

export function syncDetailViewPreferences() {
  state.detailViewModes.left = readStoredDetailView(DETAIL_LEFT_VIEW_KEY, state.detailViewModes.left);
  state.detailViewModes.right = readStoredDetailView(DETAIL_RIGHT_VIEW_KEY, state.detailViewModes.right);
}

export function readStoredThemePreference() {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return stored === 'light' || stored === 'dark' || stored === 'system' ? stored : null;
  } catch { return null; }
}

export function getEffectiveTheme(themePreference) {
  if (themePreference === 'light' || themePreference === 'dark') return themePreference;
  return systemThemeQuery && systemThemeQuery.matches ? 'light' : 'dark';
}

export function applyTheme(themePreference, persist = true) {
  const normalized = themePreference === 'light' || themePreference === 'dark' ? themePreference : 'system';
  const effective = getEffectiveTheme(normalized);
  document.documentElement.dataset.theme = effective;
  const themeSelect = document.getElementById("themeSelect"); if (themeSelect) themeSelect.value = normalized;
  if (persist) { try { localStorage.setItem(THEME_STORAGE_KEY, normalized); } catch {} }
}

export function setStatus(message, isError = false) {
  const statusEl = document.getElementById("status");
  if (!statusEl) return;
  statusEl.textContent = message;
  statusEl.style.color = isError ? "var(--danger)" : "var(--muted)";
}

export const symmetry_abbr = { "diag":"d", "book":"b", "none":"n" };

export function getMatchQuality(distance, queryNodeCount = 1) {
  const lang = localStorage.getItem('explori_lang') || 'en';
  const dict = Locales[lang] || Locales['en'];

  if (distance < 0.5) return { key: "Great", label: dict.qualityGreat };
  if (distance < 1.5) return { key: "Good", label: dict.qualityGood };
  if (distance < 3.0) return { key: "Acceptable", label: dict.qualityAcceptable };
  if (distance < 4.0) return { key: "Poor", label: dict.qualityPoor };
  return { key: "Terrible", label: dict.qualityTerrible };
}

export function exportFold(cp) {
  function getCartesian(v) {
    const vx = v[0] / v[1];
    const vy = v[2] / v[3];
    const vz = v[4] / v[5];
    const vw = v[6] / v[7];
    
    // Math.SQRT1_2 is exactly Math.sqrt(2) / 2
    const x = vx + Math.SQRT1_2 * (vy - vw);
    const y = vz + Math.SQRT1_2 * (vy + vw);
    return [x, y];
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

  // Map exact vertices directly to float coordinates
  const vertices_coords = cp.vertices.map(getCartesian);
  
  const edges_vertices = [];
  const edges_assignment = [];

  // Edges are already indexed, so we just map the types
  cp.edges.forEach(edge => {
    edges_vertices.push([edge[0], edge[1]]);
    edges_assignment.push(getFoldType(edge[2]));
  });

  const foldData = { 
    file_spec: 1.1, 
    file_creator: "SEARCH-22.5", 
    vertices_coords: vertices_coords, 
    edges_vertices: edges_vertices, 
    edges_assignment: edges_assignment 
  };

  const blob = new Blob([JSON.stringify(foldData, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  
  // Pull metadata safely from the global state if it exists
  const result = window.currentResult || {};
  const N = result.N || "N";
  const sym = result.symmetry || "sym";
  const tilingId = result.tiling_id || "unknown";
  
  a.href = url;
  a.download = `cp${N}${sym}${tilingId}.fold`;
  document.body.appendChild(a);
  a.click();
  
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function exportJson(result) {
  const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  const rank = result.rank || 1;
  const N = result.N || "N";
  const sym = result.symmetry || "sym";
  const tilingId = result.tiling_id || "unknown";
  a.href = url;
  a.download = `${N}${sym}${tilingId}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function Vertex4DtoCartesian(v){
  const SQRT2_2 = Math.SQRT2 / 2;
  const x = v[0] / v[1];
  const y = v[2] / v[3];
  const z = v[4] / v[5];
  const w = v[6] / v[7];
  return [
    x + SQRT2_2 * (y - w),
    z + SQRT2_2 * (y + w)
  ];
}
