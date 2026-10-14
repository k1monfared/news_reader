---
layout: default
title: Content
permalink: /dashboard/content/
---
{% assign d = site.data.dashboard %}{% assign t = d.totals %}

<div class="page-content dashboard" markdown="0">

<h1>Content</h1>
{% include dashnav.html %}

<p class="chart-note">
  All time: {{ t.posted | default: 0 }} entries posted
  ({{ t.posted_en | default: 0 }} EN, {{ t.posted_fa | default: 0 }} FA),
  {{ t.links | default: 0 }} source links cited from {{ t.included | default: 0 }} included stories,
  and {{ t.biases | default: 0 }} bias observations.
</p>

<h2>Stories processed</h2>
<p class="chart-note">Stories fetched from all sources and run through the pipeline each day, before dedup and filtering.</p>
{% include chart.html metric="processed" label="Stories processed" %}

<h2>Entries posted</h2>
{% include chart.html metric="posted" label="Entries posted" %}
{% include chart.html metric="posted_en" label="Entries posted (EN)" %}
{% include chart.html metric="posted_fa" label="Entries posted (FA)" %}

<h2>Links and inclusion</h2>
{% include chart.html metric="links" label="Source links cited" %}
{% include chart.html metric="included" label="Stories included" %}

<h2>Story mix</h2>
{% include chart.html metric="new_stories" label="New stories" %}
{% include chart.html metric="continuations" label="Continuations" %}
{% include chart.html metric="developments" label="Developments" %}

<h2>Source bias observations</h2>
{% include chart.html metric="biases" label="Bias observations" %}

<script id="dashboard-days" type="application/json">{{ d.days | jsonify }}</script>
<span id="dashboard-base" data-base="{{ '/' | relative_url }}" hidden></span>
<script src="{{ '/assets/js/dashboard.js' | relative_url }}" defer></script>

</div>
