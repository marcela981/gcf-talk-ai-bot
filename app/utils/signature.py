"""HMAC signature verification for Talk bot webhooks.

The `atalk_bot_msg` FastAPI dependency in `nc_py_api.ex_app` already performs
this verification automatically on the webhook route. This module exists to:

  1. Document the verification scheme so reviewers and auditors can read it.
  2. Provide a standalone verifier for unit tests and manual debugging
     (e.g., replaying a captured webhook outside the FastAPI app).

Verification scheme (per the Nextcloud Talk Bot API):

    signature = HMAC_SHA256(
        key     = bot_shared_secret,
        message = random_header + raw_request_body,
    ).hexdigest()      # lowercase hex

Two headers travel with each webhook:

    X-Nextcloud-Talk-Random     a per-request random string
    X-Nextcloud-Talk-Signature  lowercase hex digest of the HMAC above

Always compare with `hmac.compare_digest` to avoid timing leaks.
"""
from __future__ import annotations

import hashlib
import hmac


def compute_signature(secret: str, random_header: str, body: bytes) -> str:
    """Compute the expected HMAC-SHA256 hex digest for a webhook."""
    mac = hmac.new(
        key=secret.encode("utf-8"),
        msg=random_header.encode("utf-8") + body,
        digestmod=hashlib.sha256,
    )
    return mac.hexdigest()


def verify_signature(
    *,
    secret: str,
    random_header: str,
    body: bytes,
    provided_signature: str,
) -> bool:
    """Constant-time-compare a provided signature against the expected one."""
    expected = compute_signature(secret, random_header, body)
    return hmac.compare_digest(expected, provided_signature.lower())
