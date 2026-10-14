---
layout: default
title: "Sources & models"
permalink: /dashboard/sources/
---
{% assign d = site.data.dashboard %}{% assign today = d.today %}

<div class="page-content dashboard" markdown="0">

<h1>Sources &amp; models</h1>
{% include dashnav.html %}

<h2>Today's source funnel</h2>
{% if today and today.sources and today.sources.size > 0 %}
<p class="chart-note">
  Per-source counts for run {{ today.target_date | default: "—" }}. Fetched is the fetch stage total,
  prefilter passed is after the pre-filter, included is after filtering, and cited counts the
  items that reached the brief plus their related (clustered) sources.
</p>
<p class="chart-note">
  Note: <em>prefilter passed</em> counts deduped items that were not deterministically excluded
  (it still includes related and non-primary items), so it is an approximation of the true
  Iran-signal pass rate, not an exact count.
</p>
<div class="table-wrap">
<table class="dash-table">
  <thead><tr><th>Source</th><th>Fetched</th><th>Prefilter passed</th><th>Included</th><th>Cited</th><th>Down</th></tr></thead>
  <tbody>
  {% for src in today.sources %}
    <tr>
      <td>{{ src.name }}</td>
      <td>{{ src.fetched | default: 0 }}</td>
      <td>{% if src.prefilter_passed == nil %}not recorded{% else %}{{ src.prefilter_passed | default: 0 }}{% endif %}</td>
      <td>{{ src.included | default: 0 }}</td>
      <td>{{ src.posted | default: 0 }}</td>
      <td>{% if src.down == nil %}—{% elsif src.down %}yes{% else %}no{% endif %}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% else %}
<p class="chart-note">No per-source funnel recorded for today's run.</p>
{% endif %}

<h2>Models used today</h2>
{% if today and today.models and today.models.size > 0 %}
<p class="chart-note">{{ today.models | join: ", " }}</p>
{% else %}
<p class="chart-note">No model usage recorded for today's run.</p>
{% endif %}

<h2>Failovers</h2>
{% if today and today.failovers and today.failovers.size > 0 %}
<div class="table-wrap">
<table class="dash-table">
  <thead><tr><th>Model failed over from</th><th>Count</th></tr></thead>
  <tbody>
  {% for pair in today.failovers %}
    <tr><td>{{ pair[0] }}</td><td>{{ pair[1] | default: 0 }}</td></tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% else %}
<p class="chart-note">No failovers recorded for today's run.</p>
{% endif %}

</div>
