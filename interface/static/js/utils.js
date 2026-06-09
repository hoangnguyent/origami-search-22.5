import { state } from './state.js';

export const THEME_STORAGE_KEY = "search225-theme-preference";
export const DETAIL_LEFT_VIEW_KEY = "search225-detail-left-view";
export const DETAIL_RIGHT_VIEW_KEY = "search225-detail-right-view";
const systemThemeQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;

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
  // const value = Number(distance*Math.exp(queryNodeCount))/1000;
  // if (!Number.isFinite(distance)) return "unknown";
  if (distance < 0.5) return "Great";
  if (distance < 1.5) return "Good";
  if (distance < 3.0) return "Acceptable";
  if (distance < 4.0) return "Poor";
  return "Terrible";
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