---
layout: default
lang: fa
title: "اشتراک"
permalink: /fa/subscribe/
---

<div class="page-content" markdown="1">

# اشتراک در گزارش روزانه

گزارش روزانه را در ایمیل خود دریافت کنید. روزی یک ایمیل، بدون ردیابی، با یک کلیک در پایین هر ایمیل می‌توانید لغو اشتراک کنید.

{% if site.subscribe_proxy_url and site.subscribe_proxy_url != "" %}
<form action="{{ site.subscribe_proxy_url }}" method="POST" class="subscribe-form">
  <input type="hidden" name="list" value="fa">
  <label for="subscribe-email">آدرس ایمیل</label>
  <input id="subscribe-email" type="email" name="email" required
         autocomplete="email" placeholder="you@example.com" dir="ltr">
  <button type="submit">اشتراک</button>
</form>

{% if page.url contains "err=1" %}
<p class="subscribe-error">خطایی رخ داد. دوباره تلاش کنید.</p>
{% endif %}

**وقتی فرم را ارسال می‌کنید چه اتفاقی می‌افتد:**
- یک ایمیل تأیید یک‌بار برای شما ارسال می‌شود. برای تکمیل اشتراک، روی لینک داخل آن کلیک کنید. اگر کلیک نکنید، هیچ اتفاق دیگری نمی‌افتد.
- پس از تأیید، روزی یک ایمیل حاوی گزارش کامل، معمولاً تا ساعت ۹ صبح به وقت اقیانوس آرام، دریافت خواهید کرد.
- ایمیل شما در سرویس ارسال‌کننده ذخیره می‌شود، نه در مخزن گیت‌هاب این سایت.
- در هر ایمیل یک لینک «لغو اشتراک» وجود دارد. با کلیک روی آن به یک صفحه ساده با یک دکمه «لغو اشتراک» می‌روید. یک کلیک دیگر روی آن دکمه و از فهرست خارج می‌شوید. بدون فرم، بدون حساب کاربری، بدون اطلاعات اضافه.

{% else %}
فرم اشتراک هنوز پیکربندی نشده است. به‌زودی مراجعه کنید.
{% endif %}

</div>
