/**
 * subscribe-proxy: a generic, double-opt-in subscribe endpoint for a static
 * site backed by Resend. Holds the privileged RESEND_API_KEY server-side and
 * a Cloudflare KV namespace for pending confirmation tokens.
 *
 * Flow:
 *   POST /                          subscriber submits email
 *     -> Worker generates a token, stores (token -> {email, list}) in KV
 *        with a 24h TTL, also stores (rl:<email> -> 1) with 24h TTL for
 *        rate-limiting, sends a confirmation email via Resend's transactional
 *        API, redirects to CONFIRMATION_SENT_URL.
 *
 *     If (rl:<email>) already exists in KV, the Worker silently returns the
 *     same success redirect without resending. That prevents a bad actor from
 *     spamming a third party with confirmation emails.
 *
 *   GET /confirm?token=...          subscriber clicks the confirmation link
 *     -> Worker looks up the token, POSTs the email to Resend's
 *        audiences/{id}/contacts endpoint, deletes the token, redirects to
 *        SUCCESS_URL (the "you're subscribed" landing page).
 *
 *   GET /block?token=...            recipient clicks "never email me again"
 *     -> Worker looks up the token, writes block:<email> -> 1 to KV with a
 *        10-year TTL, deletes the pending token and the rate-limit marker,
 *        redirects to BLOCKED_URL. Subsequent subscribe attempts for that
 *        address are silently dropped (attacker can't tell from the response).
 *
 * Multi-list routing: the subscribe form can include a "list" field (e.g.
 * list=fa) which picks env[`AUDIENCE_ID_FA`] and switches the confirmation
 * email copy to Farsi. Without the field it falls back to AUDIENCE_ID and
 * English copy. Add new languages by extending the TEMPLATES object below.
 *
 * Required env vars (all via wrangler secret put, unless otherwise noted):
 *   RESEND_API_KEY         required
 *   AUDIENCE_ID            required, default segment/audience UUID
 *   AUDIENCE_ID_<NAME>     optional per-list override (e.g. AUDIENCE_ID_FA)
 *   FROM_ADDR              required, e.g. "Daily Brief <brief@example.com>"
 *   SITE_NAME              required, used in the confirmation email copy
 *   ALLOWED_ORIGINS        required, comma-separated list of origins (vars)
 *   SUCCESS_URL            required, landing page shown AFTER confirmation
 *   CONFIRMATION_SENT_URL  required, landing page shown after POST /
 *   BLOCKED_URL            required, landing page shown after "never email me"
 *   ERROR_URL              optional, shown on bad-token or subscribe errors
 *
 * Required KV binding (in wrangler.toml):
 *   SUBSCRIBE_KV           namespace for token and rate-limit keys
 *
 * Required D1 binding (in wrangler.toml):
 *   AUDIT_DB               SQLite database holding the subscribe_logs table
 *                          (schema in ./schema.sql). Every subscribe or
 *                          confirm attempt writes one row. Stored privately
 *                          in the Cloudflare account — never in the repo.
 */

export interface Env {
  RESEND_API_KEY: string;
  AUDIENCE_ID: string;
  FROM_ADDR: string;
  SITE_NAME: string;
  ALLOWED_ORIGINS: string;
  SUCCESS_URL: string;
  CONFIRMATION_SENT_URL: string;
  BLOCKED_URL: string;
  ERROR_URL?: string;
  SUBSCRIBE_KV: KVNamespace;
  AUDIT_DB: D1Database;
  [key: string]: string | KVNamespace | D1Database | undefined;
}

// Strict character class: rejects whitespace, HTML-active characters
// (`<`, `>`, `"`, `'`), and other punctuation that is legal per RFC 5322 but
// almost never seen in practice. The goal is defense in depth — even though
// the email is HTML-escaped before substitution into the confirmation email
// body, a tight input filter prevents weird payloads from reaching any
// downstream system that might not escape.
const EMAIL_RE = /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/;
const TOKEN_TTL_SECONDS = 60 * 60 * 24; // 24h
const RATE_LIMIT_TTL_SECONDS = 60 * 60 * 24; // 24h
const BLOCK_TTL_SECONDS = 10 * 365 * 24 * 60 * 60; // 10 years, effectively permanent

/**
 * Email copy per language. Placeholders: {email}, {confirm_url}, {block_url},
 * {site_name}. Add a new language by adding another key. Worker picks this
 * based on the `list` form field (e.g. list=fa -> TEMPLATES.fa). Unknown or
 * missing lists fall back to English.
 */
