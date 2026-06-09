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
