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
 *   ERROR_URL              optional, shown on bad-token or subscribe errors
 *
 * Required KV binding (in wrangler.toml):
 *   SUBSCRIBE_KV           namespace for token and rate-limit keys
 */

export interface Env {
  RESEND_API_KEY: string;
  AUDIENCE_ID: string;
  FROM_ADDR: string;
  SITE_NAME: string;
  ALLOWED_ORIGINS: string;
  SUCCESS_URL: string;
  CONFIRMATION_SENT_URL: string;
  ERROR_URL?: string;
  SUBSCRIBE_KV: KVNamespace;
  [key: string]: string | KVNamespace | undefined;
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const TOKEN_TTL_SECONDS = 60 * 60 * 24; // 24h
const RATE_LIMIT_TTL_SECONDS = 60 * 60 * 24; // 24h

/**
 * Email copy per language. Placeholders: {email}, {confirm_url}, {site_name}.
 * Add a new language by adding another key. Worker picks this based on the
 * `list` form field (e.g. list=fa -> TEMPLATES.fa). Unknown/missing lists
 * fall back to English.
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
      `<p>Or paste this link into your browser: <br><a href="{confirm_url}">{confirm_url}</a></p>` +
      `<p>The link is valid for 24 hours.</p>` +
      `<p>If that was not you, just ignore this email. You will not be subscribed ` +
      `and you will not hear from us again.</p>`,
    text:
      `Someone (hopefully you) used the subscribe form on {site_name} to sign up {email}.\n\n` +
      `Confirm your subscription:\n{confirm_url}\n\n` +
      `The link is valid for 24 hours.\n\n` +
      `If that was not you, just ignore this email. You will not be subscribed.\n`,
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
      `<p>یا این لینک را در مرورگر باز کنید: <br><a href="{confirm_url}">{confirm_url}</a></p>` +
      `<p>این لینک تا ۲۴ ساعت معتبر است.</p>` +
      `<p>اگر این کار توسط شما نبوده، این ایمیل را نادیده بگیرید. ` +
      `اشتراکی ثبت نخواهد شد و دیگر پیامی از ما دریافت نخواهید کرد.</p>` +
      `</div>`,
    text:
      `شخصی (امیدواریم خودتان) از فرم اشتراک در {site_name} برای ثبت {email} استفاده کرده است.\n\n` +
      `تأیید اشتراک:\n{confirm_url}\n\n` +
      `این لینک تا ۲۴ ساعت معتبر است.\n\n` +
      `اگر این کار توسط شما نبوده، این ایمیل را نادیده بگیرید.\n`,
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

async function sendConfirmationEmail(
  env: Env,
  to: string,
  confirmUrl: string,
  list: string,
): Promise<boolean> {
  const tpl = templateFor(list);
  const vars = { email: to, confirm_url: confirmUrl, site_name: env.SITE_NAME };
  const payload = {
    from: env.FROM_ADDR,
    to,
    subject: fillTemplate(tpl.subject, vars),
    html: fillTemplate(tpl.html, vars),
    text: fillTemplate(tpl.text, vars),
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
  if (!originAllowed(req, env)) return new Response("Forbidden", { status: 403 });

  const parsed = await parseSubscribeBody(req);
  if (!parsed) {
    return respondRedirect(req, env.ERROR_URL || `${env.CONFIRMATION_SENT_URL}?err=1`, "Invalid email", false);
  }

  const audience = resolveAudience(env, parsed.list);
  if (!audience) {
    return respondRedirect(req, env.ERROR_URL || `${env.CONFIRMATION_SENT_URL}?err=1`, "List not configured", false);
  }

  // Rate-limit per email: if we've already queued a confirmation for this
  // address in the last 24h, silently show the same success page without
  // sending another email. This prevents abuse where someone submits
  // random addresses repeatedly.
  const rateKey = `rl:${parsed.email}`;
  const existing = await env.SUBSCRIBE_KV.get(rateKey);
  if (existing) {
    return respondRedirect(req, env.CONFIRMATION_SENT_URL, "Already pending", true);
  }

  const token = generateToken();
  const record = JSON.stringify({
    email: parsed.email,
    audience,
    list: parsed.list || "en",
    created_at: Date.now(),
  });
  await env.SUBSCRIBE_KV.put(`token:${token}`, record, { expirationTtl: TOKEN_TTL_SECONDS });
  await env.SUBSCRIBE_KV.put(rateKey, "1", { expirationTtl: RATE_LIMIT_TTL_SECONDS });

  // Build the confirmation URL from this Worker's own origin so we do not
  // need a separate config entry for it.
  const workerUrl = new URL(req.url);
  const confirmUrl = `${workerUrl.origin}/confirm?token=${token}`;

  const sent = await sendConfirmationEmail(env, parsed.email, confirmUrl, parsed.list || "en");
  if (!sent) {
    // The token still exists in KV. If Resend is flaky, the user can retry
    // after the rate-limit window expires. We don't leak the failure through
    // the redirect since the user can't do anything useful with that info.
    return respondRedirect(req, env.ERROR_URL || `${env.CONFIRMATION_SENT_URL}?err=1`, "Send failed", false);
  }

  return respondRedirect(req, env.CONFIRMATION_SENT_URL, "Confirmation sent", true);
}

async function handleConfirm(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const token = url.searchParams.get("token") || "";
  if (!/^[a-f0-9]{64}$/.test(token)) {
    return Response.redirect(env.ERROR_URL || `${env.SUCCESS_URL}?err=bad_token`, 303);
  }
  const tokenKey = `token:${token}`;
  const raw = await env.SUBSCRIBE_KV.get(tokenKey);
  if (!raw) {
    return Response.redirect(env.ERROR_URL || `${env.SUCCESS_URL}?err=expired`, 303);
  }
  let record: { email: string; audience: string; list?: string };
  try {
    record = JSON.parse(raw);
  } catch {
    await env.SUBSCRIBE_KV.delete(tokenKey);
    return Response.redirect(env.ERROR_URL || `${env.SUCCESS_URL}?err=bad_token`, 303);
  }

  const resp = await addToResendAudience(record.email, record.audience, env.RESEND_API_KEY);
  // Treat any 2xx or 422 (already-exists) as success.
  if (!((resp.status >= 200 && resp.status < 300) || resp.status === 422)) {
    return Response.redirect(env.ERROR_URL || `${env.SUCCESS_URL}?err=resend`, 303);
  }

  // Clean up: delete the token so it can't be reused, and the rate-limit
  // marker so the user can re-subscribe later if they want.
  await env.SUBSCRIBE_KV.delete(tokenKey);
  await env.SUBSCRIBE_KV.delete(`rl:${record.email}`);

  return Response.redirect(env.SUCCESS_URL, 303);
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
    return new Response("Not Found", { status: 404 });
  },
};
