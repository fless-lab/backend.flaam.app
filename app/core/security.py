from __future__ import annotations

"""
Primitives sécurité : JWT, hashing PIN, OTP, sanitization texte (spec §16).
"""

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()

# bcrypt uniquement pour le PIN MFA (6 chiffres). Le reste des hashes
# (phone, email) sont en SHA-256 — pas d'info secrète à protéger.
_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── JWT ──────────────────────────────────────────────────────────────

def _encode_jwt(payload: dict) -> str:
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: UUID | str, is_admin: bool = False) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_token_expire_minutes),
        "type": "access",
        "admin": is_admin,
    }
    return _encode_jwt(payload)


def create_refresh_token(user_id: UUID | str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(days=settings.jwt_refresh_token_expire_days),
        "type": "refresh",
    }
    return _encode_jwt(payload)


def decode_token(token: str) -> dict:
    """
    Décode et valide un JWT.
    Raise jose.JWTError si signature/exp invalide.
    """
    return jwt.decode(
        token, settings.secret_key, algorithms=[settings.jwt_algorithm]
    )


# ── MFA PIN (bcrypt) ─────────────────────────────────────────────────

def hash_pin(pin: str) -> str:
    if not re.fullmatch(r"\d{6}", pin):
        raise ValueError("PIN must be exactly 6 digits")
    return _pwd_ctx.hash(pin)


def verify_pin(pin: str, pin_hash: str) -> bool:
    if not re.fullmatch(r"\d{6}", pin):
        return False
    return _pwd_ctx.verify(pin, pin_hash)


# ── OTP ──────────────────────────────────────────────────────────────

def generate_otp(length: int | None = None) -> str:
    """Code OTP numérique à N chiffres (6 par défaut)."""
    n = length or settings.otp_length
    # secrets.randbelow → uniform, cryptographiquement sûr
    return "".join(str(secrets.randbelow(10)) for _ in range(n))


def generate_recovery_token() -> str:
    """Token opaque URL-safe pour email verify / recovery."""
    return secrets.token_urlsafe(32)


# ── Webhook signatures ───────────────────────────────────────────────

def verify_paystack_signature(payload: bytes, signature: str) -> bool:
    expected = hmac.new(
        settings.paystack_webhook_secret.encode(),
        payload,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── Event QR codes (MàJ 8 Porte 3) ───────────────────────────────────

def sign_event_qr(event_id: UUID | str, user_id: UUID | str) -> str:
    """
    Génère un QR code signé pour le check-in event.

    Format : "{event_id}:{user_id}:{signature_hex16}"
    La signature est un HMAC-SHA256 tronqué à 16 chars hex (64 bits)
    — assez pour empêcher un brute force online.
    """
    msg = f"{event_id}:{user_id}".encode()
    sig = hmac.new(
        settings.secret_key.encode(), msg, hashlib.sha256
    ).hexdigest()[:16]
    return f"{event_id}:{user_id}:{sig}"


def verify_event_qr(token: str) -> tuple[str, str] | None:
    """
    Vérifie un QR event. Retourne (event_id, user_id) ou None.
    Timing-safe via hmac.compare_digest.
    """
    if not token:
        return None
    parts = token.split(":")
    if len(parts) != 3:
        return None
    event_id, user_id, sig = parts
    if not sig:
        return None
    msg = f"{event_id}:{user_id}".encode()
    expected = hmac.new(
        settings.secret_key.encode(), msg, hashlib.sha256
    ).hexdigest()[:16]
    if not hmac.compare_digest(expected, sig):
        return None
    return event_id, user_id


def qr_code_hash(token: str) -> str:
    """SHA-256 hex du QR token, pour stockage/idempotence en base."""
    return hashlib.sha256(token.encode()).hexdigest()


# ── Sanitization texte (spec §16) ────────────────────────────────────

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HTML_RE = re.compile(r"<[^>]+>")
_NAME_INVALID_RE = re.compile(r"[0-9@#$%^&*()_+=\[\]{};:\"\\|<>?/~`]")


def sanitize_text(text: str, max_length: int = 500) -> str:
    text = text.strip()[:max_length]
    text = _CONTROL_CHARS_RE.sub("", text)
    text = _HTML_RE.sub("", text)
    return text


def validate_display_name(name: str) -> str:
    name = sanitize_text(name, max_length=50)
    if len(name) < 2:
        raise ValueError("Name must be at least 2 characters")
    if _NAME_INVALID_RE.search(name):
        raise ValueError("Name contains invalid characters")
    return name


__all__ = [
    "JWTError",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "hash_pin",
    "verify_pin",
    "generate_otp",
    "generate_recovery_token",
    "verify_paystack_signature",
    "sign_event_qr",
    "verify_event_qr",
    "qr_code_hash",
    "sanitize_text",
    "validate_display_name",
]
