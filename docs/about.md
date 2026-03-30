---
layout: default
title: About
permalink: /about/
---

<div class="page-content" markdown="1">

# About

This is an automated daily intelligence digest covering the US-Israel war on Iran. The pipeline fetches news from multiple sources with different perspectives, deduplicates events, filters for importance, categorizes by topic, and produces a concise brief.

**Sources:**
- **Al Jazeera** (English, RSS)
- **Iran International** (Farsi, web scrape)
- **Reuters** (English, RSS)
- **France 24** (English, RSS)
- **Euronews** (English, RSS)

**How it works:**
- Items are deduplicated across sources using text similarity
- Each item is evaluated for importance by an AI editor
- Single-source claims are flagged as unconfirmed
- Contradictory claims across sources are merged with caveats
- All source URLs are verified before publication

**Limitations:**
- This is an automated system. AI can misclassify or miss nuance.
- Farsi content is machine-translated.
- Source availability varies. Check the header of each report for any sources that were unreachable.

---

# Bias Handling

Every news source carries biases in how it frames, emphasizes, and omits information. This pipeline tracks those biases and uses them to produce more balanced reporting.

**How bias handling works:**
- Debias notes for each source are fed to the summarizer, prompting it to add caveats and flag editorial framing.
- During the editorial stage, automated detection compares how different sources covered the same events, flagging new framing patterns.
- Suggested biases are logged for review before being confirmed. Only confirmed patterns inform the pipeline's output.

{% for source in site.data.source_biases %}
{% assign key = source[0] %}
{% assign info = source[1] %}

### {{ info.display_name }}

{% if info.notes %}_{{ info.notes }}_{% endif %}

<table class="bias-table">
  <thead>
    <tr>
      <th>Pattern</th>
      <th>How we counteract it</th>
      <th>Status</th>
    </tr>
  </thead>
  <tbody>
    {% for bias in info.biases %}
    <tr>
      <td><strong>{{ bias.pattern }}</strong><br><span class="bias-detail">{{ bias.detail }}</span></td>
      <td>{{ bias.debias }}</td>
      <td><span class="bias-status bias-status--{{ bias.status }}">{{ bias.status }}</span></td>
    </tr>
    {% endfor %}
  </tbody>
</table>

{% endfor %}

---

**Source code:** [github.com/k1monfared/news_reader](https://github.com/k1monfared/news_reader)

</div>
