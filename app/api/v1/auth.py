from __future__ import annotations

"""
Routes Auth (§5.1 + Auth sans mot de passe).

MVP couvert :
- /auth/otp/request, /auth/otp/resend, /auth/otp/verify
- /auth/refresh, /auth/logout
- /auth/account (DELETE)
- /auth/email/add, /auth/email/verify
- /auth/recovery/request, /auth/recovery/confirm, /auth/recovery/complete
- /auth/mfa/enable, /auth/mfa/verify, /auth/mfa/disable
- /auth/phone/change/verify-old, /auth/phone/change/set-new

Les endpoints recovery/email/MFA/phone-change sont posés avec des stubs
501 quand leur implémentation complète dépend d'autres services
(email sender, phone-change tokens). Session 2 pose l'ossature et le
flux OTP principal ; les flux complémentaires seront étoffés dans les
sessions suivantes (commentaire TODO explicite avec réf session).
"""

from datetime import datetime, timezone

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import get_current_user, get_db, get_redis
from app.core.exceptions import AppException
from app.core.security import (
    create_access_token,
    create_refresh_token,
    generate_recovery_token,
    hash_pin,
    verify_pin,
)
from app.models.user import User
from app.schemas.auth import (
    AddEmailBody,
    AuthTokenResponse,
    DeleteAccountBody,
    MfaPinBody,
    OtpRequestBody,
    OtpResendBody,
    OtpResponse,
    OtpVerifyBody,
    RecoveryCompleteBody,
    RecoveryConfirmBody,
    RecoveryRequestBody,
    RefreshTokenBody,
    SetNewPhoneBody,
    SimpleMessage,
    VerifyEmailBody,
)
from app.services import auth_service
from app.services.abuse_prevention_service import update_history_on_deletion
from app.tasks.cleanup_tasks import purge_account_data
from app.utils.phone import (
    InvalidPhoneError,
    country_code_from_phone,
    hash_phone,
    normalize_phone,
)

settings = get_settings()

log = structlog.get_logger()
router = APIRouter(prefix="/auth", tags=["auth"])


# ── OTP ──────────────────────────────────────────────────────────────

@router.post("/otp/request", response_model=OtpResponse)
async def otp_request(
    body: OtpRequestBody,
    redis: aioredis.Redis = Depends(get_redis),
) -> OtpResponse:
    result = await auth_service.request_otp(body.phone, redis, channel="sms")
    return OtpResponse(**result)


@router.post("/otp/resend", response_model=OtpResponse)
async def otp_resend(
    body: OtpResendBody,
    redis: aioredis.Redis = Depends(get_redis),
) -> OtpResponse:
    """
    Renvoi de l'OTP via un canal alternatif (WhatsApp typiquement).
    Proposé côté client après ~30 s sans réception SMS.
    """
    result = await auth_service.request_otp(body.phone, redis, channel=body.channel)
    return OtpResponse(**result)


@router.post("/otp/verify", response_model=AuthTokenResponse)
async def otp_verify(
    body: OtpVerifyBody,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> AuthTokenResponse:
    result = await auth_service.verify_otp(
        phone=body.phone,
        code=body.code,
        device_fingerprint=body.device_fingerprint,
        platform=body.platform,
        app_version=body.app_version,
        os_version=body.os_version,
        db=db,
        redis=redis,
    )
    return AuthTokenResponse(**result)


@router.post("/refresh", response_model=AuthTokenResponse)
async def refresh(
    body: RefreshTokenBody,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> AuthTokenResponse:
    result = await auth_service.refresh_access_token(body.refresh_token, db, redis)
    return AuthTokenResponse(**result)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: RefreshTokenBody,
    redis: aioredis.Redis = Depends(get_redis),
    _user: User = Depends(get_current_user),
) -> Response:
    await auth_service.logout(body.refresh_token, redis)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Account deletion (soft delete, §17 RGPD) ─────────────────────────

@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    body: DeleteAccountBody,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    user: User = Depends(get_current_user),
) -> Response:
    """
    Soft delete (§17 RGPD) :
    1. Marque User.is_deleted + deleted_at
    2. Met à jour AccountHistory (total_accounts_deleted, last_departure_reason…)
    3. Révoque tous les refresh tokens de l'user dans Redis
    4. Planifie la tâche Celery de purge RGPD (stub pour l'instant)
    """
    if not body.confirm:
        raise AppException(
            status.HTTP_400_BAD_REQUEST,
            "confirm must be true to delete account",
        )

    reason = body.reason or "user_deleted"
    now = datetime.now(timezone.utc)

    user.is_deleted = True
    user.deleted_at = now
    user.is_active = False
    user.is_visible = False

    device_fp = user.devices[0].device_fingerprint if user.devices else None

    await update_history_on_deletion(user, reason, device_fp, db)
    await db.commit()

    await auth_service.revoke_all_user_tokens(str(user.id), redis)
    await purge_account_data(user.id, reason)

    log.info("account_delete_requested", user_id=str(user.id), reason=reason)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Email (ajout + vérification) ─────────────────────────────────────

