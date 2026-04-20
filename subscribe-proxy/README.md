# subscribe-proxy

A generic Cloudflare Worker that accepts a subscribe form submission from a static site and forwards it to [Resend](https://resend.com)'s audience contacts API. The Worker holds the privileged Resend API key, so your static site can stay fully static.

Reusable across projects. Nothing in the Worker code references a specific site, audience, or list.

## Why this exists

Static sites cannot safely hold API keys. If you want a subscribe form on a GitHub Pages or similar static site, you need a tiny server-side component to call Resend without exposing the key. This Worker is that component, in ~150 lines of TypeScript.

## Features

- No-JS-friendly: returns a 303 redirect after success so a plain HTML form ends up on a static thank-you page.
- Origin-locked: only accepts submissions from origins listed in `ALLOWED_ORIGINS`.
- Multi-audience: supports per-list routing via a `list=fa` form field that picks `AUDIENCE_ID_FA`, falling back to `AUDIENCE_ID`.
- JSON or form: supports both `application/x-www-form-urlencoded` and `application/json`.

## Deploy

1. Install [wrangler](https://developers.cloudflare.com/workers/wrangler/): `npm install`.
2. Log in: `npx wrangler login`.
3. Edit `wrangler.toml`:
   - `ALLOWED_ORIGINS`: comma-separated list of origins that may POST to this Worker.
   - `SUCCESS_URL`: where to redirect after a successful subscribe.
   - `ERROR_URL`: optional, where to redirect on failure.
4. Set secrets:
   ```
   npx wrangler secret put RESEND_API_KEY
   npx wrangler secret put AUDIENCE_ID
   # Optional per-list audiences:
   npx wrangler secret put AUDIENCE_ID_FA
   ```
5. Deploy: `npx wrangler deploy`. Wrangler prints the Worker URL, e.g. `https://subscribe-proxy.<you>.workers.dev`.

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

Test a submit:
```
curl -X POST http://localhost:8787 \
  -H "Origin: https://k1monfared.github.io" \
  -d "email=test@example.com"
```
