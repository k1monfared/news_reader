// Interactive dashboard chart: horizontal scroll, zoom, and drag-to-pan.
// Data is embedded by the Jekyll page in #dashboard-days as JSON.
(function () {
  "use strict";
  var dataEl = document.getElementById("dashboard-days");
  if (!dataEl) return;

  var days;
  try {
    days = JSON.parse(dataEl.textContent);
  } catch (e) {
    return;
  }
  if (!days || !days.length) return;

  var bars = document.getElementById("chart-bars");
  var xaxis = document.getElementById("chart-x");
  var scroll = document.getElementById("chart-scroll");
  var yMax = document.querySelector('[data-y="max"]');
  var yMid = document.querySelector('[data-y="mid"]');
  var max = 1;
  days.forEach(function (d) {
    if (d.posted > max) max = d.posted;
  });
  if (yMax) yMax.textContent = max;
  if (yMid) yMid.textContent = Math.floor(max / 2);

  var MIN_W = 4;
  var MAX_W = 48;
  var DEFAULT_W = 14;
  var barW = DEFAULT_W;

  function render() {
    bars.innerHTML = "";
    xaxis.innerHTML = "";
    var frag = document.createDocumentFragment();
    var labels = document.createDocumentFragment();
    days.forEach(function (d, i) {
      var col = document.createElement("div");
      col.className = "bar-col";
      col.style.width = barW + "px";
      var tip = d.date + ": " + d.posted + " entries";
      col.setAttribute("data-tip", tip);
      col.title = tip;
      var bar = document.createElement("div");
      bar.className = "bar";
      bar.style.height = (d.posted / max) * 100 + "%";
      col.appendChild(bar);
      frag.appendChild(col);

      var label = document.createElement("span");
      label.className = "x-label";
      label.style.width = barW + "px";
      if (i % 5 === 0 || i === days.length - 1) label.textContent = d.date.slice(5);
      labels.appendChild(label);
    });
    bars.appendChild(frag);
    xaxis.appendChild(labels);
  }

  function zoom(factor) {
    var ratio = scroll.scrollWidth
      ? (scroll.scrollLeft + scroll.clientWidth / 2) / scroll.scrollWidth
      : 0;
    barW = Math.max(MIN_W, Math.min(MAX_W, Math.round(barW * factor)));
    render();
    scroll.scrollLeft = ratio * scroll.scrollWidth - scroll.clientWidth / 2;
  }

  var zoomIn = document.getElementById("chart-zoom-in");
  var zoomOut = document.getElementById("chart-zoom-out");
  var reset = document.getElementById("chart-reset");
  if (zoomIn) zoomIn.addEventListener("click", function () { zoom(1.4); });
  if (zoomOut) zoomOut.addEventListener("click", function () { zoom(1 / 1.4); });
  if (reset) {
    reset.addEventListener("click", function () {
      barW = DEFAULT_W;
      render();
      scroll.scrollLeft = scroll.scrollWidth;
    });
  }

  var dragging = false;
  var startX = 0;
  var startLeft = 0;
  scroll.addEventListener("mousedown", function (e) {
    dragging = true;
    startX = e.pageX;
    startLeft = scroll.scrollLeft;
    scroll.classList.add("dragging");
    e.preventDefault();
  });
  window.addEventListener("mouseup", function () {
    dragging = false;
    scroll.classList.remove("dragging");
  });
  scroll.addEventListener("mousemove", function (e) {
    if (!dragging) return;
    scroll.scrollLeft = startLeft - (e.pageX - startX);
  });

  scroll.addEventListener(
    "wheel",
    function (e) {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        zoom(e.deltaY < 0 ? 1.15 : 1 / 1.15);
      }
    },
    { passive: false }
  );

  render();
  // Open on the most recent days.
  scroll.scrollLeft = scroll.scrollWidth;
})();
