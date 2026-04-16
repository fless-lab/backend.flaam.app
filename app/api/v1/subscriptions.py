from __future__ import annotations

"""Routes Subscriptions / Paystack (§5.11, §16)."""

import structlog
from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import get_current_user, get_db
from app.core.exceptions import AppException
from app.models.subscription import Subscription
from app.models.user import User
from app.schemas.subscriptions import (
    CancelResponse,
    InitializeBody,
    InitializeResponse,
    PlanOption,
    PlansResponse,
    SimulateWebhookBody,
    SubscriptionMeResponse,
    WebhookResponse,
)
from app.services import payment_service

log = structlog.get_logger()
settings = get_settings()

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


async def _user_sub(user: User, db: AsyncSession) -> Subscription | None:
    row = await db.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    )
    return row.scalar_one_or_none()


@router.get("/me", response_model=SubscriptionMeResponse)
async def get_my_subscription(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    sub = await _user_sub(user, db)
    if sub is None:
        return {"is_premium": user.is_premium}
    return {
        "is_premium": user.is_premium,
        "plan": sub.plan,
        "starts_at": sub.starts_at,
        "expires_at": sub.expires_at,
        "is_auto_renew": sub.is_auto_renew,
        "is_active": sub.is_active,
        "currency": sub.currency,
    }


@router.get("/plans", response_model=PlansResponse)
async def list_plans(
    user: User = Depends(get_current_user),
) -> dict:
    """Liste les plans avec prix localisés selon la ville du user."""
    city = user.city
    if city is not None:
        weekly = city.premium_price_weekly
        monthly = city.premium_price_monthly
        currency = city.currency_code
        city_name = city.name
    else:
        weekly = 1500
        monthly = 5000
        currency = "XOF"
        city_name = None
    return {
        "plans": [
            PlanOption(
                code="weekly",
                duration_days=7,
                amount=weekly,
                currency=currency,
            ),
            PlanOption(
                code="monthly",
                duration_days=30,
                amount=monthly,
                currency=currency,
            ),
        ],
        "city_name": city_name,
    }


@router.post("/initialize", response_model=InitializeResponse)
async def initialize_subscription(
    body: InitializeBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await payment_service.initialize_payment(
        user,
        plan=body.plan,
        payment_method=body.payment_method,
        provider=body.provider,
        db=db,
    )


@router.post("/cancel", response_model=CancelResponse)
async def cancel_subscription(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Désactive l'auto-renouvellement. L'accès premium reste actif jusqu'à
    expires_at (politique : pas de remboursement au prorata).
    """
    from datetime import datetime, timezone

    sub = await _user_sub(user, db)
    if sub is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "no_active_subscription")
    sub.is_auto_renew = False
    sub.cancelled_at = datetime.now(timezone.utc)
    await db.commit()
    log.info("subscription_cancelled", user_id=str(user.id))
    return {"status": "cancelled", "expires_at": sub.expires_at}


@router.post("/webhook/paystack", response_model=WebhookResponse)
async def paystack_webhook(
    request: Request,
    x_paystack_signature: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Webhook Paystack — PUBLIC, auth uniquement par signature HMAC-SHA512.

    Note sécurité : ne JAMAIS logger le payload complet (données sensibles).
    On loggue uniquement reference, status, amount pour l'audit.
    """
    raw_body = await request.body()
    if not payment_service.verify_webhook(raw_body, x_paystack_signature):
        log.warning("paystack_webhook_invalid_signature")
        raise AppException(status.HTTP_401_UNAUTHORIZED, "invalid_signature")

    try:
        import json

        payload = json.loads(raw_body)
    except (ValueError, json.JSONDecodeError):
        raise AppException(status.HTTP_400_BAD_REQUEST, "invalid_json")

    event_type = payload.get("event", "")
    data = payload.get("data", {}) or {}

    log.info(
        "paystack_webhook_received",
        event_type=event_type,
        reference=data.get("reference"),
        amount=data.get("amount"),
        paystack_status=data.get("status"),
    )

    return await payment_service.handle_paystack_webhook(event_type, data, db)


@router.post("/webhook/simulate", response_model=WebhookResponse)
async def simulate_webhook(
    body: SimulateWebhookBody,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Simulateur de webhook (dev uniquement — 404 en prod).

    Utilisé pour les tests locaux et l'environnement dev sans configurer
    un vrai compte Paystack.
    """
    if not settings.paystack_simulate:
        raise AppException(status.HTTP_404_NOT_FOUND, "not_found")

    payment = await payment_service._get_payment_by_reference(body.reference, db)
    if payment is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "payment_not_found")

    fake_data = {
        "reference": body.reference,
        "amount": payment.amount * (100 if payment.currency != "XOF" else 1),
        "currency": payment.currency,
        "status": "success" if body.event == "charge.success" else "failed",
        "metadata": {"user_id": str(payment.user_id)},
        "gateway_response": "Simulated",
    }
    return await payment_service.handle_paystack_webhook(body.event, fake_data, db)


__all__ = ["router"]
