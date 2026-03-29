---
layout: default
title: Iran Conflict Daily Brief
---

<div class="index-description" markdown="1">

Automated daily intelligence digest covering military operations, diplomacy, economic impacts, and regional developments in the Iran conflict. Updated every morning.

Sources: Al Jazeera, Iran International, Reuters.

</div>

<ul class="post-list">
{% assign sorted_posts = site.posts | sort: "date" | reverse %}
{% for post in sorted_posts %}
  <li>
    <span class="post-list-date">{{ post.date | date: "%Y-%m-%d" }}</span>
    <span class="post-list-title"><a href="{{ post.url | relative_url }}">{{ post.title }}</a></span>
  </li>
{% endfor %}
</ul>
