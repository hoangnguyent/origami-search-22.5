export function isMobileLayout() {
  // Return true if the screen is physically narrow OR if it's a touch device (like a landscape iPad)
  const isNarrow = window.matchMedia("(max-width: 768px)").matches;
  const isTouchDevice = window.matchMedia("(pointer: coarse)").matches;
  return isNarrow || isTouchDevice;
}