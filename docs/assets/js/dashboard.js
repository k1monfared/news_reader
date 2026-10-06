// Interactive dashboard charts: horizontal scroll, zoom, drag-to-pan, hover
// tooltips, and click-to-open-the-day's-brief. Data is embedded by the Jekyll
// page in #dashboard-days as JSON; one chart is built per .chart-block.
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

  var baseEl = document.getElementById("dashboard-base");
  var base = baseEl ? baseEl.getAttribute("data-base") || "/" : "/";

  function valueOf(day, metric) {
    if (metric === "emails_total") return day.emails_total || 0;
    if (metric === "deliveries") return day.deliveries || 0;
    if (metric === "tokens_input") return (day.tokens && day.tokens.input) || 0;
    if (metric === "tokens_output") return (day.tokens && day.tokens.output) || 0;
    if (metric === "tokens_thinking") return (day.tokens && day.tokens.thinking) || 0;
    if (metric === "tokens_total") return day.tokens_total || 0;
    if (metric === "biases") return day.biases || 0;
    if (metric === "new_stories") return day.new_stories || 0;
    if (metric === "continuations") return day.continuations || 0;
    if (metric === "developments") return day.developments || 0;
    return day[metric] || 0;
  }

  function briefUrl(date) {
    return base + "daily-brief/" + date.replace(/-/g, "/") + "/daily-brief.html";
  }

  var MIN_W = 4;
  var MAX_W = 48;
  var DEFAULT_W = 14;

  function initChart(block) {
    var metric = block.getAttribute("data-chart");
    var bars = block.querySelector(".bars");
    var xaxis = block.querySelector(".chart-x");
    var scroll = block.querySelector(".chart-scroll");
    var chart = block.querySelector(".chart");
    var tipEl = block.querySelector(".chart-tip");
    var yMax = block.querySelector('[data-y="max"]');
    var yMid = block.querySelector('[data-y="mid"]');
    if (!bars || !scroll || !chart) return;

    var vals = days.map(function (d) {
      return valueOf(d, metric);
    });
    var max = 1;
    vals.forEach(function (v) {
      if (v > max) max = v;
    });
    if (yMax) yMax.textContent = max;
    if (yMid) yMid.textContent = Math.floor(max / 2);

    var barW = DEFAULT_W;

    function showTip(e, text) {
      if (!tipEl) return;
      tipEl.textContent = text;
      tipEl.hidden = false;
      var rect = chart.getBoundingClientRect();
      tipEl.style.left = e.clientX - rect.left + "px";
      tipEl.style.top = e.clientY - rect.top - 8 + "px";
    }
    function hideTip() {
      if (tipEl) tipEl.hidden = true;
    }

    function render() {
      bars.innerHTML = "";
      xaxis.innerHTML = "";
      var frag = document.createDocumentFragment();
      var labels = document.createDocumentFragment();
      days.forEach(function (d, i) {
        var v = vals[i];
        var tip = d.date + ": " + v;
        var col = document.createElement("div");
        col.className = "bar-col";
        col.style.width = barW + "px";
        col.setAttribute("data-tip", tip);
        col.title = tip;
        col.addEventListener("mouseenter", function (e) { showTip(e, tip); });
        col.addEventListener("mousemove", function (e) { showTip(e, tip); });
        col.addEventListener("mouseleave", hideTip);
        col.addEventListener("click", function () {
          if (scroll.dataset.dragged === "1") return;
          window.location.href = briefUrl(d.date);
        });
        var bar = document.createElement("div");
        bar.className = "bar";
        bar.style.height = (v / max) * 100 + "%";
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

    var toolbar = block.querySelector(".chart-toolbar");
    if (toolbar) {
      var zin = toolbar.querySelector('[data-zoom="in"]');
      var zout = toolbar.querySelector('[data-zoom="out"]');
      var zreset = toolbar.querySelector('[data-zoom="reset"]');
      if (zin) zin.addEventListener("click", function () { zoom(1.4); });
      if (zout) zout.addEventListener("click", function () { zoom(1 / 1.4); });
      if (zreset) {
        zreset.addEventListener("click", function () {
          barW = DEFAULT_W;
          render();
          scroll.scrollLeft = scroll.scrollWidth;
        });
      }
    }

    var dragging = false;
    var moved = false;
    var startX = 0;
    var startLeft = 0;
    scroll.addEventListener("mousedown", function (e) {
      dragging = true;
      moved = false;
      startX = e.pageX;
      startLeft = scroll.scrollLeft;
      scroll.classList.add("dragging");
      e.preventDefault();
    });
    window.addEventListener("mouseup", function () {
      dragging = false;
      scroll.classList.remove("dragging");
      scroll.dataset.dragged = moved ? "1" : "0";
    });
    scroll.addEventListener("mousemove", function (e) {
      if (!dragging) return;
      if (Math.abs(e.pageX - startX) > 4) moved = true;
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
    scroll.scrollLeft = scroll.scrollWidth;
  }

  Array.prototype.forEach.call(
    document.querySelectorAll(".chart-block[data-chart]"),
    initChart
  );

  // Staleness: warn if the last recorded run is more than ~26h old.
  (function () {
    var meta = document.getElementById("dashboard-last-run");
    if (!meta) return;
    var raw = meta.getAttribute("data-last-run") || "";
    if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) raw += "T12:00:00Z";
    var t = Date.parse(raw);
    if (isNaN(t)) return;
    var ageH = (Date.now() - t) / 3600000;
    if (ageH > 26) {
      var banner = document.getElementById("run-stale");
      var dateEl = document.getElementById("run-stale-date");
      if (dateEl) {
        dateEl.textContent = raw.slice(0, 10) + " (~" + Math.round(ageH) + "h ago)";
      }
      if (banner) banner.hidden = false;
    }
  })();
})();
