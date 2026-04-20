---
layout: default
title: Subscribe
permalink: /subscribe/
---

<div class="page-content" markdown="1">

# Subscribe

Get each daily brief delivered to your inbox. One email per day, no tracking, unsubscribe with a single click at the bottom of any email.

{% if site.subscribe_proxy_url and site.subscribe_proxy_url != "" %}
<form action="{{ site.subscribe_proxy_url }}" method="POST" class="subscribe-form">
  <input type="hidden" name="list" value="en">
  <label for="subscribe-email">Email address</label>
  <input id="subscribe-email" type="email" name="email" required
         autocomplete="email" placeholder="you@example.com">
  <button type="submit">Subscribe</button>
</form>

{% if page.url contains "err=1" %}
<p class="subscribe-error">Something went wrong. Try again, or email the admin.</p>
{% endif %}

**What happens when you subscribe:**
- Your email is added to the sender's audience. It is never written to this GitHub repository.
- You will get one email per day with the full brief, usually by 9 AM Pacific.
- Each email includes an "Unsubscribe" link. Clicking it removes you immediately, no confirmation page or second click required.

**Prefer RSS?** The [feed]({{ "/feed.xml" | relative_url }}) carries the same content.

{% else %}
The subscribe form is not yet configured on this site. Check back soon.
{% endif %}

</div>