const TEMPLATES: Record<string, { subject: string; html: string; text: string }> = {
  en: {
    subject: "Confirm your subscription to {site_name}",
    html:
      `<p>Someone (hopefully you) used the subscribe form on <strong>{site_name}</strong> ` +
      `to sign up <strong>{email}</strong>.</p>` +
      `<p><a href="{confirm_url}" style="display:inline-block;padding:10px 16px;` +
      `background:#0b5394;color:#ffffff;text-decoration:none;border-radius:6px;">` +
      `Confirm subscription</a></p>` +
      `<p style="font-size:13px;color:#666;">Or paste this link into your browser: ` +
      `<br><a href="{confirm_url}">{confirm_url}</a></p>` +
      `<p>The confirm link is valid for 24 hours. If you don't click it, nothing happens.</p>` +
      `<hr style="margin:24px 0;border:none;border-top:1px solid #ddd;">` +
      `<p style="font-size:13px;color:#666;">Did not request this? ` +
      `<a href="{block_url}">Block this address permanently</a> and we will never ` +
      `contact you again, even if someone tries to subscribe you later.</p>`,
    text:
      `Someone (hopefully you) used the subscribe form on {site_name} to sign up {email}.\n\n` +
      `Confirm your subscription:\n{confirm_url}\n\n` +
      `The confirm link is valid for 24 hours. If you don't click it, nothing happens.\n\n` +
      `Did not request this? Block this address permanently, and we will never\n` +
      `contact you again even if someone tries to subscribe you later:\n{block_url}\n`,
  },
  fa: {
    subject: "تأیید اشتراک در {site_name}",
    html:
      `<div dir="rtl">` +
      `<p>شخصی (امیدواریم خودتان) از فرم اشتراک در <strong>{site_name}</strong> ` +
      `برای ثبت <strong>{email}</strong> استفاده کرده است.</p>` +
      `<p><a href="{confirm_url}" style="display:inline-block;padding:10px 16px;` +
      `background:#0b5394;color:#ffffff;text-decoration:none;border-radius:6px;">` +
      `تأیید اشتراک</a></p>` +
      `<p style="font-size:13px;color:#666;">یا این لینک را در مرورگر باز کنید: ` +
      `<br><a href="{confirm_url}">{confirm_url}</a></p>` +
      `<p>لینک تأیید تا ۲۴ ساعت معتبر است. اگر کلیک نکنید، هیچ اتفاقی نمی‌افتد.</p>` +
      `<hr style="margin:24px 0;border:none;border-top:1px solid #ddd;">` +
      `<p style="font-size:13px;color:#666;">شما این درخواست را نکرده‌اید؟ ` +
      `<a href="{block_url}">این آدرس را برای همیشه مسدود کنید</a> تا دیگر هرگز ` +
      `با شما تماس گرفته نشود، حتی اگر شخص دیگری بعداً تلاش به ثبت‌نام شما کند.</p>` +
      `</div>`,
    text:
      `شخصی (امیدواریم خودتان) از فرم اشتراک در {site_name} برای ثبت {email} استفاده کرده است.\n\n` +
      `تأیید اشتراک:\n{confirm_url}\n\n` +
      `لینک تأیید تا ۲۴ ساعت معتبر است.\n\n` +
      `شما این درخواست را نکرده‌اید؟ این آدرس را برای همیشه مسدود کنید\n` +
      `تا دیگر هرگز با شما تماس گرفته نشود:\n{block_url}\n`,
  },
};

function wantsJson(req: Request): boolean {
  return (req.headers.get("accept") || "").toLowerCase().includes("application/json");
}

function originAllowed(req: Request, env: Env): boolean {
  const allowed = (env.ALLOWED_ORIGINS || "")
    .split(",").map((s) => s.trim()).filter(Boolean);
  if (allowed.length === 0) return false;
  const origin = req.headers.get("origin") || "";
  const referer = req.headers.get("referer") || "";
  for (const a of allowed) {
    if (origin === a) return true;
    if (referer.startsWith(a)) return true;
  }
  return false;
}

