---
layout: default
title: "Cost & usage"
permalink: /dashboard/cost/
---
{% assign d = site.data.dashboard %}{% assign t = d.totals %}{% assign today = d.today %}

<div class="page-content dashboard" markdown="0">

<h1>Cost &amp; usage</h1>
{% include dashnav.html %}

<h2>Today's cost</h2>
{% if today %}
{% assign run_cost = today.cost.total | default: today.cost_usd %}
<p class="chart-note">
  Run {{ today.target_date | default: "—" }} cost
  {% if run_cost %}${{ run_cost | round: 4 }}{% else %}$0.0000{% endif %}
  ({{ today.tokens.total.calls | default: 0 }} LLM call(s)).
</p>
{% else %}
<p class="chart-note">No run recorded yet.</p>
{% endif %}

{% if today.cost.by_stage and today.cost.by_stage.size > 0 %}
<h3>Cost by stage</h3>
<div class="table-wrap">
<table class="dash-table">
  <thead><tr><th>Stage</th><th>Cost (USD)</th></tr></thead>
  <tbody>
  {% for pair in today.cost.by_stage %}
    <tr><td>{{ pair[0] | replace: "_", " " }}</td><td>${{ pair[1] | default: 0 | round: 4 }}</td></tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% else %}
<p class="chart-note">No per-stage cost recorded for today's run.</p>
{% endif %}

{% if today.cost.by_model and today.cost.by_model.size > 0 %}
<h3>Cost by model</h3>
<div class="table-wrap">
<table class="dash-table">
  <thead><tr><th>Model</th><th>Cost (USD)</th></tr></thead>
  <tbody>
  {% for pair in today.cost.by_model %}
    <tr><td>{{ pair[0] }}</td><td>${{ pair[1] | default: 0 | round: 4 }}</td></tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% else %}
<p class="chart-note">No per-model cost recorded for today's run.</p>
{% endif %}
<p class="chart-note">The by-model table can sum below the run total when a call has no model recorded.</p>

<h2>Cost per day</h2>
{% include chart.html metric="cost_usd" label="LLM cost (USD)" %}

<h2>Tokens per day</h2>
{% include chart.html metric="tokens_input" label="Input tokens" %}
{% include chart.html metric="tokens_output" label="Output tokens" %}
{% include chart.html metric="tokens_thinking" label="Thinking tokens" %}
{% include chart.html metric="tokens_total" label="Total tokens" %}

<h2>All-time averages</h2>
{% assign rates = t.rates %}
<p class="chart-note">Averages since {{ t.first_token_date | default: "—" }} ({{ t.days_with_tokens | default: 0 }} day(s) with token data). Estimated totals extrapolate the per-day average across {{ t.days_running | default: 0 }} running day(s).</p>
<div class="table-wrap">
<table class="dash-table">
  <thead><tr><th>Metric</th><th>Per day</th><th>Per week</th><th>Per month</th><th>Days</th><th>Estimated total</th></tr></thead>
  <tbody>
    <tr>
      <td>Cost (USD)</td>
      <td>${{ rates.cost_usd.per_day | default: "—" }}</td>
      <td>${{ rates.cost_usd.per_week | default: "—" }}</td>
      <td>${{ rates.cost_usd.per_month | default: "—" }}</td>
      <td>{{ rates.cost_usd.days | default: "—" }}</td>
      <td>${{ rates.cost_usd.estimated_total | default: "—" }}</td>
    </tr>
    <tr>
      <td>Input tokens</td>
      <td>{{ rates.tokens_input.per_day | default: "—" }}</td>
      <td>{{ rates.tokens_input.per_week | default: "—" }}</td>
      <td>{{ rates.tokens_input.per_month | default: "—" }}</td>
      <td>{{ rates.tokens_input.days | default: "—" }}</td>
      <td>{{ rates.tokens_input.estimated_total | default: "—" }}</td>
    </tr>
    <tr>
      <td>Output tokens</td>
      <td>{{ rates.tokens_output.per_day | default: "—" }}</td>
      <td>{{ rates.tokens_output.per_week | default: "—" }}</td>
      <td>{{ rates.tokens_output.per_month | default: "—" }}</td>
      <td>{{ rates.tokens_output.days | default: "—" }}</td>
      <td>{{ rates.tokens_output.estimated_total | default: "—" }}</td>
    </tr>
    <tr>
      <td>Thinking tokens</td>
      <td>{{ rates.tokens_thinking.per_day | default: "—" }}</td>
      <td>{{ rates.tokens_thinking.per_week | default: "—" }}</td>
      <td>{{ rates.tokens_thinking.per_month | default: "—" }}</td>
      <td>{{ rates.tokens_thinking.days | default: "—" }}</td>
      <td>{{ rates.tokens_thinking.estimated_total | default: "—" }}</td>
    </tr>
    <tr>
      <td>Total tokens</td>
      <td>{{ rates.tokens_total.per_day | default: "—" }}</td>
      <td>{{ rates.tokens_total.per_week | default: "—" }}</td>
      <td>{{ rates.tokens_total.per_month | default: "—" }}</td>
      <td>{{ rates.tokens_total.days | default: "—" }}</td>
      <td>{{ rates.tokens_total.estimated_total | default: "—" }}</td>
    </tr>
  </tbody>
</table>
</div>

<h2>Projection</h2>
<p class="chart-note">
  Recorded cost {% if t.cost_actual %}${{ t.cost_actual | round: 2 }}{% else %}$0.00{% endif %}
  over {{ t.days_with_cost | default: 0 }} day(s) with recorded LLM cost,
  averaging ${{ t.cost_per_day_over_cost_days | default: 0 | round: 4 }}/day.
  Projected {% if t.cost_projected %}${{ t.cost_projected | round: 2 }}{% else %}$0.00{% endif %}
  across {{ t.days_running | default: 0 }} running day(s)
  plus {{ t.cost_extra_runs | default: 0 }} extra run(s).
</p>

<script id="dashboard-days" type="application/json">{{ d.days | jsonify }}</script>
<span id="dashboard-base" data-base="{{ '/' | relative_url }}" hidden></span>
<script src="{{ '/assets/js/dashboard.js' | relative_url }}" defer></script>

</div>
