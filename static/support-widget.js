(function () {
  var launcher = document.getElementById("support-launcher");
  var panel = document.getElementById("support-panel");
  if (!launcher || !panel) return;

  var closeButton = panel.querySelector(".support-close");

  function setOpen(open) {
    launcher.setAttribute("aria-expanded", String(open));
    panel.hidden = !open;
    if (open) {
      var firstField = panel.querySelector("input:not([type=hidden]), select, textarea, a.btn");
      if (firstField) firstField.focus();
    } else {
      launcher.focus();
    }
  }

  launcher.addEventListener("click", function () {
    setOpen(launcher.getAttribute("aria-expanded") !== "true");
  });
  closeButton.addEventListener("click", function () {
    setOpen(false);
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && launcher.getAttribute("aria-expanded") === "true") {
      setOpen(false);
    }
  });
  document.addEventListener("click", function (event) {
    if (launcher.getAttribute("aria-expanded") === "true"
        && !panel.contains(event.target) && !launcher.contains(event.target)) {
      setOpen(false);
    }
  });
})();
