"""Minimal login: PBKDF2 password hashing + HMAC-signed session cookie (stdlib only)."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

SECRET = os.environ.get("BIZPILOT_SECRET", "dev-secret-change-me").encode()
SESSION_TTL = 60 * 60 * 12
COOKIE = "bizpilot_session"


def hash_password(password: str, salt: str | None = None):
    salt = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 60_000).hex()
    return salt, h


def verify_password(password: str, salt: str, expected: str) -> bool:
    return hmac.compare_digest(hash_password(password, salt)[1], expected)


def make_token(email: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"e": email, "x": int(time.time()) + SESSION_TTL}).encode()).decode().rstrip("=")
    sig = hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def read_token(token: str | None):
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest(), sig):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode() + b"=" * (-len(payload) % 4)))
    except Exception:
        return None
    return data["e"] if data["x"] > time.time() else None
