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
    <span class="card-sub">since run tracking began</span>
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
  {% if f %}
  <div class="card">
    <span class="card-value">{{ f.counts.failed_not_backfilled | size | default: 0 }}</span>
    <span class="card-label">Failed, not repaired</span>
    <span class="card-sub">from the failure log</span>
  </div>
  <div class="card">
    <span class="card-value">{{ f.counts.backfilled | default: 0 }}</span>
    <span class="card-label">Backfilled days</span>
  </div>
  {% endif %}
</section>

<h2>Failures</h2>
{% if f %}
<p class="chart-note">
  {{ f.counts.failed | default: 0 }} failed day(s),
  {{ f.counts.backfilled | default: 0 }} backfilled,
  {{ f.counts.failed_and_backfilled | default: 0 }} repaired.
  Full detail on the <a href="{{ '/failures/' | relative_url }}">failure log</a>.
</p>

{% if f.failed_dates and f.failed_dates.size > 0 %}
<div class="table-wrap">
<table class="dash-table">
  <thead><tr><th>Date</th><th>What failed</th><th>Run</th><th>Backfilled</th><th>Fix</th></tr></thead>
  <tbody>
  {% for day in f.failed_dates %}
    <tr>
      <td>{{ day.date }}</td>
      <td>
        {{ day.failed_stages | join: ", " | default: "—" }}
        {% if day.errors.size > 0 %}<br><span class="funnel-err">{{ day.errors | join: "; " | truncate: 160 }}</span>{% endif %}
      </td>
      <td>{% if day.run_url %}<a href="{{ day.run_url }}" rel="noopener">run {{ day.run_id }}</a>{% else %}—{% endif %}</td>
      <td>{% if day.backfilled %}yes{% else %}no{% endif %}</td>
      <td>{% if day.backfill_commit %}<a href="{{ day.backfill_commit.url }}" rel="noopener">{{ day.backfill_commit.short }}</a>{% else %}—{% endif %}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% else %}
<p class="chart-note">No failed days recorded.</p>
{% endif %}

{% if f.counts.failed_not_backfilled.size > 0 %}
<h3>Failed and not repaired</h3>
<p class="chart-note">{{ f.counts.failed_not_backfilled | join: ", " }}</p>
{% endif %}
{% if f.counts.backfilled_without_failed.size > 0 %}
<h3>Backfilled without a recorded failure</h3>
<p class="chart-note">{{ f.counts.backfilled_without_failed | join: ", " }}</p>
{% endif %}
{% else %}
<p class="chart-note">No failure data yet. See the <a href="{{ '/failures/' | relative_url }}">failure log</a>.</p>
{% endif %}

<h2>Days with a recorded status</h2>
<p class="chart-note">Days before run tracking began show as "unknown" and are omitted. Statuses are success, degraded, and failed. Days that failed before run tracking appear above, from the failure log.</p>
<div class="table-wrap">
<table class="dash-table">
  <thead><tr><th>Date</th><th>Status</th><th>Failed stages</th><th>Degraded stages</th></tr></thead>
  <tbody>
  {% for day in d.days reversed %}
    {% if day.status == "success" or day.status == "degraded" or day.status == "failed" %}
    <tr>
      <td>{{ day.date }}</td>
      <td><span class="badge badge-{{ day.status }}">{{ day.status }}</span></td>
      <td>{{ day.failed_stages | join: ", " | default: "—" }}</td>
      <td>{{ day.degraded_stages | join: ", " | default: "—" }}</td>
    </tr>
    {% endif %}
  {% endfor %}
  </tbody>
</table>
</div>

</div>
