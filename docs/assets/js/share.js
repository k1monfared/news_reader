// Per-item share links for the daily brief.
// For each <details> in the post content we assign a stable, content-based
// id and inject a share button that copies the full URL (including the
// anchor) to the clipboard. Opening the page with a hash like #item-ab12
// auto-opens and scrolls to the matching item.
(function () {
  'use strict';

  function stableId(text) {
    // Small FNV-1a-like 32-bit hash, rendered in base36.
    var h = 0x811c9dc5;
    for (var i = 0; i < text.length; i++) {
      h ^= text.charCodeAt(i);
      h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
    }
    return 'item-' + h.toString(36);
  }

  function summaryText(details) {
    var s = details.querySelector('summary');
    if (!s) return details.textContent || '';
    return (s.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function buildShareButton(absoluteUrl) {
    var btn = document.createElement('button');
    btn.className = 'item-share';
    btn.type = 'button';
    btn.setAttribute('aria-label', 'Copy link to this item');
    btn.innerHTML =
      '<svg viewBox="0 0 24 24" width="14" height="14" ' +
      'fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<circle cx="18" cy="5" r="3"/>' +
      '<circle cx="6" cy="12" r="3"/>' +
      '<circle cx="18" cy="19" r="3"/>' +
      '<line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/>' +
      '<line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/>' +
      '</svg><span class="item-share-label">Share</span>';

    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      var done = function () {
        var original = btn.querySelector('.item-share-label');
        if (!original) return;
        var prev = original.textContent;
        original.textContent = 'Copied';
        btn.classList.add('item-share--copied');
        setTimeout(function () {
          original.textContent = prev;
          btn.classList.remove('item-share--copied');
        }, 1600);
      };
      var fail = function () {
        window.prompt('Copy this link:', absoluteUrl);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(absoluteUrl).then(done, fail);
      } else {
        fail();
      }
    });
    return btn;
  }

  function openAnchor() {
    var hash = window.location.hash;
    if (!hash || hash.length < 2) return;
    var id = hash.slice(1);
    var el = document.getElementById(id);
    if (!el) return;
    if (el.tagName && el.tagName.toLowerCase() === 'details') {
      el.open = true;
    }
    // Scroll a bit after open() paints.
    setTimeout(function () {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 50);
  }

  function init() {
    var container = document.querySelector('.post-content');
    if (!container) return;
    var items = container.querySelectorAll('details');
    if (!items.length) return;

    var baseUrl = window.location.origin + window.location.pathname;
    var seen = Object.create(null);

    items.forEach(function (details, idx) {
      var id = stableId(summaryText(details));
      if (seen[id]) {
        id = id + '-' + idx;
      }
      seen[id] = true;
      details.id = id;

      var footer = document.createElement('div');
      footer.className = 'item-share-wrap';
      footer.appendChild(buildShareButton(baseUrl + '#' + id));
      details.appendChild(footer);
    });

    openAnchor();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.addEventListener('hashchange', openAnchor);
})();
