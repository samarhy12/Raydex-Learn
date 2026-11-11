/* Hero code demo: tabs, plus a Run button that shows the output. */
(function () {
  var win = document.getElementById("demo");
  if (!win) return;
  var tabs = Array.prototype.slice.call(win.querySelectorAll(".tab"));
  var out = document.getElementById("out");
  var run = document.getElementById("run");
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var RESULT = {
    py: ["Total fare: GH₵ 13.5"],
    js: ["Total fare: GH₵ 13.5"]
  };
  var current = "py";

  function show(pane) {
    current = pane;
    tabs.forEach(function (t) {
      var on = t.getAttribute("data-pane") === pane;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      document.getElementById("pane-" + t.getAttribute("data-pane")).hidden = !on;
    });
    out.innerHTML = '<span class="out-hint">Press Run to see the output.</span>';
  }

  function runIt() {
    out.innerHTML = "";
    var lines = RESULT[current].concat(["Finished with no errors."]);
    lines.forEach(function (text, i) {
      var d = document.createElement("div");
      d.className = "out-line " + (i === 0 ? "ok" : "dim");
      d.textContent = text;
      d.style.animationDelay = reduce ? "0s" : (i * 0.28) + "s";
      out.appendChild(d);
    });
  }

  tabs.forEach(function (t, i) {
    t.addEventListener("click", function () { show(t.getAttribute("data-pane")); });
    t.addEventListener("keydown", function (e) {
      var n = null;
      if (e.key === "ArrowRight") n = (i + 1) % tabs.length;
      if (e.key === "ArrowLeft") n = (i - 1 + tabs.length) % tabs.length;
      if (n !== null) { e.preventDefault(); tabs[n].focus(); show(tabs[n].getAttribute("data-pane")); }
    });
  });
  run.addEventListener("click", runIt);

  // One moment on load: run it once so the page shows what a result looks like.
  setTimeout(runIt, reduce ? 0 : 900);
})();

/* Course filter box on the home page. */
(function () {
  var buttons = Array.prototype.slice.call(document.querySelectorAll(".filter-chip"));
  var cards = Array.prototype.slice.call(document.querySelectorAll("article.course-card[data-category]"));
  if (!buttons.length || !cards.length) return;

  function apply(filter) {
    buttons.forEach(function (button) {
      var active = button.getAttribute("data-filter") === filter;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });

    cards.forEach(function (card) {
      var match = filter === "all" || card.getAttribute("data-category") === filter;
      card.hidden = !match;
    });
  }

  buttons.forEach(function (button) {
    button.addEventListener("click", function () {
      apply(button.getAttribute("data-filter"));
    });
  });
})();

/* Reveal meaningful groups as they enter view; content stays visible without motion support. */
(function () {
  if (!("IntersectionObserver" in window) ||
      (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches)) return;

  var items = Array.prototype.slice.call(document.querySelectorAll(
    ".course-card, .section .sec-head, .section .steps li, .eco-card"
  ));
  if (!items.length) return;

  document.documentElement.classList.add("home-motion");
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-visible");
      observer.unobserve(entry.target);
    });
  }, { threshold: 0.12, rootMargin: "0px 0px -24px 0px" });

  items.forEach(function (item) {
    item.classList.add("home-reveal");
    observer.observe(item);
  });
})();
