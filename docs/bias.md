---
layout: default
title: Bias Handling
permalink: /bias/
---

<div class="page-content" markdown="1">

# Bias Handling

Every news source carries biases in how it frames, emphasizes, and omits information. This pipeline tracks those biases and uses them to produce more balanced reporting.

**How bias handling works:**
- Debias notes for each source are fed to the summarizer, prompting it to add caveats and flag editorial framing.
- During the editorial stage, automated detection compares how different sources covered the same events, flagging new framing patterns.
- Suggested biases are logged for review before being confirmed. Only confirmed patterns inform the pipeline's output.

{% for source in site.data.source_biases %}
{% assign key = source[0] %}
{% assign info = source[1] %}

<details class="bias-source-section">
<summary><h3 class="bias-source-heading">{{ info.display_name }} <span class="bias-count">({{ info.biases | size }} patterns)</span></h3></summary>

{% if info.notes %}_{{ info.notes }}_{% endif %}

<table class="bias-table">
  <thead>
    <tr>
      <th>Pattern</th>
      <th>How we counteract it</th>
      <th>Added</th>
      <th>Status</th>
    </tr>
  </thead>
  <tbody>
    {% for bias in info.biases %}
    <tr>
      <td>
        <strong>{{ bias.pattern }}</strong>
        <details class="bias-foldable">
          <summary>Details ({{ bias.detail_items | size }})</summary>
          <ul class="bias-detail-list">
            {% for item in bias.detail_items %}
            <li>{{ item }}</li>
            {% endfor %}
          </ul>
        </details>
        {% if bias.example_text %}
        <details class="bias-foldable">
          <summary>Example</summary>
          <blockquote>{{ bias.example_text }}</blockquote>
          {% if bias.example_url %}<a href="{{ bias.example_url }}" target="_blank" rel="noopener">Source article</a>{% endif %}
        </details>
        {% endif %}
      </td>
      <td>
        <details class="bias-foldable">
          <summary>Counteraction</summary>
          {{ bias.debias }}
        </details>
        {% if bias.unbiased_text %}
        <details class="bias-foldable">
          <summary>Unbiased version</summary>
          <blockquote>{{ bias.unbiased_text }}</blockquote>
        </details>
        {% endif %}
      </td>
      <td class="bias-date">{{ bias.date_added }}</td>
      <td><span class="bias-status bias-status--{{ bias.status }}">{{ bias.status }}</span></td>
    </tr>
    {% endfor %}
  </tbody>
</table>

</details>

{% endfor %}

</div>
