---
layout: default
lang: en
---

<div class="index-description">
  <p>{{ site.data.i18n.en.site_description }}</p>
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
