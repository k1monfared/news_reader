---
layout: default
title: Failures
permalink: /failures/
---

{% assign f = site.data.failures %}

<div class="page-content dashboard" markdown="0">

<h1>Failures</h1>

{% if f %}
<p class="chart-note">Updated {{ f.generated_at }}. {{ f.counts.failed }} failed day(s), {{ f.counts.backfilled }} backfilled day(s), {{ f.counts.failed_and_backfilled }} repaired. Generated automatically from GitHub Actions runs, the run database, and git history.</p>

<h2>Failed days</h2>
<div class="table-wrap">
<table class="dash-table">
  <thead><tr><th>Date</th><th>What failed</th><th>Run log</th><th>Backfilled</th><th>Fix commit</th></tr></thead>
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
      <td>{% if day.backfill_commit %}<a href="{{ day.backfill_commit.url }}" rel="noopener">{{ day.backfill_commit.short }}</a> {{ day.backfill_commit.message | truncate: 80 }}{% else %}—{% endif %}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
</div>

<h2>Backfilled days</h2>
<div class="table-wrap">
<table class="dash-table">
  <thead><tr><th>Date</th><th>Commit</th><th>Message</th><th>Posts</th></tr></thead>
  <tbody>
  {% for day in f.backfilled_dates %}
    <tr>
      <td>{{ day.date }}</td>
      <td>{% if day.commit %}<a href="{{ day.commit.url }}" rel="noopener">{{ day.commit.short }}</a>{% else %}—{% endif %}</td>
      <td>{% if day.commit %}{{ day.commit.message | truncate: 100 }}{% else %}—{% endif %}</td>
      <td>{{ day.posts | size }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
</div>

{% if f.counts.failed_not_backfilled.size > 0 %}
<h2>Failed but not backfilled</h2>
<p class="chart-note">{{ f.counts.failed_not_backfilled | join: ", " }}</p>
{% endif %}
{% if f.counts.backfilled_without_failed.size > 0 %}
<h2>Backfilled without a recorded failure</h2>
<p class="chart-note">{{ f.counts.backfilled_without_failed | join: ", " }}</p>
{% endif %}

{% else %}
<p class="chart-note">No failure data yet.</p>
{% endif %}

</div>
