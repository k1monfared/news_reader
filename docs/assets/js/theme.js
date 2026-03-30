(function () {
  var STORAGE_KEY = "theme-preference";
  var DARK = "dark";
  var LIGHT = "light";
  var SUN = "\u263C";
  var MOON = "\u263E";

  function getPreferred() {
    var stored = localStorage.getItem(STORAGE_KEY);
    if (stored === DARK || stored === LIGHT) {
      return stored;
    }
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
      return LIGHT;
    }
    return DARK;
  }

  function apply(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    var icon = document.querySelector(".theme-icon");
    if (icon) {
      icon.textContent = theme === DARK ? SUN : MOON;
    }
  }

  // Apply immediately to prevent flash
  apply(getPreferred());

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("theme-toggle");
    if (!btn) return;

    // Re-apply to update button icon now that DOM is ready
    apply(getPreferred());

    btn.addEventListener("click", function () {
      var current = document.documentElement.getAttribute("data-theme") || DARK;
      var next = current === DARK ? LIGHT : DARK;
      localStorage.setItem(STORAGE_KEY, next);
      apply(next);
    });
  });

  // Listen for system preference changes
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", function (e) {
      if (!localStorage.getItem(STORAGE_KEY)) {
        apply(e.matches ? LIGHT : DARK);
      }
    });
  }
})();
