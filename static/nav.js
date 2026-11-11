/* Mobile nav toggle. */
(function () {
  var btn = document.querySelector(".nav-toggle");
  var nav = document.getElementById("main-nav");
  if (!btn || !nav) return;
  btn.addEventListener("click", function () {
    var open = nav.classList.toggle("open");
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  });
  nav.addEventListener("click", function (e) {
    if (e.target.tagName === "A") {
      nav.classList.remove("open");
      btn.setAttribute("aria-expanded", "false");
    }
  });

  // Student app sidebar: collapse behind a Menu button on small screens.
  var sideBtn = document.querySelector(".side-toggle");
  var side = document.getElementById("side-nav");
  if (sideBtn && side) {
    sideBtn.addEventListener("click", function () {
      var open = side.classList.toggle("open");
      sideBtn.setAttribute("aria-expanded", open ? "true" : "false");
    });
    side.addEventListener("click", function (e) {
      if (e.target.tagName === "A") {
        side.classList.remove("open");
        sideBtn.setAttribute("aria-expanded", "false");
      }
    });
  }
})();

// Prevent double-submits on auth/settings forms.
document.addEventListener("submit", function (e) {
  var f = e.target;
  if (!f || f.tagName !== "FORM" || f.dataset.noGuard) return;
  var btn = f.querySelector('button[type="submit"],button:not([type])');
  if (btn && !btn.disabled) {
    btn.disabled = true;
    btn.dataset.label = btn.textContent;
    btn.textContent = "Please wait…";
    setTimeout(function () { btn.disabled = false; btn.textContent = btn.dataset.label; }, 8000);
  }
});
