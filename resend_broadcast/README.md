# resend_broadcast

A minimal, project-agnostic Python wrapper around Resend's Broadcasts API. Given an HTML body, subject, from address, and audience ID, creates a broadcast and sends it.

Two HTTP calls, no dependencies beyond `httpx`.

## Install

Copy the `resend_broadcast/` directory into any Python project. It works as a package with `from resend_broadcast import send_broadcast`, or as a CLI with `python -m resend_broadcast ...`.

Requires `httpx>=0.27`.

## Library use

```python
from resend_broadcast import send_broadcast

result = send_broadcast(
    api_key=os.environ["RESEND_API_KEY"],
    audience_id="aud_xxx",
    from_addr="Brief <brief@example.com>",
    subject="Daily Brief: April 16, 2026",
    html="<h1>...</h1>",
    reply_to="you@example.com",      # optional
    text="Plain-text alt",           # optional; Resend auto-generates if omitted
    name="daily-brief-2026-04-16",   # optional, internal label
)
# result: {"broadcast_id": "bcst_xxx", "status": "sent"}
```

Raises `BroadcastError` on any non-2xx response from Resend.

## CLI use

```
python -m resend_broadcast \
    --audience-id aud_xxx \
    --from "Brief <brief@example.com>" \
    --subject "Daily Brief: April 16, 2026" \
    --html-file out.html
```

Reads `RESEND_API_KEY` from the environment. Prints `{"broadcast_id": ..., "status": "sent"}` on success.

## What it does not do

- Does not manage subscribers. Use Resend's Audiences dashboard, or the [subscribe-proxy](../subscribe-proxy/) Worker.
- Does not generate HTML. Pass in HTML your caller already built.
- Does not handle unsubscribe. Resend does this automatically via the `List-Unsubscribe` header and a hosted landing page. To include an unsubscribe link in the body, put the literal string `{{{RESEND_UNSUBSCRIBE_URL}}}` somewhere in your HTML and Resend substitutes it per recipient.
