---
layout: default
title: Dashboard
permalink: /dashboard/
---

{% assign d = site.data.dashboard %}
{% assign t = d.totals %}

<div class="page-content dashboard" markdown="0">

<h1>Dashboard</h1>

<p class="chart-note">Automated status and counts for this brief, rebuilt once per day after the run. Counts cover both languages. Last updated {{ t.generated_at | default: "unknown" }}.</p>

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
    <span class="card-value">{{ t.days_failed | default: 0 }}</span>
    <span class="card-label">Failed days</span>
    <span class="card-sub">{{ t.days_degraded | default: 0 }} degraded · {{ t.days_empty | default: 0 }} empty</span>
  </div>
</section>

{% assign recent = d.days | slice: -30, 30 %}
{% assign max_posted = 1 %}
{% for day in recent %}{% if day.posted and day.posted > max_posted %}{% assign max_posted = day.posted %}{% endif %}{% endfor %}

<h2>Brief entries per day</h2>
<p class="chart-note">Last {{ recent | size }} days, zero baseline. Tallest bar is {{ max_posted }} entries.</p>
<div class="bars" role="img" aria-label="Brief entries per day over the last {{ recent | size }} days">
  {% for day in recent %}
  {% assign h = day.posted | times: 100.0 | divided_by: max_posted %}
  <div class="bar-col" title="{{ day.date }}: {{ day.posted }} entries">
    <div class="bar" style="height: {{ h }}%"></div>
  </div>
  {% endfor %}
</div>

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

</div>
