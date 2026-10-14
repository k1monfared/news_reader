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
{% include dashnav.html %}

<p class="chart-note">Automated status and counts for this brief, rebuilt once per day after the run. Cards mix English-only and bilingual counts; each label says which. Last updated {{ t.generated_at | default: "unknown" }}.</p>

{% assign rs = site.data.run_status %}
{% if rs and rs.status == "running" %}
<div class="run-live">
  <strong>Run in progress</strong> — {{ rs.target_date }} ({{ rs.run_id }}). Current stage: <strong>{{ rs.current_stage | default: "starting" }}</strong>.
  <ol class="funnel">
    {% for stage in rs.stages %}
    <li class="funnel-step funnel-{{ stage.status }}">
      <span class="funnel-name">{{ stage.name | replace: "_", " " }}</span>
      <span class="funnel-status">{{ stage.status }}</span>
      {% if stage.duration_s != nil %}<span class="funnel-dur">{{ stage.duration_s }}s</span>{% endif %}
      {% if stage.error %}<span class="funnel-err">{{ stage.error | truncate: 100 }}</span>{% endif %}
    </li>
    {% endfor %}
  </ol>
  <p class="chart-note">Updated {{ rs.updated_at }}. This view refreshes on the next status push.</p>
</div>
{% endif %}

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
    <span class="card-label">Entries posted (EN)</span>
    <span class="card-sub">{{ today.posted_fa | default: 0 }} FA</span>
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
  <div class="card">
    <span class="card-value">{% if today.cost_usd %}${{ today.cost_usd | round: 2 }}{% else %}$0.00{% endif %}</span>
    <span class="card-label">Cost this run</span>
    <span class="card-sub">flat peak rates</span>
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
{% else %}
<p class="chart-note">No run recorded yet. The next pipeline run will populate today's status and funnel.</p>
{% endif %}

<h2>All time</h2>
<section class="cards">
  <div class="card">
    <span class="card-value">{{ t.briefs_en | default: 0 }}</span>
    <span class="card-label">Briefs published (EN)</span>
    <span class="card-sub">{{ t.days_running | default: 0 }} days running, {{ t.days_running | minus: t.briefs_en }} without a brief</span>
  </div>
  <div class="card">
    <span class="card-value">{% if t.cost_actual %}${{ t.cost_actual | round: 2 }}{% else %}$0.00{% endif %}</span>
    <span class="card-label">LLM cost (recorded)</span>
    <span class="card-sub">{% if t.cost_projected %}${{ t.cost_projected | round: 2 }}{% else %}$0.00{% endif %} projected over {{ t.days_running | plus: t.cost_extra_runs }} run-days</span>
  </div>
  <div class="card">
    <span class="card-value">{{ t.days_failed_ever | default: 0 }}</span>
    <span class="card-label">Failed days (ever)</span>
    <span class="card-sub">{{ t.days_repaired | default: 0 }} repaired, {{ t.days_degraded | default: 0 }} degraded</span>
  </div>
  <div class="card">
    <span class="card-value">{{ t.subscribers_current | default: "—" }}</span>
    <span class="card-label">Subscribers now</span>
    <span class="card-sub">peak {{ t.subscribers_peak | default: "—" }}</span>
  </div>
</section>

<h2>Source health</h2>
{% if today and today.sources and today.sources.size > 0 %}
<p class="chart-note">
  Per-source funnel for today's run only (no trend across days):
  {% for src in today.sources %}<strong>{{ src.name }}</strong>: {{ src.fetched | default: 0 }} fetched, {% if src.prefilter_passed == nil %}prefilter not recorded{% else %}{{ src.prefilter_passed }} passed prefilter{% endif %}, {{ src.included | default: 0 }} included{% unless forloop.last %} &middot; {% endunless %}{% endfor %}
</p>
{% assign flagged_sources = "" %}
{% for src in today.sources %}
{% if src.fetched > 0 %}
{% if src.prefilter_passed == nil or src.prefilter_passed == 0 %}
{% assign flagged_sources = flagged_sources | append: src.name | append: " " %}
{% endif %}
{% endif %}
{% endfor %}
{% if flagged_sources != "" %}
<div class="sources-down"><strong>Source health flag:</strong> {{ flagged_sources }}fetched items but recorded no prefilter pass. Check the fetchers and the prefilter.</div>
{% endif %}
{% else %}
<p class="chart-note">No per-source funnel recorded for today's run.</p>
{% endif %}

<h2>Sections</h2>
<div class="cards">
  <a class="card" href="{{ '/dashboard/cost/' | relative_url }}"><span class="card-label">Cost &amp; usage</span><span class="card-sub">spend, tokens, projections</span></a>
  <a class="card" href="{{ '/dashboard/reliability/' | relative_url }}"><span class="card-label">Reliability</span><span class="card-sub">status, failures, repairs</span></a>
  <a class="card" href="{{ '/dashboard/content/' | relative_url }}"><span class="card-label">Content</span><span class="card-sub">entries, links, story mix</span></a>
  <a class="card" href="{{ '/dashboard/sources/' | relative_url }}"><span class="card-label">Sources &amp; models</span><span class="card-sub">funnel, models, failover</span></a>
  <a class="card" href="{{ '/dashboard/audience/' | relative_url }}"><span class="card-label">Audience</span><span class="card-sub">subscribers, deliveries</span></a>
</div>

<script id="dashboard-days" type="application/json">{{ d.days | jsonify }}</script>
<span id="dashboard-base" data-base="{{ '/' | relative_url }}" hidden></span>
<script src="{{ '/assets/js/dashboard.js' | relative_url }}" defer></script>

</div>
