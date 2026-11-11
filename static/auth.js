/* Show or hide the password. */
(function () {
  document.querySelectorAll(".pw-toggle").forEach(function (b) {
    b.addEventListener("click", function () {
      var f = document.getElementById(b.getAttribute("data-target"));
      var show = f.type === "password";
      f.type = show ? "text" : "password";
      b.textContent = show ? "Hide" : "Show";
      b.setAttribute("aria-label", show ? "Hide password" : "Show password");
    });
  });
})();
