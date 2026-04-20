"""resend_broadcast: a tiny, project-agnostic wrapper around Resend's
Broadcasts API. Given an HTML body, subject, from address, and audience ID,
creates a broadcast and sends it.

This module does not know or care where the HTML came from. It is designed
to be lifted into any Python project that wants to send newsletters through
Resend without depending on Resend's own SDK.
"""

from __future__ import annotations

from .client import BroadcastError, send_broadcast

__all__ = ["send_broadcast", "BroadcastError"]
