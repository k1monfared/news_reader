---
layout: default
title: Dashboard
permalink: /dashboard/
---

{% assign d = site.data.dashboard %}
{% assign t = d.totals %}
{% assign today = d.today %}

<div class="page-content dashboard" markdown="0">

<h1>Dashboard</h1>

<p class="chart-note">Automated status and counts for this brief, rebuilt once per day after the run. Counts cover both languages. Last updated {{ t.generated_at | default: "unknown" }}.</p>

<h2>Today's run{% if today %} — {{ today.target_date }}{% endif %}</h2>
<div id="run-stale" class="run-stale" hidden>
  <strong>No recent run detected.</strong> The last recorded brief is <span id="run-stale-date"></span>. If it is more than about 26 hours old, the scheduled run likely failed.
</div>
{% if today %}
<span id="dashboard-last-run" data-last-run="{{ today.finished_at | default: today.target_date }}" hidden></span>
{% if today.recorded == false %}
<p class="chart-note">Stage funnel and token data were not recorded for this day (it predates run tracking); they will appear after the next run.</p>
{% endif %}
<section class="cards">
  <div class="card">
    <span class="card-value"><span class="badge badge-{{ today.status | default: 'unknown' }}">{{ today.status | default: "unknown" }}</span></span>
    <span class="card-label">Status</span>
    {% if today.failed_stages.size > 0 %}<span class="card-sub">failed: {{ today.failed_stages | join: ", " }}</span>{% elsif today.degraded_stages.size > 0 %}<span class="card-sub">degraded: {{ today.degraded_stages | join: ", " }}</span>{% endif %}
  </div>
  <div class="card">
    <span class="card-value">{{ today.duration_s | default: "—" }}</span>
    <span class="card-label">Duration (s)</span>
  </div>
  <div class="card">
    <span class="card-value">{{ today.funnel.fetched | default: "—" }}</span>
    <span class="card-label">Links processed</span>
    <span class="card-sub">{{ today.funnel.included | default: "—" }} made the cut</span>
  </div>
  <div class="card">
    <span class="card-value">{{ today.posted | default: 0 }}</span>
    <span class="card-label">Entries posted</span>
    <span class="card-sub">{{ today.posted_en | default: 0 }} EN · {{ today.posted_fa | default: 0 }} FA</span>
  </div>
  <div class="card">
    <span class="card-value">{{ today.links | default: 0 }}</span>
    <span class="card-label">Source links cited</span>
  </div>
  <div class="card">
    <span class="card-value">{% if today.emails.en.sent %}EN{% endif %}{% if today.emails.en.sent and today.emails.fa.sent %} · {% endif %}{% if today.emails.fa.sent %}FA{% endif %}{% unless today.emails.en.sent or today.emails.fa.sent %}—{% endunless %}</span>
    <span class="card-label">Emails sent</span>
    <span class="card-sub">{% if today.emails.en.sent %}{{ today.emails.en.recipients | default: "?" }} EN{% endif %}{% if today.emails.fa.sent %} · {{ today.emails.fa.recipients | default: "?" }} FA{% endif %}</span>
  </div>
  <div class="card">
    <span class="card-value">{{ today.subscribers | default: "—" }}</span>
    <span class="card-label">Subscribers</span>
  </div>
  <div class="card">
    <span class="card-value">{{ today.tokens.total.input | default: "—" }}</span>
    <span class="card-label">Input tokens</span>
    <span class="card-sub">{{ today.tokens.total.output | default: 0 }} out · {{ today.tokens.total.thinking | default: 0 }} think</span>
  </div>
</section>

<h3>Funnel</h3>
<ol class="funnel">
  {% for stage in today.stages %}
  <li class="funnel-step funnel-{{ stage.status }}{% if stage.degraded %} funnel-degraded{% endif %}">
    <span class="funnel-name">{{ stage.name | replace: "_", " " }}</span>
    <span class="funnel-status">{{ stage.status }}{% if stage.degraded %} · degraded{% endif %}</span>
    {% if stage.duration_s != nil %}<span class="funnel-dur">{{ stage.duration_s }}s</span>{% endif %}
    {% if stage.error %}<span class="funnel-err">{{ stage.error | truncate: 140 }}</span>{% endif %}
  </li>
  {% endfor %}
