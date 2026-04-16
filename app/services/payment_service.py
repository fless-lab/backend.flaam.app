from __future__ import annotations

"""
Payment service — Paystack (§5.11, §16 webhooks).

Responsabilités :
- Initialize payment (simulate en dev, vrai appel Paystack en prod)
- Verify webhook (HMAC-SHA512 timing-safe)
- Process payment success → activate Subscription + set User.is_premium
- Process payment failure
- Idempotent sur provider_reference (unique en DB)

Interface abstraite BasePaymentProvider pour permettre l'ajout de
Flutterwave plus tard (pattern identique à BaseSMSProvider en S2).
Au MVP une seule implémentation concrète : PaystackProvider.

Plans :
- weekly  : 1 500 FCFA, 7 jours
- monthly : 5 000 FCFA, 30 jours

Prix effectifs pris dans City.premium_price_weekly / premium_price_monthly
(configurables par ville et devise).
"""

import hashlib
import hmac
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

import structlog
from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.models.city import City
from app.models.payment import Payment
from app.models.subscription import Subscription
from app.models.user import User

log = structlog.get_logger()
settings = get_settings()


PLAN_DURATION_DAYS: dict[str, int] = {
    "weekly": 7,
    "monthly": 30,
}


# ══════════════════════════════════════════════════════════════════════
# Provider interface (pattern BaseSMSProvider)
# ══════════════════════════════════════════════════════════════════════


class BasePaymentProvider(ABC):
    name: str

    @abstractmethod
    async def initialize(
        self,
        *,
        reference: str,
        amount_minor: int,
        currency: str,
        email: str,
        metadata: dict | None = None,
    ) -> dict:
        """Retourne au minimum {authorization_url, access_code, reference}."""
        ...


class PaystackProvider(BasePaymentProvider):
    """Implémentation Paystack (simulée en dev, réelle en prod)."""

    name = "paystack"

    async def initialize(
        self,
        *,
        reference: str,
        amount_minor: int,
        currency: str,
        email: str,
        metadata: dict | None = None,
    ) -> dict:
        if settings.paystack_simulate:
            # Mode dev : fausse URL. Le test `/subscriptions/webhook/simulate`
            # déclenche un webhook fictif.
            return {
                "authorization_url": (
                    f"{settings.frontend_base_url}/pay/simulate/{reference}"
                ),
                "access_code": f"sim_{reference}",
                "reference": reference,
                "provider": "paystack",
            }

        # Mode réel : appel httpx à Paystack. Stub non-câblé au MVP —
        # l'intégration httpx + retry + mapping d'erreurs viendra en S11.
        log.warning(
            "paystack_real_mode_not_wired",
            note="PAYSTACK_SIMULATE=false mais httpx non câblé — voir S11",
        )
        raise AppException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "payment_provider_not_wired",
        )


# Un seul provider enregistré au MVP. Flutterwave viendra plus tard via
# un objet FlutterwaveProvider(BasePaymentProvider) — pas implémenté ici.
_providers: dict[str, BasePaymentProvider] = {
    "paystack": PaystackProvider(),
}


def get_provider(name: str = "paystack") -> BasePaymentProvider:
    p = _providers.get(name)
    if p is None:
        raise AppException(
            status.HTTP_400_BAD_REQUEST, f"unknown_provider:{name}"
        )
    return p


# ══════════════════════════════════════════════════════════════════════
# Initialize
# ══════════════════════════════════════════════════════════════════════


def _plan_price(plan: str, city: City | None) -> tuple[int, str]:
    """Retourne (amount, currency) dans la devise locale."""
    if plan not in PLAN_DURATION_DAYS:
        raise AppException(status.HTTP_400_BAD_REQUEST, f"unknown_plan:{plan}")
    if city is None:
        # Fallback : XOF + prix par défaut MVP (Togo/CI)
        return (1500 if plan == "weekly" else 5000, "XOF")
    if plan == "weekly":
        return (city.premium_price_weekly, city.currency_code)
    return (city.premium_price_monthly, city.currency_code)


