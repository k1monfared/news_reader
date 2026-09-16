"""Phase 0: authenticated model probe (dry run, no pipeline changes).

Runs in GitHub Actions with the real OPENCODE_API_KEY to answer the one
question that cannot be answered keyless: does the key flip any free model
from GATED (MissingSessionID) to OK?

Flow (mirrors v2 plan + verified prototypes in /tmp/verify_selector):
  1. Key-health check (step 0): OPENCODE_API_KEY must be non-empty.
  2. Discover: GET {base}/v1/models, no auth -> live id set.
  3. Candidates = ids ending in "-free" + allowlist ("big-pickle").
  4. Probe each candidate through the pipeline's own call path:
     POST {base}/chat/completions with Bearer key, tiny prompt.
     On WRONG_ROUTE_OR_DOWN, retry POST {base}/responses (records winner).
  5. Classify with the verified error taxonomy (incl. envelope-B fix for
     deepseek-v4-flash-free) and write probe_results.json.
  6. Exit 0 if >=1 OK, else 1. The workflow always uploads the JSON.

Usage (CI):
  python scripts/probe_models.py --out probe_results.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.error

DEFAULT_BASE_URL = "https://opencode.ai/zen/v1"
ALLOWLIST = {"big-pickle"}
TIMEOUT_S = 60

# (needle, outcome, retry_other_route)
RULES = [
    ("not supported", "DELISTED", False),
    ("MissingSessionID", "GATED", False),
    ("free tier can only be used in OpenCode", "GATED", False),
    ("Model is unavailable", "UPSTREAM_DOWN", False),
    ("Internal server error", "WRONG_ROUTE_OR_DOWN", True),
]


def _http(method: str, url: str, headers: dict, payload: dict | None) -> tuple[int, str]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"User-Agent": "news_reader-phase0-probe/1.0", **headers}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body
    except Exception as e:  # network failure, DNS, timeout
        return -1, json.dumps({"error": {"type": "TRANSPORT", "message": str(e)}})


def _err_text(status: int, body: str) -> str:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return f"HTTP_{status}_UNPARSEABLE"
    # envelope A: {"type":"error","error":{...}} ; envelope B: {"error":{...}}
    err = data.get("error", data)
    if isinstance(err, dict) and isinstance(err.get("error"), dict):
        err = err["error"]
    if isinstance(err, dict):
        return f"{err.get('type', '?')}: {err.get('message', '?')}"
    return f"HTTP_{status}_UNKNOWN_SHAPE"


def classify(status: int, body: str, route: str) -> tuple[str, bool]:
    """-> (outcome, try_other_route)."""
    if status == 200:
        try:
            data = json.loads(body)
            if route == "responses":
                out = data.get("output") or []
                texts = []
                for item in out:
                    for c in item.get("content", []) or []:
                        if isinstance(c, dict) and c.get("text"):
                            texts.append(c["text"])
                if "".join(texts).strip():
                    return ("OK", False)
                return ("EMPTY_CONTENT", False)
            content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            if content:
                return ("OK", False)
            return ("EMPTY_CONTENT", False)
        except (json.JSONDecodeError, ValueError, AttributeError):
            return ("HTTP_200_UNPARSEABLE", False)
    if status == 404:
        return ("NO_SUCH_ROUTE", False)
    text = _err_text(status, body)
    for needle, outcome, retry in RULES:
        if needle in text:
            if outcome == "WRONG_ROUTE_OR_DOWN" and route != "chat/completions":
                return ("DOWN", False)
            return (outcome, retry and route == "chat/completions")
    return (f"HTTP_{status}_OTHER", False)


def discover(base: str) -> list[str]:
    status, body = _http("GET", f"{base}/models", {}, None)
    if status != 200:
        raise RuntimeError(f"discovery failed: HTTP {status}: {body[:300]}")
    data = json.loads(body)
    return [m["id"] for m in data.get("data", []) if isinstance(m.get("id"), str)]


def probe_chat(base: str, key: str, model: str) -> tuple[int, str]:
    return _http(
        "POST",
        f"{base}/chat/completions",
        {"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        {"model": model, "max_tokens": 5,
         "messages": [{"role": "user", "content": "Reply with the word ok."}]},
    )


def probe_responses(base: str, key: str, model: str) -> tuple[int, str]:
    return _http(
        "POST",
        f"{base}/responses",
        {"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        {"model": model, "input": "Reply with the word ok.", "max_output_tokens": 8},
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="probe_results.json")
    ap.add_argument("--base", default=os.environ.get("OPENCODE_API_BASE_URL", DEFAULT_BASE_URL))
    args = ap.parse_args()

    key = os.environ.get("OPENCODE_API_KEY", "")
    if not key:
        print("KEY_MISSING: OPENCODE_API_KEY is empty. Check the GitHub secret, "
              "not the models. Aborting probe.", file=sys.stderr)
        result = {"key_health": "MISSING", "candidates": [], "summary": "abort: key missing"}
        with open(args.out, "w") as f:
            json.dump(result, f, indent=2)
        return 2

    try:
        live_ids = discover(args.base)
    except Exception as e:
        print(f"DISCOVERY_FAILED: {e}", file=sys.stderr)
        return 3

    candidates = sorted([i for i in live_ids if i.endswith("-free") or i in ALLOWLIST])
    print(f"live models: {len(live_ids)}, free candidates: {len(candidates)}")
    print("candidates: " + ", ".join(candidates))

    results = []
    for model in candidates:
        status, body = probe_chat(args.base, key, model)
        outcome, retry = classify(status, body, "chat/completions")
        route = "chat/completions"
        if retry:
            s2, b2 = probe_responses(args.base, key, model)
            o2, _ = classify(s2, b2, "responses")
            results.append({"id": model, "route": "chat/completions",
                            "status": status, "outcome": outcome,
                            "fallback_route": "responses", "fallback_status": s2,
                            "fallback_outcome": o2, "winning_route": "responses" if o2 == "OK" else route,
                            "ok": o2 == "OK", "body_snippet": b2[:300]})
            print(f"{model:40s} chat->{status}/{outcome} responses->{s2}/{o2}")
        else:
            results.append({"id": model, "route": route, "status": status,
                            "outcome": outcome, "ok": outcome == "OK",
                            "body_snippet": body[:300]})
            print(f"{model:40s} chat->{status}/{outcome}")

    ok = [r for r in results if r["ok"]]
    gated = [r["id"] for r in results if r["outcome"] == "GATED" or r.get("fallback_outcome") == "GATED"]
    summary = {
        "key_health": "OK",
        "total": len(results),
        "ok": [r["id"] for r in ok],
        "all_gated": len(ok) == 0 and len(gated) == len(results) and len(results) > 0,
    }
    with open(args.out, "w") as f:
        json.dump({"key_health": "OK", "candidates": results, "summary": summary}, f, indent=2)
    print(f"\nOK: {summary['ok'] or 'NONE'}")
    if not ok:
        print("NONE_ACCESSIBLE: no free model answered under this key. "
              "If all are GATED, the free tier is session-gated; revisit the "
              "paid-fallback decision with this evidence.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
