---
layout: default
lang: fa
title: "مدیریت سوگیری"
permalink: /fa/bias/
---

<div class="page-content" markdown="1">

# مدیریت سوگیری

هر منبع خبری در نحوه قاب‌بندی، تأکید و حذف اطلاعات سوگیری دارد. این خط لوله این سوگیری‌ها را ردیابی می‌کند و از آن‌ها برای تولید گزارش متوازن‌تر استفاده می‌کند.

**نحوه مدیریت سوگیری:**
- یادداشت‌های رفع سوگیری برای هر منبع به خلاصه‌ساز داده می‌شود و از آن خواسته می‌شود احتیاط‌ها را اضافه کند و قاب‌بندی ویراستاری را پرچم‌گذاری کند.
- در مرحله ویرایشی، تشخیص خودکار مقایسه می‌کند که چگونه منابع مختلف همان رویدادها را پوشش داده‌اند و الگوهای قاب‌بندی جدید را پرچم‌گذاری می‌کند.
- سوگیری‌های پیشنهادی قبل از تأیید برای بازبینی ثبت می‌شوند. فقط الگوهای تأیید شده بر خروجی خط لوله اثر می‌گذارند.

{% for source in site.data.source_biases_fa %}
{% assign key = source[0] %}
{% assign info = source[1] %}

<details class="bias-source-section">
<summary><h3 class="bias-source-heading">{{ info.display_name }} <span class="bias-count">({{ info.biases | size }} الگو)</span></h3></summary>

{% if info.notes %}_{{ info.notes }}_{% endif %}

<table class="bias-table">
  <thead>
    <tr>
      <th>الگو</th>
      <th>چگونه آن را خنثی می‌کنیم</th>
      <th>اضافه شده در</th>
      <th>وضعیت</th>
    </tr>
  </thead>
  <tbody>
    {% for bias in info.biases %}
    <tr>
      <td>
        <strong>{{ bias.pattern }}</strong>
        <details class="bias-foldable">
          <summary>جزئیات ({{ bias.detail_items | size }})</summary>
          <ul class="bias-detail-list">
            {% for item in bias.detail_items %}
            <li>{{ item }}</li>
            {% endfor %}
          </ul>
        </details>
        {% if bias.example_text %}
        <details class="bias-foldable">
          <summary>نمونه</summary>
          <blockquote>{{ bias.example_text }}</blockquote>
          {% if bias.example_url %}<a href="{{ bias.example_url }}" target="_blank" rel="noopener">مقاله منبع</a>{% endif %}
        </details>
        {% endif %}
      </td>
      <td>
        <details class="bias-foldable">
          <summary>خنثی‌سازی</summary>
          {{ bias.debias }}
        </details>
        {% if bias.unbiased_text %}
        <details class="bias-foldable">
          <summary>نسخه بی‌طرفانه</summary>
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
