/* Raydex Learn theme manager.
   Preference lives in localStorage["rl-theme"]: "light" | "dark" | "system".
   "system" (or unset) follows prefers-color-scheme via the CSS media query —
   no data-theme attribute is set, so there is no flash of wrong theme. */
(function () {
  var KEY = "rl-theme";

  function pref() {
    try { return localStorage.getItem(KEY) || "system"; } catch (e) { return "system"; }
  }
  function resolved() {
    var p = pref();
    if (p === "light" || p === "dark") return p;
    return window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function apply() {
    var p = pref();
    if (p === "light" || p === "dark") document.documentElement.dataset.theme = p;
    else delete document.documentElement.dataset.theme;
    syncUI();
  }
  function set(p) {
    try { localStorage.setItem(KEY, p); } catch (e) {}
    apply();
  }

  function syncUI() {
    var dark = resolved() === "dark";
    // Icons are inline in the markup; CSS shows sun or moon from the theme.
    document.querySelectorAll(".theme-toggle").forEach(function (b) {
      b.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
      b.title = dark ? "Switch to light theme" : "Switch to dark theme";
    });
    var cur = pref();
    document.querySelectorAll("input[name=rl-theme-opt]").forEach(function (r) {
      r.checked = r.value === cur;
    });
  }

  function init() {
    document.querySelectorAll(".theme-toggle").forEach(function (b) {
      b.addEventListener("click", function () {
        // Flip the currently resolved theme; an explicit choice is stored.
        set(resolved() === "dark" ? "light" : "dark");
      });
    });
    document.querySelectorAll("input[name=rl-theme-opt]").forEach(function (r) {
      r.addEventListener("change", function () { if (r.checked) set(r.value); });
    });
    // Keep the toggle icon honest if the OS theme flips while on "system".
    if (window.matchMedia) {
      var mq = matchMedia("(prefers-color-scheme: dark)");
      if (mq.addEventListener) mq.addEventListener("change", syncUI);
    }
    apply();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
  window.RLTheme = { get: pref, set: set, resolved: resolved };
})();
