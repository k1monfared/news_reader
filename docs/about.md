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
- **Reuters** (English, RSS)
- **France 24** (English, RSS)
- **Euronews** (English, RSS)

**How it works:**

The pipeline runs daily, triggered by a cron job at 8 AM Vancouver time. It first checks for any missed dates since the last successful run and backfills those before running today's digest.

Each run gets its own isolated directory. The pipeline flows through ten stages, each reading the previous stage's output and writing its own.

The fetch stage pulls raw content from the four sources listed above, all through RSS feeds. If a source is down, the pipeline logs it and continues with the others. It only aborts if every source fails.

The same event often appears across multiple sources. The dedup stage builds TF-IDF vectors from each item's English title and text, then computes pairwise cosine similarity. Items above a similarity threshold get clustered together. The longest item in each cluster becomes the "primary," and the others are marked as related sources. This preserves multi-source corroboration while avoiding repetition.

Primary items go through an LLM call that judges relevance to the conflict. Each item gets an included/excluded decision, a confidence score, and a reason. Non-primary items inherit their cluster's decision. The filter also flags sole-source items with low confidence. Results are cached so re-runs don't waste LLM calls on already-seen articles.

All included items get categorized into topic buckets: Military Operations, Inside Iran, US Policy, Israel Policy, Diplomacy, Regional Actors, International, Economy, or Other. If the LLM suggests a category not in the list, it gets logged for future consideration.

Development tracking loads items from the past seven days and computes similarity against today's items. When a match is found, the LLM classifies the relationship: "new" (no prior coverage), "continuation" (same story, no new info), or "development" (same story with new information). Continuations get excluded from the report to avoid repetition. Developments get a timeline showing how the story evolved over previous days.

The included, tracked items get organized by bucket and formatted into the daily brief. Buckets with more or higher-confidence items appear first. Empty buckets are omitted. An LLM pass reviews the draft for editorial quality, runs bias detection comparing how different sources framed the same events, and flags any remaining sole-source claims that need caveats. The verify stage checks that all article URLs in the report are still live. Dead links get flagged. The final stage generates a blog post and publishes it to this site.

Every LLM call and HTTP request is logged to an audit trail with full input/output, token counts, and cost tracking. Each run stays within a configurable budget cap.

**Limitations:**
- This is an automated system. AI can misclassify or miss nuance.
- Source availability varies. Check the header of each report for any sources that were unreachable.

---

# Bias Handling

Every news source carries biases in how it frames, emphasizes, and omits information. This pipeline tracks those biases and uses them to produce more balanced reporting. Debias notes feed into the summarizer, automated detection runs in the editorial stage, and only confirmed patterns inform the output.

See the full list of detected patterns per source, with examples and counteractions, on the [Bias Handling page]({{ '/bias/' | relative_url }}).

---

# What it costs to run

Hosting, email, and the code itself are on free tiers (GitHub Pages, Cloudflare, Resend). The LLM calls are the only real expense. Measured across 40 runs from February to April 2026:

- **Median day:** about **$0.61** (16 LLM calls, roughly 127k input tokens and 15k output)
- **Busy day:** up to **$1.00**
- **Monthly:** around **$18** at the median, up to **$30** at the top of the range
- **Yearly:** around **$220**, up to **$365**

Two stages eat most of the bill: `editorial` (one long-context review of the full brief) and, on busy news days, `track_developments` (one LLM call per story pair checked against the last seven days).

Full per-stage breakdown and methodology: [COSTS.md on GitHub](https://github.com/k1monfared/news_reader/blob/master/COSTS.md).

# Support this project

If you find the daily brief useful, you can help cover the LLM bill.

**[Sponsor this project]({{ site.sponsor_url }})**

---

**Source code:** [github.com/k1monfared/news_reader](https://github.com/k1monfared/news_reader)

</div>
