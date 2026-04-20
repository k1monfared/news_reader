"""Resend broadcasts client. Two calls: create the broadcast, then send it.

Reference: https://resend.com/docs/api-reference/broadcasts
"""

from __future__ import annotations

from typing import Any

import httpx


RESEND_API_BASE = "https://api.resend.com"


class BroadcastError(RuntimeError):
    """Raised when Resend returns a non-success status for any call."""

    def __init__(self, step: str, status: int, body: str) -> None:
        super().__init__(f"Resend {step} failed ({status}): {body}")
        self.step = step
        self.status = status
        self.body = body


def send_broadcast(
    *,
    api_key: str,
    audience_id: str,
    from_addr: str,
    subject: str,
    html: str,
    reply_to: str | None = None,
    text: str | None = None,
    name: str | None = None,
    client: httpx.Client | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Create a broadcast in Resend and immediately send it.

    Args:
        api_key: Resend API key. Must have broadcasts permission.
        audience_id: UUID of the Resend audience to send to.
        from_addr: RFC 5322 formatted sender, e.g. 'Brief <brief@example.com>'.
            The domain must be verified in Resend.
        subject: Email subject line.
        html: Full HTML body. Resend wraps it in a minimal shell and injects
            `{{{RESEND_UNSUBSCRIBE_URL}}}` if the string appears in the body.
        reply_to: Optional Reply-To header.
        text: Optional plain-text alternative. If None, Resend generates one.
        name: Optional internal broadcast name shown in the Resend dashboard.
        client: Optional pre-configured httpx.Client (useful for tests or when
            the caller wants to share a session / pool). If None, a short-lived
            client is created for this call.
        timeout: Per-request timeout in seconds.

    Returns:
        A dict: {"broadcast_id": str, "status": "sent"}.

    Raises:
        BroadcastError: if create or send returns a non-2xx status.
    """
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=timeout)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Resend renamed the field from "audience_id" to "segment_id" in their
    # Broadcast API. Sending the new field name is what enables per-recipient
    # {{{RESEND_UNSUBSCRIBE_URL}}} substitution. The id value is the same.
    create_payload: dict[str, Any] = {
        "segment_id": audience_id,
        "from": from_addr,
        "subject": subject,
        "html": html,
    }
    if text is not None:
        create_payload["text"] = text
    if reply_to is not None:
        create_payload["reply_to"] = reply_to
    if name is not None:
        create_payload["name"] = name

    try:
        create = client.post(
            f"{RESEND_API_BASE}/broadcasts",
            headers=headers,
            json=create_payload,
        )
        if create.status_code < 200 or create.status_code >= 300:
            raise BroadcastError("create", create.status_code, create.text)

        data = create.json()
        broadcast_id = data.get("id")
        if not broadcast_id:
            raise BroadcastError("create", create.status_code, "no id in response")

        send = client.post(
            f"{RESEND_API_BASE}/broadcasts/{broadcast_id}/send",
            headers=headers,
            json={},
        )
        if send.status_code < 200 or send.status_code >= 300:
            raise BroadcastError("send", send.status_code, send.text)

        return {"broadcast_id": broadcast_id, "status": "sent"}
    finally:
        if owns_client:
            client.close()
