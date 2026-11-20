"""Time-based one-time passwords (RFC 6238) for authenticator apps. No outside library needed."""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30
DIGITS = 6


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode()


def _code(secret, counter):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 10**DIGITS:0{DIGITS}d}"


def verify(secret, code, now=None, window=1, after_step=0):
    """Return the matching time step (so it can be remembered and never used twice), or None."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    step_now = int((now if now is not None else time.time()) // STEP)
    for step in range(step_now - window, step_now + window + 1):
        if step > after_step and hmac.compare_digest(_code(secret, step), code):
            return step
    return None


def provisioning_uri(secret, username, issuer):
    return f"otpauth://totp/{quote(issuer)}:{quote(username)}?secret={secret}&issuer={quote(issuer)}"
