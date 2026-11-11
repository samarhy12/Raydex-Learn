/* Catalogue page: category chips + free-text search over course cards. */
(function () {
  var input = document.querySelector(".catalogue-search");
  var buttons = Array.prototype.slice.call(document.querySelectorAll(".course-filters .filter-chip"));
  var cards = Array.prototype.slice.call(document.querySelectorAll("article.course-card[data-category]"));
  if (!cards.length) return;

  var filter = "all";
  var query = "";

  function apply() {
    buttons.forEach(function (button) {
      var active = button.getAttribute("data-filter") === filter;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    cards.forEach(function (card) {
      var cat = card.getAttribute("data-category");
      var matchCat = filter === "all" || cat === filter;
      var matchQuery = !query || (card.textContent || "").toLowerCase().indexOf(query) !== -1;
      card.hidden = !(matchCat && matchQuery);
    });
  }

  buttons.forEach(function (button) {
    button.addEventListener("click", function () {
      filter = button.getAttribute("data-filter");
      apply();
    });
  });

  if (input) {
    input.addEventListener("input", function () {
      query = input.value.trim().toLowerCase();
      apply();
    });
  }
})();