@router.post("/email/add", response_model=SimpleMessage)
async def email_add(
    body: AddEmailBody,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    user: User = Depends(get_current_user),
) -> SimpleMessage:
    user.email = body.email.lower()
    user.is_email_verified = False
    user.email_verified_at = None
    token = generate_recovery_token()
    await redis.set(
        f"email:verify:{token}",
        str(user.id),
        ex=60 * 60 * 24,  # 24 h
    )
    await db.commit()
    # TODO(Session 8 — Notifications) : envoyer l'email contenant `token`
    # via le service mail (SES/Resend). Pour l'instant on loggue.
    log.info("email_verify_token_issued", user_id=str(user.id), token=token)
    return SimpleMessage(message="Verification email sent")


@router.post("/email/verify", response_model=SimpleMessage)
async def email_verify(
    body: VerifyEmailBody,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> SimpleMessage:
    key = f"email:verify:{body.token}"
    user_id = await redis.get(key)
    if not user_id:
        raise AppException(status.HTTP_400_BAD_REQUEST, "invalid_or_expired_token")

    user = await db.get(User, user_id)
    if user is None:
        raise AppException(status.HTTP_400_BAD_REQUEST, "invalid_or_expired_token")

    user.is_email_verified = True
    user.email_verified_at = datetime.now(timezone.utc)
    user.recovery_email = user.recovery_email or user.email
    await redis.delete(key)
    await db.commit()
    return SimpleMessage(message="Email verified")


# ── Recovery (numéro perdu, §Auth sans mot de passe) ─────────────────

@router.post("/recovery/request", response_model=SimpleMessage)
async def recovery_request(
    body: RecoveryRequestBody,
    redis: aioredis.Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
) -> SimpleMessage:
    """
    Envoi d'un lien de récupération. On retourne 200 quoi qu'il en soit
    (anti énumération). Le lien expire en 1 h.
    """
    normalized = body.email.lower()
    result = await db.execute(select(User).where(User.recovery_email == normalized))
    user = result.scalar_one_or_none()
    if user is None:
        result = await db.execute(select(User).where(User.email == normalized))
        user = result.scalar_one_or_none()

    if user and user.is_email_verified:
        token = generate_recovery_token()
        await redis.set(f"recovery:{token}", str(user.id), ex=60 * 60)
        # TODO(Session 8) : envoyer le lien `flaam://recovery?token={token}`
        log.info("recovery_token_issued", user_id=str(user.id), token=token)

    return SimpleMessage(message="If the email exists, a recovery link has been sent")


@router.post("/recovery/confirm", response_model=OtpResponse)
async def recovery_confirm(
    body: RecoveryConfirmBody,
    redis: aioredis.Redis = Depends(get_redis),
) -> OtpResponse:
    """
    L'utilisateur a cliqué le lien reçu par email et soumet son nouveau
    numéro. On déclenche un OTP vers ce nouveau numéro.
    """
    user_id = await redis.get(f"recovery:{body.recovery_token}")
    if not user_id:
        raise AppException(status.HTTP_400_BAD_REQUEST, "invalid_or_expired_token")

    # On attache le nouveau numéro au token pour le matcher au complete
    await redis.set(
        f"recovery:new_phone:{body.recovery_token}",
        body.new_phone,
        ex=60 * 60,
    )
    result = await auth_service.request_otp(body.new_phone, redis, channel="sms")
    return OtpResponse(**result)


@router.post("/recovery/complete", response_model=AuthTokenResponse)
async def recovery_complete(
    body: RecoveryCompleteBody,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> AuthTokenResponse:
    user_id = await redis.get(f"recovery:{body.recovery_token}")
    new_phone = await redis.get(f"recovery:new_phone:{body.recovery_token}")
    if not user_id or not new_phone:
        raise AppException(status.HTTP_400_BAD_REQUEST, "invalid_or_expired_token")

    try:
        normalized = normalize_phone(new_phone)
    except InvalidPhoneError as e:
        raise AppException(status.HTTP_400_BAD_REQUEST, str(e))

    phash = hash_phone(normalized)
    stored = await redis.get(f"otp:{phash}")
    if stored is None or stored != body.otp:
        raise AppException(status.HTTP_401_UNAUTHORIZED, "invalid_otp")

    user = await db.get(User, user_id)
    if user is None:
        raise AppException(status.HTTP_400_BAD_REQUEST, "invalid_or_expired_token")

    user.phone_hash = phash
    user.phone_country_code = country_code_from_phone(normalized)
    user.is_phone_verified = True

    await redis.delete(f"recovery:{body.recovery_token}")
    await redis.delete(f"recovery:new_phone:{body.recovery_token}")
    await redis.delete(f"otp:{phash}")

    await db.commit()
    return AuthTokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        is_new_user=False,
        user_id=user.id,
    )


# ── MFA (PIN 6 chiffres) ─────────────────────────────────────────────

@router.post("/mfa/enable", response_model=SimpleMessage)
async def mfa_enable(
    body: MfaPinBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SimpleMessage:
    user.mfa_pin_hash = hash_pin(body.pin)
    user.mfa_enabled = True
    await db.commit()
    return SimpleMessage(message="MFA enabled")


@router.post("/mfa/verify", response_model=SimpleMessage)
async def mfa_verify(
    body: MfaPinBody,
    user: User = Depends(get_current_user),
) -> SimpleMessage:
    if not user.mfa_enabled or not user.mfa_pin_hash:
        raise AppException(status.HTTP_400_BAD_REQUEST, "mfa_not_enabled")
    if not verify_pin(body.pin, user.mfa_pin_hash):
        raise AppException(status.HTTP_401_UNAUTHORIZED, "invalid_pin")
    return SimpleMessage(message="MFA verified")


@router.post("/mfa/disable", response_model=SimpleMessage)
async def mfa_disable(
    body: MfaPinBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SimpleMessage:
    if not user.mfa_enabled or not user.mfa_pin_hash:
        raise AppException(status.HTTP_400_BAD_REQUEST, "mfa_not_enabled")
    if not verify_pin(body.pin, user.mfa_pin_hash):
        raise AppException(status.HTTP_401_UNAUTHORIZED, "invalid_pin")
    user.mfa_enabled = False
    user.mfa_pin_hash = None
    await db.commit()
    return SimpleMessage(message="MFA disabled")


# ── Phone change ─────────────────────────────────────────────────────

@router.post("/phone/change/verify-old", response_model=SimpleMessage)
async def phone_change_verify_old(
    body: OtpVerifyBody,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
    user: User = Depends(get_current_user),
) -> SimpleMessage:
    """
    Étape 1 : vérifier l'ancien numéro avant d'en changer. Émet un
    change_token stocké dans Redis (15 min) qui autorisera l'étape 2.
    """
    try:
        normalized = normalize_phone(body.phone)
    except InvalidPhoneError as e:
        raise AppException(status.HTTP_400_BAD_REQUEST, str(e))

    if hash_phone(normalized) != user.phone_hash:
        raise AppException(status.HTTP_400_BAD_REQUEST, "phone_mismatch")

    stored = await redis.get(f"otp:{user.phone_hash}")
    if stored is None or stored != body.code:
        raise AppException(status.HTTP_401_UNAUTHORIZED, "invalid_otp")

    await redis.delete(f"otp:{user.phone_hash}")
    token = generate_recovery_token()
    await redis.set(f"phone_change:{token}", str(user.id), ex=15 * 60)
    return SimpleMessage(message=token)


@router.post("/phone/change/set-new", response_model=AuthTokenResponse)
async def phone_change_set_new(
    body: SetNewPhoneBody,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> AuthTokenResponse:
    user_id = await redis.get(f"phone_change:{body.change_token}")
    if not user_id:
        raise AppException(status.HTTP_400_BAD_REQUEST, "invalid_or_expired_token")

    try:
        normalized = normalize_phone(body.new_phone)
    except InvalidPhoneError as e:
        raise AppException(status.HTTP_400_BAD_REQUEST, str(e))

    new_phash = hash_phone(normalized)
    stored = await redis.get(f"otp:{new_phash}")
    if stored is None or stored != body.otp:
        raise AppException(status.HTTP_401_UNAUTHORIZED, "invalid_otp")

    user = await db.get(User, user_id)
    if user is None:
        raise AppException(status.HTTP_400_BAD_REQUEST, "invalid_user")

    user.phone_hash = new_phash
    user.phone_country_code = country_code_from_phone(normalized)
    user.is_phone_verified = True

    await redis.delete(f"phone_change:{body.change_token}")
    await redis.delete(f"otp:{new_phash}")

    await db.commit()
    return AuthTokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        is_new_user=False,
        user_id=user.id,
    )


__all__ = ["router"]