</ol>

<h3>Tokens this run</h3>
<div class="table-wrap">
<table class="dash-table">
  <thead><tr><th>Stage</th><th>Calls</th><th>Input</th><th>Output</th><th>Thinking</th><th>Total</th></tr></thead>
  <tbody>
    {% for s in today.tokens.by_stage %}
    <tr>
      <td>{{ s[0] | replace: "_", " " }}</td>
      <td>{{ s[1].calls }}</td>
      <td>{{ s[1].input }}</td>
      <td>{{ s[1].output }}</td>
      <td>{{ s[1].thinking }}</td>
      <td>{{ s[1].input | plus: s[1].output | plus: s[1].thinking }}</td>
    </tr>
    {% endfor %}
    <tr>
      <td><strong>Total</strong></td>
      <td>{{ today.tokens.total.calls | default: 0 }}</td>
      <td>{{ today.tokens.total.input | default: 0 }}</td>
      <td>{{ today.tokens.total.output | default: 0 }}</td>
      <td>{{ today.tokens.total.thinking | default: 0 }}</td>
      <td>{{ today.tokens.total.input | default: 0 | plus: today.tokens.total.output | plus: today.tokens.total.thinking }}</td>
    </tr>
  </tbody>
</table>
</div>
{% else %}
<p class="chart-note">No run recorded yet. The next pipeline run will populate today's status, funnel, and tokens.</p>
{% endif %}

<h2>All time</h2>
<section class="cards">
  <div class="card">
    <span class="card-value">{{ t.days_running | default: "—" }}</span>
    <span class="card-label">Days running</span>
    <span class="card-sub">since {{ t.first_date | default: "—" }}</span>
  </div>
  <div class="card">
    <span class="card-value">{{ t.briefs_en | default: "—" }}</span>
    <span class="card-label">Briefs published (EN)</span>
    <span class="card-sub">{{ t.briefs_fa | default: 0 }} in Farsi</span>
  </div>
  <div class="card">
    <span class="card-value">{{ t.posted | default: "—" }}</span>
    <span class="card-label">Brief entries posted</span>
    <span class="card-sub">{{ t.links | default: 0 }} source links cited</span>
  </div>
  <div class="card">
    <span class="card-value">{{ t.processed | default: "—" }}</span>
    <span class="card-label">Links processed</span>
    <span class="card-sub">{{ t.included | default: "—" }} made the cut</span>
  </div>
  <div class="card">
    <span class="card-value">{{ t.emails_total | default: 0 }}</span>
    <span class="card-label">Emails sent</span>
    <span class="card-sub">{{ t.emails_en | default: 0 }} EN · {{ t.emails_fa | default: 0 }} FA</span>
  </div>
  <div class="card">
    <span class="card-value">{{ t.deliveries | default: "—" }}</span>
    <span class="card-label">Recipient deliveries</span>
  </div>
  <div class="card">
    <span class="card-value">{{ t.subscribers_current | default: "—" }}</span>
    <span class="card-label">Subscribers now</span>
    <span class="card-sub">peak {{ t.subscribers_peak | default: "—" }}</span>
  </div>
  <div class="card">
    <span class="card-value">{{ t.subscribers_total_ever | default: "—" }}</span>
    <span class="card-label">Total ever subscribed</span>
    <span class="card-sub">distinct emails</span>
  </div>
  <div class="card">
    <span class="card-value">{{ t.tokens_total | default: "—" }}</span>
    <span class="card-label">LLM tokens</span>
    <span class="card-sub">{{ t.llm_calls | default: 0 }} calls</span>
  </div>
  <div class="card">
    <span class="card-value">{{ t.days_failed | default: 0 }}</span>
    <span class="card-label">Failed days</span>
    <span class="card-sub">{{ t.days_degraded | default: 0 }} degraded · {{ t.days_empty | default: 0 }} empty</span>
  </div>
</section>

