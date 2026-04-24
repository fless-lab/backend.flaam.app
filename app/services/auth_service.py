from __future__ import annotations

"""
Auth service — OTP, JWT, création de compte avec anti-abus (§5.1, §16, §30).

- request_otp(phone, channel): rate limit + génère code + envoie via SMSDeliveryService
- verify_otp(phone, code, device_fp, ...): vérifie, crée user si nouveau, applique restrictions
- refresh_token(refresh): renouvelle l'access token
"""

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

import redis.asyncio as aioredis
import structlog
from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import FlaamError
from app.core.exceptions import AppException
from app.core.security import (
    JWTError,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_otp,
)
from app.models.account_history import AccountHistory
from app.models.device import Device
from app.models.user import User
from app.services.abuse_prevention_service import (
    calculate_restrictions,
    find_history_by_device,
    find_history_by_phone,
)
from app.utils.phone import (
    InvalidPhoneError,
    country_code_from_phone,
    hash_phone,
    normalize_phone,
)
from app.utils.sms import sms_service

log = structlog.get_logger()
settings = get_settings()


# ── Redis keys ──────────────────────────────────────────────────────

def _otp_key(phone_hash: str) -> str:
    return f"otp:{phone_hash}"


def _otp_rate_key(phone_hash: str) -> str:
    # Sliding window via Redis list (spec §15 — implémentation légère)
    return f"otp:rate:{phone_hash}"


def _otp_attempts_key(phone_hash: str) -> str:
    return f"otp:attempts:{phone_hash}"


def _refresh_blacklist_key(jti: str) -> str:
    return f"refresh:blacklist:{jti}"


def _user_revoked_key(user_id: str) -> str:
    """Clé de révocation globale — set à la suppression de compte."""
    return f"user:revoked:{user_id}"


async def revoke_all_user_tokens(user_id: str, redis: aioredis.Redis) -> None:
    """
    Invalide tous les refresh tokens d'un user (suppression de compte).
    TTL = durée de vie max d'un refresh token.
    """
    ttl = settings.jwt_refresh_token_expire_days * 86400
    await redis.set(_user_revoked_key(user_id), "1", ex=ttl)


# ── OTP request ─────────────────────────────────────────────────────

async def _check_otp_rate_limit(
    phone_hash: str, redis: aioredis.Redis
) -> tuple[bool, int]:
    """
    Sliding window : max N tentatives par fenêtre. Retourne (allowed, retry_after).
    Stockage : Redis list avec les timestamps (secondes epoch) dans la fenêtre.
    """
    now = int(datetime.now(timezone.utc).timestamp())
    window = settings.rate_limit_otp_window_seconds
    limit = settings.rate_limit_otp_per_window
    key = _otp_rate_key(phone_hash)

    # Purge des entrées hors fenêtre + compte
    await redis.zremrangebyscore(key, 0, now - window)
    count = await redis.zcard(key)
    if count >= limit:
        # Calculer retry_after : plus ancien + window - now
        oldest = await redis.zrange(key, 0, 0, withscores=True)
        retry_after = int(oldest[0][1]) + window - now if oldest else window
        return False, max(1, retry_after)

    await redis.zadd(key, {f"{now}-{count}": now})
    await redis.expire(key, window)
    return True, 0


async def request_otp(
    phone: str,
    redis: aioredis.Redis,
    channel: Literal["sms", "whatsapp"] = "sms",
    lang: str = "fr",
) -> dict:
    try:
        normalized = normalize_phone(phone)
    except InvalidPhoneError as e:
        raise AppException(status.HTTP_400_BAD_REQUEST, str(e))

    phash = hash_phone(normalized)

    allowed, retry_after = await _check_otp_rate_limit(phash, redis)
    if not allowed:
        raise FlaamError(
            "otp_rate_limited", 429, lang, retry_after=retry_after
        )

    code = generate_otp()
    await redis.set(_otp_key(phash), code, ex=settings.otp_expire_seconds)
    await redis.delete(_otp_attempts_key(phash))

    try:
        delivery = await sms_service.send_otp(normalized, code, channel)
    except Exception as exc:  # noqa: BLE001 — on veut remonter l'échec
        log.warning("otp_delivery_failed", phone=normalized, error=str(exc))
        raise AppException(
            status.HTTP_502_BAD_GATEWAY,
            "OTP delivery failed. Please try again.",
        )

    log.info(
        "otp_requested",
        phone_hash=phash[:12],
        channel=channel,
        provider=delivery.get("provider"),
    )

    return {
        "message": "OTP sent" + (" via WhatsApp" if channel == "whatsapp" else ""),
        "channel": channel,
        "expires_in": settings.otp_expire_seconds,
        "retry_after": settings.otp_cooldown_seconds,
    }