async function parseSubscribeBody(req: Request): Promise<{ email: string; list: string } | null> {
  const ct = (req.headers.get("content-type") || "").toLowerCase();
  let email = "";
  let list = "";
  if (ct.includes("application/json")) {
    try {
      const body = (await req.json()) as Record<string, unknown>;
      email = String(body.email ?? "").trim();
      list = String(body.list ?? "").trim();
    } catch {
      return null;
    }
  } else {
    const form = await req.formData();
    email = String(form.get("email") ?? "").trim();
    list = String(form.get("list") ?? "").trim();
  }
  if (!EMAIL_RE.test(email)) return null;
  return {
    email: email.toLowerCase(),
    list: list.replace(/[^A-Za-z0-9_]/g, "").slice(0, 32).toLowerCase(),
  };
}

function resolveAudience(env: Env, list: string): string | null {
  if (list) {
    const key = `AUDIENCE_ID_${list.toUpperCase()}`;
    const specific = env[key];
    if (typeof specific === "string" && specific) return specific;
  }
  return env.AUDIENCE_ID || null;
}

/**
 * Resolve a per-language URL. For a given base name like
 * "CONFIRMATION_SENT_URL" and a list like "fa", look for
 * CONFIRMATION_SENT_URL_FA first and fall back to CONFIRMATION_SENT_URL.
 * Returns empty string if neither is set (caller should treat as error).
 */
function resolveUrl(env: Env, base: string, list: string): string {
  if (list) {
    const key = `${base}_${list.toUpperCase()}`;
    const specific = env[key];
    if (typeof specific === "string" && specific) return specific;
  }
  const fallback = env[base];
  return typeof fallback === "string" ? fallback : "";
}

function templateFor(list: string): { subject: string; html: string; text: string } {
  return TEMPLATES[list] || TEMPLATES.en;
}

function generateToken(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes).map((b) => b.toString(16).padStart(2, "0")).join("");
}

