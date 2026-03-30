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

The pipeline runs daily, triggered by a cron job at 8 AM Vancouver time. It first checks for any missed dates since the last successful run and backfills those before running today's digest.

Each run gets its own isolated directory. The pipeline flows through ten stages, each reading the previous stage's output and writing its own.

The fetch stage pulls raw content from the five sources listed above. Al Jazeera, Reuters, France 24, and Euronews come through RSS feeds. Iran International gets scraped directly from its Farsi website, since it has no usable RSS feed. If a source is down, the pipeline logs it and continues with the others. It only aborts if every source fails.

English items pass through untouched. Farsi items from Iran International get batched and sent to an LLM for translation. Both the title and body text are translated to English. If translation fails for a batch, the original text is kept as a fallback.

The same event often appears across multiple sources. The dedup stage builds TF-IDF vectors from each item's English title and text, then computes pairwise cosine similarity. Items above a similarity threshold get clustered together. The longest item in each cluster becomes the "primary," and the others are marked as related sources. This preserves multi-source corroboration while avoiding repetition.

Primary items go through an LLM call that judges relevance to the conflict. Each item gets an included/excluded decision, a confidence score, and a reason. Non-primary items inherit their cluster's decision. The filter also flags sole-source items with low confidence. Results are cached so re-runs don't waste LLM calls on already-seen articles.

All included items get categorized into topic buckets: Military Operations, Inside Iran, US Policy, Israel Policy, Diplomacy, Regional Actors, International, Economy, or Other. If the LLM suggests a category not in the list, it gets logged for future consideration.

Development tracking loads items from the past seven days and computes similarity against today's items. When a match is found, the LLM classifies the relationship: "new" (no prior coverage), "continuation" (same story, no new info), or "development" (same story with new information). Continuations get excluded from the report to avoid repetition. Developments get a timeline showing how the story evolved over previous days.

The included, tracked items get organized by bucket and formatted into the daily brief. Buckets with more or higher-confidence items appear first. Empty buckets are omitted. An LLM pass reviews the draft for editorial quality, runs bias detection comparing how different sources framed the same events, and flags any remaining sole-source claims that need caveats. The verify stage checks that all article URLs in the report are still live. Dead links get flagged. The final stage generates a blog post and publishes it to this site.

Every LLM call and HTTP request is logged to an audit trail with full input/output, token counts, and cost tracking. Each run stays within a configurable budget cap.

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
      <th>Added</th>
      <th>Status</th>
    </tr>
  </thead>
  <tbody>
    {% for bias in info.biases %}
    <tr>
      <td><strong>{{ bias.pattern }}</strong><br><span class="bias-detail">{{ bias.detail }}</span></td>
      <td>{{ bias.debias }}</td>
      <td class="bias-date">{{ bias.date_added }}</td>
      <td><span class="bias-status bias-status--{{ bias.status }}">{{ bias.status }}</span></td>
    </tr>
    {% endfor %}
  </tbody>
</table>

{% endfor %}

---

**Source code:** [github.com/k1monfared/news_reader](https://github.com/k1monfared/news_reader)

</div>