# ── OTP verify + user creation ──────────────────────────────────────

async def _register_device(
    user: User,
    device_fp: str | None,
    platform: str | None,
    app_version: str | None,
    os_version: str | None,
    db: AsyncSession,
) -> None:
    if not device_fp:
        return
    result = await db.execute(
        select(Device).where(
            Device.user_id == user.id,
            Device.device_fingerprint == device_fp,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.last_login_at = datetime.now(timezone.utc)
        if platform:
            existing.platform = platform
        if app_version:
            existing.app_version = app_version
        if os_version:
            existing.os_version = os_version
        return
    db.add(
        Device(
            user_id=user.id,
            device_fingerprint=device_fp,
            platform=platform or "android",
            app_version=app_version,
            os_version=os_version,
        )
    )


async def verify_otp(
    *,
    phone: str,
    code: str,
    device_fingerprint: str | None,
    platform: str | None,
    app_version: str | None,
    os_version: str | None,
    db: AsyncSession,
    redis: aioredis.Redis,
    lang: str = "fr",
    invite_code: str | None = None,
) -> dict:
    try:
        normalized = normalize_phone(phone)
    except InvalidPhoneError as e:
        raise AppException(status.HTTP_400_BAD_REQUEST, str(e))

    phash = hash_phone(normalized)

    stored = await redis.get(_otp_key(phash))
    if stored is None:
        raise FlaamError("otp_expired", 401, lang)

    attempts = await redis.incr(_otp_attempts_key(phash))
    await redis.expire(_otp_attempts_key(phash), settings.otp_expire_seconds)
    if attempts > settings.otp_max_attempts:
        await redis.delete(_otp_key(phash))
        raise FlaamError("otp_max_attempts", 429, lang)

    if stored != code:
        remaining = max(0, settings.otp_max_attempts - attempts)
        raise FlaamError("otp_invalid", 401, lang, remaining=remaining)

    # Code correct — on consomme
    await redis.delete(_otp_key(phash))
    await redis.delete(_otp_attempts_key(phash))

    # User existant ?
    result = await db.execute(select(User).where(User.phone_hash == phash))
    user = result.scalar_one_or_none()

    is_new_user = user is None
    restriction_info: dict | None = None

    if is_new_user:
        # Anti-abus : chercher un historique (§30)
        history = await find_history_by_phone(phash, db)
        if history is None and device_fingerprint:
            history = await find_history_by_device(device_fingerprint, db)

        restrictions = calculate_restrictions(history) if history else None
        if restrictions and not restrictions["allowed"]:
            raise AppException(
                status.HTTP_403_FORBIDDEN,
                f"account_creation_blocked:{restrictions['restriction']}:"
                f"{restrictions.get('reason') or ''}",
            )

        user = User(
            phone_hash=phash,
            phone_country_code=country_code_from_phone(normalized),
            is_phone_verified=True,
            account_created_count=(
                history.total_accounts_created + 1 if history else 1
            ),
            onboarding_step="city_selection",
        )
        db.add(user)

        if history:
            history.total_accounts_created += 1
            history.last_account_created_at = datetime.now(timezone.utc)
            if device_fingerprint and device_fingerprint not in history.device_fingerprints:
                history.device_fingerprints = [
                    *history.device_fingerprints,
                    device_fingerprint,
                ]
            if restrictions:
                history.risk_score = restrictions["risk_score"]
                history.current_restriction = restrictions["restriction"]
                history.restriction_expires_at = restrictions.get(
                    "restriction_expires_at"
                )
                restriction_info = {
                    "restriction": restrictions["restriction"],
                    "reason": restrictions.get("reason"),
                }
        else:
            db.add(
                AccountHistory(
                    phone_hash=phash,
                    device_fingerprints=[device_fingerprint] if device_fingerprint else [],
                )
            )

    else:
        user.is_phone_verified = True
        user.last_active_at = datetime.now(timezone.utc)

    # MàJ 8 — Porte 3 : détection ghost user.
    # Si un user existant est ghost/pre_registered, c'est sa première
    # connexion dans l'app → on promeut à "city_selection" et on construit
    # le payload ghost_data.
    is_ghost_conversion = False
    ghost_data: dict | None = None
    if (
        not is_new_user
        and user is not None
        and user.onboarding_step in ("ghost", "pre_registered")
    ):
        from app.services.event_preregistration_service import (
            build_ghost_conversion_payload,
            promote_ghost_on_conversion,
        )

        is_ghost_conversion = True
        ghost_data = await build_ghost_conversion_payload(user, db)
        await promote_ghost_on_conversion(user, db)

    # Flush pour obtenir l'id (si nouveau user)
    await db.flush()

    await _register_device(
        user, device_fingerprint, platform, app_version, os_version, db
    )

    # Invite code (silent fail). Pour les nouveaux users uniquement :
    # si fourni et valide, marque le code comme used + place l'user en
    # waitlist "activated" (bypass) + set onboarding_source="invite".
    # En cas d'erreur (code invalide/expiré/utilisé) on ignore — la
    # création du compte n'est jamais bloquée par un mauvais code.
    invite_redeemed = False
    if is_new_user and invite_code:
        try:
            from app.services import invite_service

            await invite_service.redeem_code(invite_code, user, db)
            user.onboarding_source = "invite"
            invite_redeemed = True
            log.info(
                "invite_code_redeemed_at_signup",
                user_id=str(user.id),
                code=invite_code,
            )
        except Exception as exc:
            log.info(
                "invite_code_redeem_skipped",
                user_id=str(user.id),
                code=invite_code,
                reason=str(exc),
            )

    # MFA : si activé, on émet des tokens limités — le client devra
    # appeler /auth/mfa/verify avec le PIN avant d'accéder aux routes
    # protégées. Pour l'MVP on signale mfa_required dans la réponse et
    # on émet quand même les tokens (simplicité).
    mfa_required = bool(user.mfa_enabled and not is_new_user)

    access = create_access_token(user.id)
    refresh = create_refresh_token(user.id)

    await db.commit()

    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": settings.jwt_access_token_expire_minutes * 60,
        "is_new_user": is_new_user,
        "user_id": user.id,
        "onboarding_step": user.onboarding_step,
        "restriction": restriction_info["restriction"] if restriction_info else None,
        "mfa_required": mfa_required,
        "is_ghost_conversion": is_ghost_conversion,
        "ghost_data": ghost_data,
        "invite_redeemed": invite_redeemed,
    }


# ── Refresh ─────────────────────────────────────────────────────────

async def refresh_access_token(
    refresh_token: str,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> dict:
    try:
        payload = decode_token(refresh_token)
    except JWTError:
        raise AppException(status.HTTP_401_UNAUTHORIZED, "invalid_refresh_token")

    if payload.get("type") != "refresh":
        raise AppException(status.HTTP_401_UNAUTHORIZED, "invalid_refresh_token")

    sub = payload.get("sub")
    if not sub:
        raise AppException(status.HTTP_401_UNAUTHORIZED, "invalid_refresh_token")

    # Blacklist check (logout révoque le refresh ; delete account révoque tous)
    blacklisted = await redis.get(_refresh_blacklist_key(refresh_token))
    if blacklisted:
        raise AppException(status.HTTP_401_UNAUTHORIZED, "revoked_refresh_token")

    if await redis.get(_user_revoked_key(sub)):
        raise AppException(status.HTTP_401_UNAUTHORIZED, "user_revoked")

    try:
        user_id = UUID(sub)
    except ValueError:
        raise AppException(status.HTTP_401_UNAUTHORIZED, "invalid_refresh_token")

    user = await db.get(User, user_id)
    if (
        user is None
        or not user.is_active
        or user.is_banned
        or user.is_deleted
    ):
        raise AppException(status.HTTP_401_UNAUTHORIZED, "user_inactive")

    return {
        "access_token": create_access_token(user.id),
        "refresh_token": create_refresh_token(user.id),
        "token_type": "bearer",
        "expires_in": settings.jwt_access_token_expire_minutes * 60,
        "is_new_user": False,
        "user_id": user.id,
    }


async def logout(refresh_token: str, redis: aioredis.Redis) -> None:
    """
    Blackliste le refresh token jusqu'à son expiration naturelle.
    L'access token (15 min) expire naturellement, on n'a pas besoin de
    maintenir une blacklist par-dessus (TTL court).
    """
    try:
        payload = decode_token(refresh_token)
    except JWTError:
        # Token déjà pourri — rien à faire, on retourne 204 pacifiquement
        return
    exp = payload.get("exp")
    if not exp:
        return
    ttl = max(1, int(exp - datetime.now(timezone.utc).timestamp()))
    await redis.set(_refresh_blacklist_key(refresh_token), "1", ex=ttl)


__all__ = ["request_otp", "verify_otp", "refresh_access_token", "logout"]
