---
layout: default
---

<div class="index-description" markdown="1">

Automated daily intelligence digest covering the US-Israel war on Iran. Tracks military operations, diplomacy, economic impacts, and regional developments from multiple sources with different perspectives.

</div>

<ul class="post-list">
{% assign sorted_posts = site.posts | sort: "date" | reverse %}
{% for post in sorted_posts %}
  <li>
    <a href="{{ post.url | relative_url }}" class="post-list-link">
      <span class="post-list-date">{{ post.date | date: "%B %d, %Y" }}</span>
    </a>
  </li>
{% endfor %}
</ul>
