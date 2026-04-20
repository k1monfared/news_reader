/**
 * subscribe-proxy: a generic Cloudflare Worker that accepts a subscribe form
 * submission from a static site and forwards it to Resend's audience contacts
 * API. Holds the privileged RESEND_API_KEY server-side so the static site can
 * remain fully static.
 *
 * Accepts:
 *   POST /                         application/x-www-form-urlencoded or application/json
 *     body: email=foo@bar.com      or { "email": "foo@bar.com" }
 *
 * Behavior:
 *   - Rejects origins not in ALLOWED_ORIGINS (comma-separated env var).
 *   - Validates the email shape with a simple regex.
 *   - POSTs to Resend: /audiences/{AUDIENCE_ID}/contacts.
 *   - Returns 303 redirect to SUCCESS_URL (for no-JS form submits),
 *     or a JSON body if the client sent Accept: application/json.
 *
 * Optional per-audience routing: if the request includes a "list" field
 * (e.g. list=fa), the worker looks up env[`AUDIENCE_ID_${list.toUpperCase()}`]
 * first, falling back to AUDIENCE_ID. That lets one Worker serve several
 * lists (e.g. en and fa) with a single deployment.
 *
 * Env vars (set as Cloudflare secrets or wrangler vars):
 *   RESEND_API_KEY       required
 *   AUDIENCE_ID          required (default audience)
 *   AUDIENCE_ID_<NAME>   optional, one per named list
 *   ALLOWED_ORIGINS      required, comma-separated list of origins
 *   SUCCESS_URL          required, URL to redirect to after success
 *   ERROR_URL            optional, URL to redirect to on failure (defaults to SUCCESS_URL + ?err=1)
 */

export interface Env {
  RESEND_API_KEY: string;
  AUDIENCE_ID: string;
  ALLOWED_ORIGINS: string;
  SUCCESS_URL: string;
  ERROR_URL?: string;
  [key: string]: string | undefined;
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function wantsJson(req: Request): boolean {
  return (req.headers.get("accept") || "").toLowerCase().includes("application/json");
}

function originAllowed(req: Request, env: Env): boolean {
  const allowed = (env.ALLOWED_ORIGINS || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (allowed.length === 0) return false;
  const origin = req.headers.get("origin") || "";
  const referer = req.headers.get("referer") || "";
  for (const a of allowed) {
    if (origin === a) return true;
    if (referer.startsWith(a)) return true;
  }
  return false;
}

async function parseEmail(req: Request): Promise<{ email: string; list: string } | null> {
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
  return { email, list: list.replace(/[^A-Za-z0-9_]/g, "").slice(0, 32) };
}

function resolveAudience(env: Env, list: string): string | null {
  if (list) {
    const key = `AUDIENCE_ID_${list.toUpperCase()}`;
    const specific = env[key];
    if (specific) return specific;
  }
  return env.AUDIENCE_ID || null;
}

async function addToResend(email: string, audienceId: string, apiKey: string): Promise<Response> {
  return fetch(`https://api.resend.com/audiences/${audienceId}/contacts`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ email, unsubscribed: false }),
  });
}

function respond(req: Request, ok: boolean, env: Env, message: string, status: number): Response {
  if (wantsJson(req)) {
    return new Response(JSON.stringify({ ok, message }), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }
  const target = ok
    ? env.SUCCESS_URL
    : (env.ERROR_URL || `${env.SUCCESS_URL}?err=1`);
  return Response.redirect(target, 303);
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    if (req.method !== "POST") {
      return new Response("Method Not Allowed", { status: 405 });
    }
    if (!originAllowed(req, env)) {
      return new Response("Forbidden", { status: 403 });
    }

    const parsed = await parseEmail(req);
    if (!parsed) {
      return respond(req, false, env, "Invalid email", 400);
    }

    const audience = resolveAudience(env, parsed.list);
    if (!audience) {
      return respond(req, false, env, "List not configured", 500);
    }

    const resp = await addToResend(parsed.email, audience, env.RESEND_API_KEY);
    // Resend returns 201 on create, 200 on already-exists (or 422 depending on plan).
    // Treat any 2xx as success; surface anything else as a generic failure.
    if (resp.status >= 200 && resp.status < 300) {
      return respond(req, true, env, "Subscribed", 303);
    }
    // 422 "already_exists" is a soft success from the user's perspective.
    if (resp.status === 422) {
      return respond(req, true, env, "Already subscribed", 303);
    }
    return respond(req, false, env, "Subscription failed", 502);
  },
};
