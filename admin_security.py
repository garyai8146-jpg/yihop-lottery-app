"""Minimal fail-closed admin PIN checks for the local lottery app.

The PIN is supplied by the deployment environment, never committed to Git.
This is an application-level gate, not a substitute for an external identity
provider, network access control or rate limiting in a public deployment.
"""

from __future__ import annotations

import hashlib
import hmac


MIN_ADMIN_PIN_LENGTH = 12


def pin_is_configured(pin: str | None) -> bool:
    """Accept only a reasonably long, non-trivial operator-supplied secret."""
    if not isinstance(pin, str) or len(pin) < MIN_ADMIN_PIN_LENGTH:
        return False
    if pin != pin.strip() or len(set(pin)) < 4:
        return False
    common = ("123456", "password", "admin", "qwerty", "000000", "111111")
    return not any(item in pin.lower() for item in common)


def pin_fingerprint(pin: str) -> str:
    return hashlib.sha256(pin.encode("utf-8")).hexdigest()


def verify_admin_pin(candidate: str, configured_pin: str | None) -> bool:
    if not pin_is_configured(configured_pin) or not isinstance(candidate, str):
        return False
    return hmac.compare_digest(
        pin_fingerprint(candidate),
        pin_fingerprint(configured_pin),
    )


def session_is_authorized(fingerprint: str | None, configured_pin: str | None) -> bool:
    if not pin_is_configured(configured_pin) or not isinstance(fingerprint, str):
        return False
    return hmac.compare_digest(fingerprint, pin_fingerprint(configured_pin))