async def initialize_payment(
    user: User,
    *,
    plan: Literal["weekly", "monthly"],
    payment_method: str,
    provider: str = "paystack",
    db: AsyncSession,
) -> dict:
    """
    Crée un Payment en status="initialized" et retourne l'URL Paystack
    (ou fake URL si PAYSTACK_SIMULATE=true).
    """
    amount, currency = _plan_price(plan, user.city)

    reference = f"flaam_sub_{uuid4().hex[:24]}"
    payment = Payment(
        user_id=user.id,
        amount=amount,
        currency=currency,
        provider=provider,
        provider_reference=reference,
        payment_method=payment_method,
        status="initialized",
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    email = user.email or f"{user.id}@flaam.app"
    provider_impl = get_provider(provider)
    # Paystack veut le montant en "minor units" (kobo). Pour XOF il n'y
    # a pas de subdivision : on passe le montant tel quel.
    amount_minor = amount if currency == "XOF" else amount * 100

    result = await provider_impl.initialize(
        reference=reference,
        amount_minor=amount_minor,
        currency=currency,
        email=email,
        metadata={"user_id": str(user.id), "plan": plan},
    )

    payment.status = "pending"
    await db.commit()

    log.info(
        "payment_initialized",
        user_id=str(user.id),
        plan=plan,
        reference=reference,
        amount=amount,
        currency=currency,
    )

    return {
        "authorization_url": result["authorization_url"],
        "access_code": result.get("access_code"),
        "reference": reference,
        "provider": provider,
        "plan": plan,
        "amount": amount,
        "currency": currency,
    }


# ══════════════════════════════════════════════════════════════════════
# Webhook signature
# ══════════════════════════════════════════════════════════════════════


def verify_webhook(payload: bytes, signature: str | None) -> bool:
    """HMAC-SHA512 Paystack, timing-safe."""
    if not signature:
        return False
    secret = settings.paystack_webhook_secret
    if not secret:
        # En dev sans secret configuré : on refuse par défaut (tests
        # doivent set PAYSTACK_WEBHOOK_SECRET ou passer par /webhook/simulate).
        return False
    expected = hmac.new(
        secret.encode(), payload, hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ══════════════════════════════════════════════════════════════════════
# Process payment (idempotent)
# ══════════════════════════════════════════════════════════════════════


async def _get_payment_by_reference(
    reference: str, db: AsyncSession
) -> Payment | None:
    row = await db.execute(
        select(Payment).where(Payment.provider_reference == reference)
    )
    return row.scalar_one_or_none()


async def process_successful_payment(
    payment: Payment, webhook_payload: dict, db: AsyncSession
) -> Subscription:
    """
    Active/renouvelle la Subscription et passe User.is_premium=true.

    Idempotent : si payment.status == "success" déjà, no-op.
    """
    if payment.status == "success" and payment.completed_at is not None:
        log.info(
            "payment_webhook_replay_ignored",
            reference=payment.provider_reference,
        )
        # Récupère la subscription existante
        if payment.subscription_id is not None:
            sub = await db.get(Subscription, payment.subscription_id)
            if sub is not None:
                return sub
        sub_row = await db.execute(
            select(Subscription).where(Subscription.user_id == payment.user_id)
        )
        return sub_row.scalar_one()

    user = await db.get(User, payment.user_id)
    if user is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "user_not_found")

    plan = webhook_payload.get("metadata", {}).get("plan") or (
        "monthly" if payment.amount >= 3000 else "weekly"
    )
    duration = timedelta(days=PLAN_DURATION_DAYS[plan])
    now = datetime.now(timezone.utc)

    sub_row = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    )
    sub = sub_row.scalar_one_or_none()

    if sub is None:
        sub = Subscription(
            user_id=user.id,
            plan=plan,
            provider=payment.provider,
            payment_method=payment.payment_method or "momo",
            amount=payment.amount,
            currency=payment.currency,
            starts_at=now,
            expires_at=now + duration,
            is_auto_renew=True,
            is_active=True,
        )
        db.add(sub)
        await db.flush()
    else:
        # Renouvellement : extension depuis max(now, current expires_at)
        base = sub.expires_at if sub.expires_at > now else now
        sub.expires_at = base + duration
        sub.plan = plan
        sub.is_active = True
        sub.is_auto_renew = True
        sub.cancelled_at = None

    user.is_premium = True

    payment.status = "success"
    payment.subscription_id = sub.id
    payment.completed_at = now
    payment.webhook_payload = webhook_payload

    await db.commit()
    await db.refresh(sub)

    log.info(
        "payment_success",
        reference=payment.provider_reference,
        user_id=str(user.id),
        plan=plan,
        expires_at=sub.expires_at.isoformat(),
    )
    return sub


async def process_failed_payment(
    payment: Payment,
    reason: str,
    webhook_payload: dict,
    db: AsyncSession,
) -> None:
    if payment.status == "failed":
        return
    payment.status = "failed"
    payment.failure_reason = (reason or "")[:200]
    payment.completed_at = datetime.now(timezone.utc)
    payment.webhook_payload = webhook_payload
    await db.commit()
    log.info(
        "payment_failed",
        reference=payment.provider_reference,
        reason=reason,
    )


async def handle_paystack_webhook(
    event_type: str, data: dict, db: AsyncSession
) -> dict:
    """
    Router les événements Paystack. Idempotent par provider_reference.
    """
    reference = data.get("reference")
    if not reference:
        raise AppException(status.HTTP_400_BAD_REQUEST, "missing_reference")

    payment = await _get_payment_by_reference(reference, db)
    if payment is None:
        # On accepte mais on ignore les webhooks sans payment correspondant
        # (anti-replay, anti-enumération).
        log.warning("webhook_unknown_reference", reference=reference)
        return {"status": "ignored", "reason": "unknown_reference"}

    if event_type in ("charge.success", "subscription.create"):
        await process_successful_payment(payment, data, db)
        return {"status": "processed", "event": event_type}
    if event_type in ("charge.failed",):
        await process_failed_payment(
            payment, data.get("gateway_response", "failed"), data, db
        )
        return {"status": "processed", "event": event_type}

    log.info(
        "webhook_unhandled_event", event_type=event_type, reference=reference
    )
    return {"status": "ignored", "reason": "unhandled_event"}


__all__ = [
    "PLAN_DURATION_DAYS",
    "BasePaymentProvider",
    "PaystackProvider",
    "get_provider",
    "initialize_payment",
    "verify_webhook",
    "process_successful_payment",
    "process_failed_payment",
    "handle_paystack_webhook",
]
