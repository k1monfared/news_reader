---
layout: default
title: Reliability
permalink: /dashboard/reliability/
---
{% assign d = site.data.dashboard %}{% assign t = d.totals %}{% assign f = site.data.failures %}

<div class="page-content dashboard" markdown="0">

<h1>Reliability</h1>
{% include dashnav.html %}

<section class="cards">
  <div class="card">
    <span class="card-value">{{ t.days_succeeded | default: 0 }}</span>
    <span class="card-label">Clean days</span>
  </div>
  <div class="card">
    <span class="card-value">{{ t.days_degraded | default: 0 }}</span>
    <span class="card-label">Degraded days</span>
  </div>
  <div class="card">
    <span class="card-value">{{ t.days_failed_ever | default: 0 }}</span>
    <span class="card-label">Failed days (ever)</span>
    <span class="card-sub">{{ t.days_repaired | default: 0 }} repaired</span>
  </div>
</section>

<h2>Failures</h2>
{% if f %}
<p class="chart-note">
  {{ f.counts.failed | default: 0 }} failed day(s),
  {{ f.counts.backfilled | default: 0 }} backfilled,
  {{ f.counts.failed_and_backfilled | default: 0 }} repaired.
  See the <a href="{{ '/failures/' | relative_url }}">full failure log</a>.
</p>
{% else %}
<p class="chart-note">No failure data yet. See the <a href="{{ '/failures/' | relative_url }}">failure log</a>.</p>
{% endif %}

<h2>Status over time</h2>
{% if d.days and d.days.size > 0 %}
<div class="table-wrap">
<table class="dash-table">
  <thead><tr><th>Date</th><th>Status</th><th>Failed stages</th><th>Degraded stages</th></tr></thead>
  <tbody>
  {% for day in d.days reversed %}
    <tr>
      <td>{{ day.date }}</td>
      <td><span class="badge badge-{{ day.status | default: 'unknown' }}">{{ day.status | default: "unknown" }}</span></td>
      <td>{{ day.failed_stages | join: ", " | default: "—" }}</td>
      <td>{{ day.degraded_stages | join: ", " | default: "—" }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% else %}
<p class="chart-note">No per-day status records yet.</p>
{% endif %}

</div>