<h2>Failures</h2>
{% assign f = site.data.failures %}
{% if f and f.counts %}
<p class="chart-note">{{ f.counts.failed }} failed day(s), {{ f.counts.backfilled }} backfilled day(s), {{ f.counts.failed_and_backfilled }} of them repaired. <a href="{{ '/failures/' | relative_url }}">Failure log</a>.</p>
{% else %}
<p class="chart-note">No failure data yet.</p>
{% endif %}

<h2>Averages</h2>
<p class="chart-note">Over {{ t.days_running | default: "—" }} days. Average entries per brief: {{ t.entries_per_brief_en | default: "—" }} EN, {{ t.entries_per_brief_fa | default: "—" }} FA.{% if t.subscribers_growth %} Subscriber growth: {{ t.subscribers_growth.total }} total{% if t.subscribers_growth.per_week != nil %}, {{ t.subscribers_growth.per_week }}/week{% endif %}.{% endif %}</p>
<div class="table-wrap">
<table class="dash-table">
  <thead>
    <tr><th>Metric</th><th>Per day</th><th>Per week</th><th>Per month</th></tr>
  </thead>
  <tbody>
    <tr><td>Brief entries posted</td><td>{{ t.rates.posted.per_day | default: "—" }}</td><td>{{ t.rates.posted.per_week | default: "—" }}</td><td>{{ t.rates.posted.per_month | default: "—" }}</td></tr>
    <tr><td>Entries posted (EN)</td><td>{{ t.rates.posted_en.per_day | default: "—" }}</td><td>{{ t.rates.posted_en.per_week | default: "—" }}</td><td>{{ t.rates.posted_en.per_month | default: "—" }}</td></tr>
    <tr><td>Entries posted (FA)</td><td>{{ t.rates.posted_fa.per_day | default: "—" }}</td><td>{{ t.rates.posted_fa.per_week | default: "—" }}</td><td>{{ t.rates.posted_fa.per_month | default: "—" }}</td></tr>
    <tr><td>Links processed</td><td>{{ t.rates.processed.per_day | default: "—" }}</td><td>{{ t.rates.processed.per_week | default: "—" }}</td><td>{{ t.rates.processed.per_month | default: "—" }}</td></tr>
    <tr><td>Links included</td><td>{{ t.rates.included.per_day | default: "—" }}</td><td>{{ t.rates.included.per_week | default: "—" }}</td><td>{{ t.rates.included.per_month | default: "—" }}</td></tr>
    <tr><td>New stories</td><td>{{ t.rates.new_stories.per_day | default: "—" }}</td><td>{{ t.rates.new_stories.per_week | default: "—" }}</td><td>{{ t.rates.new_stories.per_month | default: "—" }}</td></tr>
    <tr><td>Continuations (repeats, excluded)</td><td>{{ t.rates.continuations.per_day | default: "—" }}</td><td>{{ t.rates.continuations.per_week | default: "—" }}</td><td>{{ t.rates.continuations.per_month | default: "—" }}</td></tr>
    <tr><td>Developments (updates)</td><td>{{ t.rates.developments.per_day | default: "—" }}</td><td>{{ t.rates.developments.per_week | default: "—" }}</td><td>{{ t.rates.developments.per_month | default: "—" }}</td></tr>
    <tr><td>Biases found</td><td>{{ t.rates.biases.per_day | default: "—" }}</td><td>{{ t.rates.biases.per_week | default: "—" }}</td><td>{{ t.rates.biases.per_month | default: "—" }}</td></tr>
    <tr><td>Source links cited</td><td>{{ t.rates.links.per_day | default: "—" }}</td><td>{{ t.rates.links.per_week | default: "—" }}</td><td>{{ t.rates.links.per_month | default: "—" }}</td></tr>
    <tr><td>Emails sent</td><td>{{ t.rates.emails.per_day | default: "—" }}</td><td>{{ t.rates.emails.per_week | default: "—" }}</td><td>{{ t.rates.emails.per_month | default: "—" }}</td></tr>
    <tr><td>Recipient deliveries</td><td>{{ t.rates.deliveries.per_day | default: "—" }}</td><td>{{ t.rates.deliveries.per_week | default: "—" }}</td><td>{{ t.rates.deliveries.per_month | default: "—" }}</td></tr>
    <tr><td>Input tokens</td><td>{{ t.rates.tokens_input.per_day | default: "—" }}</td><td>{{ t.rates.tokens_input.per_week | default: "—" }}</td><td>{{ t.rates.tokens_input.per_month | default: "—" }}</td></tr>
    <tr><td>Output tokens</td><td>{{ t.rates.tokens_output.per_day | default: "—" }}</td><td>{{ t.rates.tokens_output.per_week | default: "—" }}</td><td>{{ t.rates.tokens_output.per_month | default: "—" }}</td></tr>
    <tr><td>Thinking tokens</td><td>{{ t.rates.tokens_thinking.per_day | default: "—" }}</td><td>{{ t.rates.tokens_thinking.per_week | default: "—" }}</td><td>{{ t.rates.tokens_thinking.per_month | default: "—" }}</td></tr>
    <tr><td>Total tokens</td><td>{{ t.rates.tokens_total.per_day | default: "—" }}</td><td>{{ t.rates.tokens_total.per_week | default: "—" }}</td><td>{{ t.rates.tokens_total.per_month | default: "—" }}</td></tr>
  </tbody>
