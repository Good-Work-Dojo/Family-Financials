// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// The saved theme, applied before the first paint. app/index.html loads this file with a plain blocking
// <script src="./theme.js"> ahead of the bundle (the content-security policy allows no inline script), so the page never
// shows the wrong theme for a frame. Dark unless this device chose light: the app's theme toggle (src/App.tsx) writes
// the same key (src/lib/demoKeys.ts THEME_KEY, which the demo build's copy of this file reads with the demo's prefix).
try {
  if (localStorage.getItem("theme") === "light") {
    document.documentElement.classList.remove("dark");
    document.documentElement.classList.add("light");
  }
} catch {
  // no storage (a private window, blocked site data): the default dark theme stays
}