function fillTemplate(tpl: string, vars: Record<string, string>): string {
  return tpl.replace(/{(\w+)}/g, (_, k) => vars[k] ?? `{${k}}`);
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function sendConfirmationEmail(
  env: Env,
  to: string,
  confirmUrl: string,
  blockUrl: string,
  list: string,
): Promise<boolean> {
  const tpl = templateFor(list);
  // Two vars sets: one raw (for subject and plain text), one with every user-
  // controllable field HTML-escaped before substitution into the HTML body.
  const varsText = {
    email: to,
    confirm_url: confirmUrl,
    block_url: blockUrl,
    site_name: env.SITE_NAME,
  };
  const varsHtml = {
    ...varsText,
    email:     escapeHtml(to),
    site_name: escapeHtml(env.SITE_NAME || ""),
  };
  const payload = {
    from: env.FROM_ADDR,
    to,
    subject: fillTemplate(tpl.subject, varsText),
    html: fillTemplate(tpl.html, varsHtml),
    text: fillTemplate(tpl.text, varsText),
  };
  const resp = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.RESEND_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return resp.status >= 200 && resp.status < 300;
}

async function addToResendAudience(email: string, audienceId: string, apiKey: string): Promise<Response> {
  return fetch(`https://api.resend.com/audiences/${audienceId}/contacts`, {
    method: "POST",
    headers: { "Authorization": `Bearer ${apiKey}`, "Content-Type": "application/json" },
    body: JSON.stringify({ email, unsubscribed: false }),
  });
}

async function logEvent(
  env: Env,
  req: Request,
  event: "subscribe_attempt" | "confirm_attempt" | "block_attempt",
  outcome: string,
  fields: { email?: string | null; list?: string | null; tokenPrefix?: string | null } = {},
): Promise<void> {
  const cf = ((req as unknown) as { cf?: { country?: string } }).cf || {};
  try {
    await env.AUDIT_DB.prepare(
      `INSERT INTO subscribe_logs
         (ts, event, outcome, email, list, ip, country, user_agent, referer, token_prefix)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      new Date().toISOString(),
      event,
      outcome,
      fields.email ?? null,
      fields.list ?? null,
      req.headers.get("cf-connecting-ip") ?? null,
      cf.country ?? null,
      req.headers.get("user-agent") ?? null,
      req.headers.get("referer") ?? null,
      fields.tokenPrefix ?? null,
    ).run();
  } catch (e) {
    // Never fail a user-visible request just because the audit write failed.
    // The log shows up in `wrangler tail` if something is misconfigured.
    console.error("audit log failed:", e);
  }
}

function respondRedirect(req: Request, url: string, message: string, ok: boolean): Response {
  if (wantsJson(req)) {
    return new Response(JSON.stringify({ ok, message, redirect: url }), {
      status: ok ? 200 : 400,
      headers: { "Content-Type": "application/json" },
    });
  }
  return Response.redirect(url, 303);
}

async function handleSubscribe(req: Request, env: Env): Promise<Response> {
  if (!originAllowed(req, env)) {
    await logEvent(env, req, "subscribe_attempt", "bad_origin", {});
    return new Response("Forbidden", { status: 403 });
  }

  const parsed = await parseSubscribeBody(req);
  if (!parsed) {
    await logEvent(env, req, "subscribe_attempt", "invalid_email", {});
    const errorUrl = resolveUrl(env, "ERROR_URL", "")
      || `${resolveUrl(env, "CONFIRMATION_SENT_URL", "")}?err=1`;
    return respondRedirect(req, errorUrl, "Invalid email", false);
  }

  const list = parsed.list;
  const confirmationSentUrl = resolveUrl(env, "CONFIRMATION_SENT_URL", list);
  const errorUrl = resolveUrl(env, "ERROR_URL", list) || `${confirmationSentUrl}?err=1`;

  const audience = resolveAudience(env, list);
  if (!audience) {
    await logEvent(env, req, "subscribe_attempt", "list_not_configured", {
      email: parsed.email, list,
    });
    return respondRedirect(req, errorUrl, "List not configured", false);
  }

  // Permanent block: if the address previously clicked the "never email me
  // again" link in a confirmation email, drop the attempt silently. The
  // attacker gets the same success redirect as a real user, so they can't
  // distinguish blocked addresses from pending ones.
  const blockKey = `block:${parsed.email}`;
  const isBlocked = await env.SUBSCRIBE_KV.get(blockKey);
  if (isBlocked) {
    await logEvent(env, req, "subscribe_attempt", "blocked", {
      email: parsed.email, list,
    });
    return respondRedirect(req, confirmationSentUrl, "Already pending", true);
  }

  // Rate-limit per email: if we've already queued a confirmation for this
  // address in the last 24h, silently show the same success page without
  // sending another email. This prevents abuse where someone submits
  // random addresses repeatedly.
  const rateKey = `rl:${parsed.email}`;
  const existing = await env.SUBSCRIBE_KV.get(rateKey);
  if (existing) {
    await logEvent(env, req, "subscribe_attempt", "rate_limited", {
      email: parsed.email, list,
    });
    return respondRedirect(req, confirmationSentUrl, "Already pending", true);
  }

  const token = generateToken();
  const tokenPrefix = token.slice(0, 8);
  const record = JSON.stringify({
    email: parsed.email,
    audience,
    list: parsed.list || "en",
    created_at: Date.now(),
  });
  await env.SUBSCRIBE_KV.put(`token:${token}`, record, { expirationTtl: TOKEN_TTL_SECONDS });
  await env.SUBSCRIBE_KV.put(rateKey, "1", { expirationTtl: RATE_LIMIT_TTL_SECONDS });

  // Build the confirmation + block URLs from this Worker's own origin so we
  // do not need separate config entries for them.
  const workerUrl = new URL(req.url);
  const confirmUrl = `${workerUrl.origin}/confirm?token=${token}`;
  const blockUrl = `${workerUrl.origin}/block?token=${token}`;

  const sent = await sendConfirmationEmail(env, parsed.email, confirmUrl, blockUrl, parsed.list || "en");
  if (!sent) {
    // The token still exists in KV. If Resend is flaky, the user can retry
    // after the rate-limit window expires. We don't leak the failure through
    // the redirect since the user can't do anything useful with that info.
    await logEvent(env, req, "subscribe_attempt", "send_failed", {
      email: parsed.email, list, tokenPrefix,
    });
    return respondRedirect(req, errorUrl, "Send failed", false);
  }

  await logEvent(env, req, "subscribe_attempt", "pending", {
    email: parsed.email, list, tokenPrefix,
  });
  return respondRedirect(req, confirmationSentUrl, "Confirmation sent", true);
}

async function handleConfirm(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const token = url.searchParams.get("token") || "";
  const tokenPrefix = token.slice(0, 8) || null;
  if (!/^[a-f0-9]{64}$/.test(token)) {
    await logEvent(env, req, "confirm_attempt", "bad_token", { tokenPrefix });
    return Response.redirect(
      resolveUrl(env, "ERROR_URL", "") || `${resolveUrl(env, "SUCCESS_URL", "")}?err=bad_token`,
      303,
    );
  }
  const tokenKey = `token:${token}`;
  const raw = await env.SUBSCRIBE_KV.get(tokenKey);
  if (!raw) {
    await logEvent(env, req, "confirm_attempt", "expired_token", { tokenPrefix });
    return Response.redirect(
      resolveUrl(env, "ERROR_URL", "") || `${resolveUrl(env, "SUCCESS_URL", "")}?err=expired`,
      303,
    );
  }
  let record: { email: string; audience: string; list?: string };
  try {
    record = JSON.parse(raw);
  } catch {
    await env.SUBSCRIBE_KV.delete(tokenKey);
    await logEvent(env, req, "confirm_attempt", "bad_token", { tokenPrefix });
    return Response.redirect(
      resolveUrl(env, "ERROR_URL", "") || `${resolveUrl(env, "SUCCESS_URL", "")}?err=bad_token`,
      303,
    );
  }

  const list = record.list || "";
  const successUrl = resolveUrl(env, "SUCCESS_URL", list);
  const errorUrl = resolveUrl(env, "ERROR_URL", list) || `${successUrl}?err=1`;

  const resp = await addToResendAudience(record.email, record.audience, env.RESEND_API_KEY);
  // Treat any 2xx or 422 (already-exists) as success.
  if (!((resp.status >= 200 && resp.status < 300) || resp.status === 422)) {
    await logEvent(env, req, "confirm_attempt", "resend_error", {
      email: record.email, list, tokenPrefix,
    });
    return Response.redirect(errorUrl, 303);
  }

  // Clean up: delete the token so it can't be reused, and the rate-limit
  // marker so the user can re-subscribe later if they want.
  await env.SUBSCRIBE_KV.delete(tokenKey);
  await env.SUBSCRIBE_KV.delete(`rl:${record.email}`);

  await logEvent(env, req, "confirm_attempt", "confirmed", {
    email: record.email, list, tokenPrefix,
  });
  return Response.redirect(successUrl, 303);
}

async function handleBlock(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const token = url.searchParams.get("token") || "";
  const tokenPrefix = token.slice(0, 8) || null;
  if (!/^[a-f0-9]{64}$/.test(token)) {
    await logEvent(env, req, "block_attempt", "bad_token", { tokenPrefix });
    return Response.redirect(
      resolveUrl(env, "ERROR_URL", "") || `${resolveUrl(env, "BLOCKED_URL", "")}?err=bad_token`,
      303,
    );
  }
  const tokenKey = `token:${token}`;
  const raw = await env.SUBSCRIBE_KV.get(tokenKey);
  if (!raw) {
    // Token may have expired (24h) or been consumed. We still want the
    // subscriber to land on the "blocked" page so their intent is honored
    // visually, but we have no email to actually block here.
    await logEvent(env, req, "block_attempt", "expired_token", { tokenPrefix });
    return Response.redirect(resolveUrl(env, "BLOCKED_URL", ""), 303);
  }
  let record: { email: string; list?: string };
  try {
    record = JSON.parse(raw);
  } catch {
    await env.SUBSCRIBE_KV.delete(tokenKey);
    await logEvent(env, req, "block_attempt", "bad_token", { tokenPrefix });
    return Response.redirect(
      resolveUrl(env, "ERROR_URL", "") || `${resolveUrl(env, "BLOCKED_URL", "")}?err=bad_token`,
      303,
    );
  }

  const list = record.list || "";
  await env.SUBSCRIBE_KV.put(`block:${record.email}`, "1", { expirationTtl: BLOCK_TTL_SECONDS });
  await env.SUBSCRIBE_KV.delete(tokenKey);
  await env.SUBSCRIBE_KV.delete(`rl:${record.email}`);

  await logEvent(env, req, "block_attempt", "blocked", {
    email: record.email, list, tokenPrefix,
  });
  return Response.redirect(resolveUrl(env, "BLOCKED_URL", list), 303);
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    if (req.method === "POST" && url.pathname === "/") {
      return handleSubscribe(req, env);
    }
    if (req.method === "GET" && url.pathname === "/confirm") {
      return handleConfirm(req, env);
    }
    if (req.method === "GET" && url.pathname === "/block") {
      return handleBlock(req, env);
    }
    return new Response("Not Found", { status: 404 });
  },
};
