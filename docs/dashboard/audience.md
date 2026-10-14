---
layout: default
title: Audience
permalink: /dashboard/audience/
---
{% assign d = site.data.dashboard %}{% assign t = d.totals %}

<div class="page-content dashboard" markdown="0">

<h1>Audience</h1>
{% include dashnav.html %}

<section class="cards">
  <div class="card">
    <span class="card-value">{{ t.subscribers_current | default: "—" }}</span>
    <span class="card-label">Subscribers now</span>
    <span class="card-sub">peak {{ t.subscribers_peak | default: "—" }}</span>
  </div>
  <div class="card">
    <span class="card-value">{{ t.subscribers_total_ever | default: "—" }}</span>
    <span class="card-label">Proxy distinct emails</span>
  </div>
  <div class="card">
    <span class="card-value">{{ t.emails_en | default: 0 }}</span>
    <span class="card-label">Emails sent (EN)</span>
  </div>
  <div class="card">
    <span class="card-value">{{ t.emails_fa | default: 0 }}</span>
    <span class="card-label">Emails sent (FA)</span>
  </div>
  <div class="card">
    <span class="card-value">{{ t.deliveries | default: "—" }}</span>
    <span class="card-label">Deliveries</span>
  </div>
</section>

<h2>Subscribers</h2>
{% assign sub_day_count = 0 %}
{% for day in d.days %}
  {% if day.subscribers != nil %}{% assign sub_day_count = sub_day_count | plus: 1 %}{% endif %}
{% endfor %}
{% if sub_day_count > 0 %}
{% include chart.html metric="subscribers" label="Subscribers" %}
{% else %}
<p class="chart-note">No per-day subscriber values recorded yet.</p>
{% endif %}

<script id="dashboard-days" type="application/json">{{ d.days | jsonify }}</script>
<span id="dashboard-base" data-base="{{ '/' | relative_url }}" hidden></span>
<script src="{{ '/assets/js/dashboard.js' | relative_url }}" defer></script>

</div>
