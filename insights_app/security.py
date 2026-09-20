from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any


PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 310_000


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str, *, salt: str | None = None, iterations: int = PASSWORD_ITERATIONS) -> str:
    if not password:
        raise ValueError("Password cannot be empty.")
    salt = salt or secrets.token_urlsafe(18)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    encoded = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"{PASSWORD_ALGORITHM}${iterations}${salt}${encoded}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt, digest = encoded.split("$", 3)
        iterations = int(iterations_text)
    except ValueError:
        return False
    if algorithm != PASSWORD_ALGORITHM or not digest:
        return False
    expected = hash_password(password, salt=salt, iterations=iterations).split("$", 3)[3]
    return hmac.compare_digest(expected, digest)


def secure_token(bytes_count: int = 32) -> str:
    return secrets.token_urlsafe(bytes_count)


def sign_value(secret_key: str, value: str, *, max_age_seconds: int | None = None) -> str:
    timestamp = str(int(time.time()))
    payload = f"{value}.{timestamp}"
    signature = hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{payload}.{encoded_signature}"


def verify_signed_value(secret_key: str, signed: str, *, max_age_seconds: int | None = None) -> str | None:
    try:
        value, timestamp_text, signature = signed.rsplit(".", 2)
        int_timestamp = int(timestamp_text)
    except ValueError:
        return None
    if max_age_seconds is not None and time.time() - int_timestamp > max_age_seconds:
        return None
    payload = f"{value}.{timestamp_text}"
    expected = hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    expected_signature = base64.urlsafe_b64encode(expected).decode("ascii").rstrip("=")
    if not hmac.compare_digest(expected_signature, signature):
        return None
    return value


def create_session(conn: Any, user_id: int, role: str, *, ip: str, user_agent: str, hours: int) -> tuple[str, str]:
    token = secure_token(32)
    csrf = secure_token(24)
    expires_at = iso(utcnow() + timedelta(hours=hours))
    conn.execute(
        """
        INSERT INTO sessions(token_hash, csrf_token, user_id, role, ip_hash, user_agent_hash, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (stable_hash(token), csrf, user_id, role, stable_hash(ip or ""), stable_hash(user_agent or ""), expires_at),
    )
    return token, csrf


def load_session(conn: Any, token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    row = conn.execute(
        """
        SELECT sessions.*, users.username, users.active, users.learning_experience_id
        FROM sessions
        JOIN users ON users.id = sessions.user_id
        WHERE sessions.token_hash = ?
        """,
        (stable_hash(token),),
    ).fetchone()
    if not row or not row["active"]:
        return None
    if parse_iso(row["expires_at"]) <= utcnow():
        conn.execute("DELETE FROM sessions WHERE id = ?", (row["id"],))
        return None
    return dict(row)


def destroy_session(conn: Any, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (stable_hash(token),))


def participant_action_token(secret_key: str, participant_id: int, token_hash: str) -> str:
    return sign_value(secret_key, f"participant:{participant_id}:{token_hash}", max_age_seconds=None)


def verify_participant_action_token(secret_key: str, signed: str, participant_id: int, token_hash: str) -> bool:
    value = verify_signed_value(secret_key, signed, max_age_seconds=8 * 60 * 60)
    return hmac.compare_digest(value or "", f"participant:{participant_id}:{token_hash}")


def check_rate_limit(conn: Any, *, bucket: str, identity: str, limit: int, window_seconds: int) -> bool:
    now = int(time.time())
    window_start = now - (now % window_seconds)
    identity_hash = stable_hash(identity)
    conn.execute(
        "DELETE FROM rate_limits WHERE bucket = ? AND window_start < ?",
        (bucket, window_start - window_seconds),
    )
    row = conn.execute(
        "SELECT count FROM rate_limits WHERE bucket = ? AND identity_hash = ? AND window_start = ?",
        (bucket, identity_hash, window_start),
    ).fetchone()
    if row and row["count"] >= limit:
        return False
    if row:
        conn.execute(
            "UPDATE rate_limits SET count = count + 1 WHERE bucket = ? AND identity_hash = ? AND window_start = ?",
            (bucket, identity_hash, window_start),
        )
    else:
        conn.execute(
            "INSERT INTO rate_limits(bucket, identity_hash, window_start, count) VALUES (?, ?, ?, 1)",
            (bucket, identity_hash, window_start),
        )
    return True
