from __future__ import annotations

import hashlib
import hmac
import time


SIGNATURE_TOLERANCE_SECONDS = 60 * 5


def verify_slack_signature(
    *,
    signing_secret: str,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    now: int | None = None,
) -> bool:
    if not timestamp or not signature:
        return False

    try:
        request_time = int(timestamp)
    except ValueError:
        return False

    current_time = int(now if now is not None else time.time())
    if abs(current_time - request_time) > SIGNATURE_TOLERANCE_SECONDS:
        return False

    base_string = b"v0:" + timestamp.encode("utf-8") + b":" + body
    digest = hmac.new(signing_secret.encode("utf-8"), base_string, hashlib.sha256).hexdigest()
    expected_signature = f"v0={digest}"
    return hmac.compare_digest(expected_signature, signature)
