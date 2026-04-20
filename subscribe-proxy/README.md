# subscribe-proxy (vendored from newsletter_base)

> **This directory is a copy of [newsletter_base/subscribe-proxy](https://github.com/k1monfared/newsletter_base/tree/master/subscribe-proxy).** The Python portion of `newsletter_base` is pip-installed via `requirements.txt`, but the Worker source and schema are vendored here because wrangler deploys from a local directory. When newsletter_base ships updates to the Worker, sync with:
>
> ```bash
> # From the repo root, with newsletter_base cloned adjacent:
> cp ../newsletter_base/subscribe-proxy/src/worker.ts subscribe-proxy/src/worker.ts
> cp ../newsletter_base/subscribe-proxy/schema.sql subscribe-proxy/schema.sql
> cd subscribe-proxy && npx wrangler deploy
> ```
>
> Do not hand-edit `worker.ts` or `schema.sql` here; push upstream first, then sync.

A Cloudflare Worker that implements **double-opt-in subscribe** for a static site backed by [Resend](https://resend.com). The Worker holds the privileged Resend API key and a KV namespace for pending confirmation tokens, so your static site can stay fully static while still protecting against bad-actor subscribe spam.

## Why this exists

Static sites cannot safely hold API keys. Resend's API also auto-confirms contacts on add, which means a naive subscribe proxy can be used to spam arbitrary addresses. This Worker fixes both: it holds the API key server-side and inserts a confirmation step so only people who click a link actually join the list.

## Flow

1. **POST /** with `email` (and optional `list=fa` to route to a non-default segment). If the address is on the permanent block list, the request silently succeeds and nothing is sent. Otherwise the Worker generates a random token, stores `{token, email, list}` in KV with a 24h TTL, rate-limits the address so resubmissions within 24h do not trigger another email, sends a confirmation email via Resend, redirects to `CONFIRMATION_SENT_URL`. The confirmation email contains two links: "confirm" and "never email me again".
2. **GET /confirm?token=...** (clicked from the confirmation email). Worker looks up the token, POSTs the email to Resend's `/audiences/{id}/contacts`, deletes the token, redirects to `SUCCESS_URL`.
3. **GET /block?token=...** (clicked from the same confirmation email when the recipient did not request the subscription). Worker writes a permanent `block:<email>` entry to KV, deletes the pending token, redirects to `BLOCKED_URL`. Subsequent subscribe attempts for that address are silently dropped.

## Features

- Double opt-in: Resend audience is only touched after the subscriber clicks the link in the confirmation email.
- Rate-limited: one confirmation email per address per 24h. Prevents abuse.
- Audit log: every subscribe or confirm attempt writes a row to a private Cloudflare D1 database (`subscribe_logs`), capturing timestamp, outcome, email, list, IP, country, user agent, referer, and the first 8 chars of the token. Queryable from the Cloudflare dashboard console or via `wrangler d1 execute`.
- No-JS-friendly: plain HTML forms work, redirects instead of JSON where appropriate.
- Origin-locked: only accepts submissions from origins in `ALLOWED_ORIGINS`.
- Multi-list: `list=fa` form field picks `AUDIENCE_ID_FA` and switches confirmation email copy to Farsi. Add new languages in the `TEMPLATES` object in `src/worker.ts`.
- JSON or form: both `application/x-www-form-urlencoded` and `application/json` supported.

## Deploy

1. Install deps: `npm install`.
2. Log in: `npx wrangler login`.
3. Create the KV namespace (for pending tokens + rate-limit markers):
   ```
   npx wrangler kv namespace create SUBSCRIBE_KV
   ```
   Wrangler prints an `id = "..."`. Paste that into `wrangler.toml` under `[[kv_namespaces]]`, replacing `REPLACE_WITH_KV_NAMESPACE_ID`.
4. Create the D1 database (for the audit log):
   ```
   npx wrangler d1 create subscribe-audit
   ```
   Wrangler prints a `database_id = "..."`. Paste that into `wrangler.toml` under `[[d1_databases]]`, replacing `REPLACE_WITH_D1_DATABASE_ID`.
5. Apply the schema:
   ```
   npx wrangler d1 execute subscribe-audit --remote --file=./schema.sql
   ```
6. Edit `wrangler.toml` vars:
   - `SITE_NAME`: used in confirmation email subject and body.
   - `FROM_ADDR`: sender address, must be on a verified Resend domain.
   - `ALLOWED_ORIGINS`: comma-separated list of origins permitted to POST.
   - `SUCCESS_URL`: where to redirect after confirmation succeeds.
   - `CONFIRMATION_SENT_URL`: where to redirect after POST / (the "check your email" page).
   - `ERROR_URL`: optional, shown on bad-token or failed subscribe.
7. Set secrets:
   ```
   npx wrangler secret put RESEND_API_KEY
   npx wrangler secret put AUDIENCE_ID
   # Optional per-list audience overrides:
   npx wrangler secret put AUDIENCE_ID_FA
   ```
8. Deploy: `npx wrangler deploy`. Wrangler prints the Worker URL, e.g. `https://subscribe-proxy.<you>.workers.dev`.

## Querying the audit log

Via the CLI (run from `subscribe-proxy/`):
```
# Recent attempts
npx wrangler d1 execute subscribe-audit --remote \
  --command="SELECT ts, event, outcome, email, list, ip, country FROM subscribe_logs ORDER BY ts DESC LIMIT 20"

# Aggregated activity by IP over the last 24h (bot hunt)
npx wrangler d1 execute subscribe-audit --remote \
  --command="SELECT ip, country, count(*) AS n FROM subscribe_logs WHERE ts > datetime('now','-24 hours') GROUP BY ip, country ORDER BY n DESC"
```

Or use the Cloudflare dashboard: **Workers & Pages → D1 → subscribe-audit → Console** for an interactive SQL window. The database lives in your Cloudflare account only, never in this repository.

Outcome values:

| event | outcome |
|---|---|
| `subscribe_attempt` | `bad_origin`, `invalid_email`, `list_not_configured`, `rate_limited`, `blocked`, `send_failed`, `pending` |
| `confirm_attempt` | `bad_token`, `expired_token`, `resend_error`, `confirmed` |
| `block_attempt` | `bad_token`, `expired_token`, `blocked` |

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
