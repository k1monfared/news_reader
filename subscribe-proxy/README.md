# subscribe-proxy

A generic Cloudflare Worker that implements **double-opt-in subscribe** for a static site backed by [Resend](https://resend.com). The Worker holds the privileged Resend API key and a KV namespace for pending confirmation tokens, so your static site can stay fully static while still protecting against bad-actor subscribe spam.

Reusable across projects. Nothing in the Worker code references a specific site, audience, or list.

## Why this exists

Static sites cannot safely hold API keys. Resend's API also auto-confirms contacts on add, which means a naive subscribe proxy can be used to spam arbitrary addresses. This Worker fixes both: it holds the API key server-side and inserts a confirmation step so only people who click a link actually join the list.

## Flow

1. **POST /** with `email` (and optional `list=fa` to route to a non-default segment). Worker generates a random token, stores `{token, email, list}` in KV with a 24h TTL, rate-limits the address so resubmissions within 24h do not trigger another email, sends a confirmation email via Resend, redirects to `CONFIRMATION_SENT_URL`.
2. **GET /confirm?token=...** (clicked from the confirmation email). Worker looks up the token, POSTs the email to Resend's `/audiences/{id}/contacts`, deletes the token, redirects to `SUCCESS_URL`.

## Features

- Double opt-in: Resend audience is only touched after the subscriber clicks the link in the confirmation email.
- Rate-limited: one confirmation email per address per 24h. Prevents abuse.
- No-JS-friendly: plain HTML forms work, redirects instead of JSON where appropriate.
- Origin-locked: only accepts submissions from origins in `ALLOWED_ORIGINS`.
- Multi-list: `list=fa` form field picks `AUDIENCE_ID_FA` and switches confirmation email copy to Farsi. Add new languages in the `TEMPLATES` object in `src/worker.ts`.
- JSON or form: both `application/x-www-form-urlencoded` and `application/json` supported.

## Deploy

1. Install deps: `npm install`.
2. Log in: `npx wrangler login`.
3. Create the KV namespace:
   ```
   npx wrangler kv namespace create SUBSCRIBE_KV
   ```
   Wrangler prints an `id = "..."`. Paste that into `wrangler.toml` under `[[kv_namespaces]]`, replacing `REPLACE_WITH_KV_NAMESPACE_ID`.
4. Edit `wrangler.toml` vars:
   - `SITE_NAME`: used in confirmation email subject and body.
   - `FROM_ADDR`: sender address, must be on a verified Resend domain.
   - `ALLOWED_ORIGINS`: comma-separated list of origins permitted to POST.
   - `SUCCESS_URL`: where to redirect after confirmation succeeds.
   - `CONFIRMATION_SENT_URL`: where to redirect after POST / (the "check your email" page).
   - `ERROR_URL`: optional, shown on bad-token or failed subscribe.
5. Set secrets:
   ```
   npx wrangler secret put RESEND_API_KEY
   npx wrangler secret put AUDIENCE_ID
   # Optional per-list audience overrides:
   npx wrangler secret put AUDIENCE_ID_FA
   ```
6. Deploy: `npx wrangler deploy`. Wrangler prints the Worker URL, e.g. `https://subscribe-proxy.<you>.workers.dev`.

## Use it from a static site

Plain HTML form, no JavaScript:

```html
<form action="https://subscribe-proxy.<you>.workers.dev" method="POST">
  <input type="email" name="email" required placeholder="you@example.com">
  <button type="submit">Subscribe</button>
</form>
```

With per-list routing (e.g. Farsi audience):

```html
<form action="https://subscribe-proxy.<you>.workers.dev" method="POST">
  <input type="email" name="email" required>
  <input type="hidden" name="list" value="fa">
  <button type="submit">اشتراک</button>
</form>
```

JSON variant (for JS clients that want a structured response):

```js
await fetch("https://subscribe-proxy.<you>.workers.dev", {
  method: "POST",
  headers: { "Content-Type": "application/json", "Accept": "application/json" },
  body: JSON.stringify({ email: "you@example.com" }),
});
```

## What lives where

- **Subscriber list:** Resend (managed, includes one-click unsubscribe, bounce handling, `List-Unsubscribe` headers).
- **API key:** Cloudflare secret, never in Git, never on your laptop beyond the initial `wrangler secret put`.
- **Worker code:** this repo (or lifted into any other project as a standalone directory).

## Development

```
npm install
npx wrangler dev          # local server at http://localhost:8787
```

Subscribe (should 303 and send a confirmation email):
```
curl -i -X POST http://localhost:8787 \
  -H "Origin: https://k1monfared.github.io" \
  -d "email=YOU@example.com&list=en"
```

Then click the link in the confirmation email, or hit the confirm endpoint directly with the token you extract from the email:
```
curl -i "http://localhost:8787/confirm?token=<token-from-email>"
```

Expected: 303 to `SUCCESS_URL`, and `YOU@example.com` appears in the Resend segment.