</table>
</div>

<h2>Per-day metrics</h2>
<p class="chart-note">Zero baseline. Drag to pan, scroll sideways, Ctrl/⌘ + wheel zooms, click a bar to open that day's brief. {{ d.days | size }} days available.</p>
{% include chart.html metric="posted" label="Brief entries posted" %}
{% include chart.html metric="posted_en" label="Entries posted (EN)" %}
{% include chart.html metric="posted_fa" label="Entries posted (FA)" %}
{% include chart.html metric="processed" label="Links processed" %}
{% include chart.html metric="included" label="Links included" %}
{% include chart.html metric="links" label="Source links cited" %}
{% include chart.html metric="new_stories" label="New stories" %}
{% include chart.html metric="continuations" label="Continuations (repeats, excluded)" %}
{% include chart.html metric="developments" label="Developments (updates)" %}
{% include chart.html metric="biases" label="Biases found" %}
{% include chart.html metric="emails_total" label="Emails sent" %}
{% include chart.html metric="deliveries" label="Recipient deliveries" %}
{% include chart.html metric="tokens_input" label="Input tokens" %}
{% include chart.html metric="tokens_output" label="Output tokens" %}
{% include chart.html metric="tokens_thinking" label="Thinking tokens" %}
{% include chart.html metric="tokens_total" label="Total tokens" %}

<h2>Daily status</h2>
<div class="table-wrap">
<table class="dash-table">
  <thead>
    <tr>
      <th>Date</th><th>Status</th><th>EN</th><th>FA</th>
      <th>Posted</th><th>Links</th><th>Email</th><th>Subs</th><th>Failures</th>
    </tr>
  </thead>
  <tbody>
    {% assign rows = d.days | reverse %}
    {% for day in rows %}
    <tr>
      <td>{{ day.date }}</td>
      <td><span class="badge badge-{{ day.status | default: 'unknown' }}">{{ day.status | default: "unknown" }}</span></td>
      <td>{% if day.en %}yes{% else %}—{% endif %}</td>
      <td>{% if day.fa %}yes{% else %}—{% endif %}</td>
      <td>{{ day.posted | default: 0 }}</td>
      <td>{{ day.links | default: 0 }}</td>
      <td>{% if day.emails.en.sent %}EN{% endif %}{% if day.emails.fa.sent %} FA{% endif %}{% unless day.emails.en.sent or day.emails.fa.sent %}—{% endunless %}</td>
      <td>{{ day.subscribers | default: "—" }}</td>
      <td>{{ day.failed_stages | join: ", " | default: "—" }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
</div>

<p class="chart-note">
  <strong>success</strong>: all critical stages passed. <strong>degraded</strong>: a non-critical stage failed or editorial shipped an unedited draft. <strong>failed</strong>: a critical stage failed. <strong>unknown</strong>: historical day before status tracking began.
</p>

<script id="dashboard-days" type="application/json">{{ d.days | jsonify }}</script>
<span id="dashboard-base" data-base="{{ '/' | relative_url }}" hidden></span>
<script src="{{ '/assets/js/dashboard.js' | relative_url }}" defer></script>

</div>
