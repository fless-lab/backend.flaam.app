from __future__ import annotations

"""Routes Admin (§20 + §22). 22 endpoints.

Toutes les routes sont derrière `get_admin_user` (403 si non-admin). Le
flag `User.is_admin` est promu manuellement en base (psql ou script de
seed) — jamais via un endpoint utilisateur.

Bloc A.1 : reports / users / stats / events / spots (15 endpoints).
Bloc A.2 : photos / matching-config / prompts / batch / ambassadors /
waitlist (ajouté en Etape 4).
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_user, get_db, get_redis
from app.core.constants import REDIS_CONFIG_KEY
from app.core.exceptions import AppException
from app.models.account_history import AccountHistory
from app.models.city import City
from app.models.event import Event
from app.models.invite_code import InviteCode
from app.models.match import Match
from app.models.matching_config import MatchingConfig
from app.models.payment import Payment
from app.models.photo import Photo
from app.models.profile import Profile
from app.models.report import Report
from app.models.spot import Spot
from app.models.user import User
from app.models.waitlist_entry import WaitlistEntry
from app.schemas.admin import (
    AdminAckResponse,
    AdminAmbassadorItem,
    AdminAmbassadorListResponse,
    AdminAmbassadorPromoteBody,
    AdminBanBody,
    AdminBatchTriggerBody,
    AdminBatchTriggerResponse,
    AdminBulkApproveResponse,
    AdminDashboardStats,
    AdminEventCreateBody,
    AdminEventItem,
    AdminEventListResponse,
    AdminEventUpdateBody,
    AdminGenderChangeBody,
    AdminMatchingConfigItem,
    AdminMatchingConfigListResponse,
    AdminMatchingConfigUpdateBody,
    AdminPhotoBulkApproveBody,
    AdminPhotoItem,
    AdminPhotoListResponse,
    AdminPhotoModerateBody,
    AdminPhotoStats,
    AdminPromptStatItem,
    AdminPromptStatsResponse,
    AdminReportAction,
    AdminReportItem,
    AdminReportListResponse,
    AdminSpotValidateBody,
    AdminUserDetail,
    AdminUserItem,
    AdminUserListResponse,
    AdminWaitlistReleaseBody,
    AdminWaitlistReleaseResponse,
    AdminWaitlistStats,
)
from app.services import invite_service, notification_service, waitlist_service

log = structlog.get_logger()

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(get_admin_user)],
)


# ══════════════════════════════════════════════════════════════════════
# Reports (3)
# ══════════════════════════════════════════════════════════════════════


@router.get("/reports", response_model=AdminReportListResponse)
async def list_reports(
    status_filter: str | None = Query(
        default=None, alias="status", pattern="^(pending|resolved|dismissed)$"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> AdminReportListResponse:
    stmt = select(Report).order_by(Report.created_at.desc())
    if status_filter:
        stmt = stmt.where(Report.status == status_filter)

    total = (
        await db.scalar(
            select(func.count()).select_from(stmt.subquery())
        )
    ) or 0
    rows = (
        await db.execute(stmt.offset(offset).limit(limit))
    ).scalars().all()

    return AdminReportListResponse(
        items=[
            AdminReportItem(
                id=r.id,
                reporter_id=r.reporter_id,
                reported_user_id=r.reported_user_id,
                reason=r.reason,
                description=r.description,
                status=r.status,
                resolution_note=r.resolution_note,
                resolved_by=r.resolved_by,
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=total,
    )


@router.get("/reports/{report_id}", response_model=AdminReportItem)
async def get_report_detail(
    report_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> AdminReportItem:
    r = await db.get(Report, report_id)
    if r is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "report_not_found")
    return AdminReportItem(
        id=r.id,
        reporter_id=r.reporter_id,
        reported_user_id=r.reported_user_id,
        reason=r.reason,
        description=r.description,
        status=r.status,
        resolution_note=r.resolution_note,
        resolved_by=r.resolved_by,
        created_at=r.created_at,
    )


@router.patch("/reports/{report_id}", response_model=AdminReportItem)
async def act_on_report(
    report_id: UUID,
    body: AdminReportAction,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AdminReportItem:
    r = await db.get(Report, report_id)
    if r is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "report_not_found")
    if r.status != "pending":
        raise AppException(status.HTTP_400_BAD_REQUEST, "report_already_processed")

    r.status = "resolved" if body.action == "resolve" else "dismissed"
    r.resolution_note = body.note
    r.resolved_by = str(admin.id)
    await db.commit()
    await db.refresh(r)

    log.info(
        "admin_report_action",
        admin_id=str(admin.id),
        report_id=str(report_id),
        action=body.action,
    )
    return AdminReportItem(
        id=r.id,
        reporter_id=r.reporter_id,
        reported_user_id=r.reported_user_id,
        reason=r.reason,
        description=r.description,
        status=r.status,
        resolution_note=r.resolution_note,
        resolved_by=r.resolved_by,
        created_at=r.created_at,
    )


# ══════════════════════════════════════════════════════════════════════
# Users (5)
# ══════════════════════════════════════════════════════════════════════


def _user_item(u: User) -> AdminUserItem:
    p = u.profile
    return AdminUserItem(
        id=u.id,
        phone_hash=u.phone_hash,
        display_name=p.display_name if p else None,
        gender=p.gender if p else None,
        city_id=u.city_id,
        is_active=u.is_active,
        is_banned=u.is_banned,
        is_deleted=u.is_deleted,
        is_premium=u.is_premium,
        is_selfie_verified=u.is_selfie_verified,
        is_admin=u.is_admin,
        created_at=u.created_at,
    )


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    q: str | None = Query(default=None, description="phone_hash ou display_name"),
    status_filter: str | None = Query(
        default=None, alias="status", pattern="^(active|banned|deleted)$"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> AdminUserListResponse:
    stmt = select(User).order_by(User.created_at.desc())
    if status_filter == "active":
        stmt = stmt.where(
            User.is_active.is_(True),
            User.is_banned.is_(False),
            User.is_deleted.is_(False),
        )
    elif status_filter == "banned":
        stmt = stmt.where(User.is_banned.is_(True))
    elif status_filter == "deleted":
        stmt = stmt.where(User.is_deleted.is_(True))

    if q:
        # Recherche phone_hash exact OU display_name ILIKE (via join profile)
        stmt = stmt.outerjoin(Profile, Profile.user_id == User.id).where(
            or_(
                User.phone_hash == q,
                Profile.display_name.ilike(f"%{q}%"),
            )
        )

    total = (
        await db.scalar(
            select(func.count()).select_from(stmt.subquery())
        )
    ) or 0
    rows = (
        await db.execute(stmt.offset(offset).limit(limit))
    ).scalars().all()
    return AdminUserListResponse(
        items=[_user_item(u) for u in rows], total=total
    )


@router.get("/users/{user_id}", response_model=AdminUserDetail)
async def get_user_detail(
    user_id: UUID, db: AsyncSession = Depends(get_db)
) -> AdminUserDetail:
    u = await db.get(User, user_id)
    if u is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "user_not_found")

    ah_row = await db.execute(
        select(AccountHistory).where(AccountHistory.phone_hash == u.phone_hash)
    )
    ah = ah_row.scalar_one_or_none()
    ah_dict = None
    if ah is not None:
        ah_dict = {
            "total_accounts_created": ah.total_accounts_created,
            "total_accounts_deleted": ah.total_accounts_deleted,
            "total_bans": ah.total_bans,
            "risk_score": ah.risk_score,
            "current_restriction": ah.current_restriction,
            "blocked_by_count": ah.blocked_by_count,
            "last_ban_at": ah.last_ban_at.isoformat() if ah.last_ban_at else None,
        }

    base = _user_item(u)
    return AdminUserDetail(
        **base.model_dump(),
        ban_reason=u.ban_reason,
        deleted_at=u.deleted_at,
        account_history=ah_dict,
    )


@router.patch("/users/{user_id}/ban", response_model=AdminAckResponse)
async def ban_user(
    user_id: UUID,
    body: AdminBanBody,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AdminAckResponse:
    u = await db.get(User, user_id)
    if u is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "user_not_found")
    if u.is_admin:
        raise AppException(status.HTTP_400_BAD_REQUEST, "cannot_ban_admin")

    u.is_banned = True
    u.ban_reason = body.reason

    ah_row = await db.execute(
        select(AccountHistory).where(AccountHistory.phone_hash == u.phone_hash)
    )
    ah = ah_row.scalar_one_or_none()
    if ah is not None:
        ah.total_bans = (ah.total_bans or 0) + 1
        ah.last_ban_at = datetime.now(timezone.utc)

    await db.commit()
    log.info(
        "admin_user_ban",
        admin_id=str(admin.id),
        user_id=str(user_id),
        reason=body.reason,
    )
    return AdminAckResponse()


@router.patch("/users/{user_id}/unban", response_model=AdminAckResponse)
async def unban_user(
    user_id: UUID,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AdminAckResponse:
    u = await db.get(User, user_id)
    if u is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "user_not_found")
    u.is_banned = False
    u.ban_reason = None
    await db.commit()
    log.info("admin_user_unban", admin_id=str(admin.id), user_id=str(user_id))
    return AdminAckResponse()


@router.patch("/users/{user_id}/gender", response_model=AdminAckResponse)
async def change_user_gender(
    user_id: UUID,
    body: AdminGenderChangeBody,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> AdminAckResponse:
    """
    Change le genre d'un user — réservé aux cas : personne trans/NB, erreur
    à l'onboarding, ou correction admin.

    Effets :
    - UPDATE profile.gender
    - is_selfie_verified = False (force re-vérification)
    - DEL Redis behavior:{user_id} (reset scoring comportemental — genre
      affecte certains tuning implicites)
    - Push selfie_reverify_required
    - Log admin_id + old_gender + new_gender + reason
    """
    u = await db.get(User, user_id)
    if u is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "user_not_found")
    if u.profile is None:
        raise AppException(status.HTTP_400_BAD_REQUEST, "profile_missing")

    old_gender = u.profile.gender
    if old_gender == body.new_gender:
        raise AppException(status.HTTP_400_BAD_REQUEST, "gender_unchanged")

    u.profile.gender = body.new_gender
    u.is_selfie_verified = False
    await db.commit()

    # Reset du scoring comportemental + push de re-vérification.
    await redis.delete(f"behavior:{user_id}")
    await notification_service.send_push(
        user_id,
        type="notif_selfie_required",
        data={},
        db=db,
    )

    log.info(
        "admin_user_gender_change",
        admin_id=str(admin.id),
        user_id=str(user_id),
        old_gender=old_gender,
        new_gender=body.new_gender,
        reason=body.reason,
    )
    return AdminAckResponse()


# ══════════════════════════════════════════════════════════════════════
# Stats (1)
# ══════════════════════════════════════════════════════════════════════


@router.get("/stats/dashboard", response_model=AdminDashboardStats)
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db),
) -> AdminDashboardStats:
    now = datetime.now(timezone.utc)
    since_7d = now - timedelta(days=7)
    since_30d = now - timedelta(days=30)

    active_users_7d = (
        await db.scalar(
            select(func.count(User.id)).where(
                User.last_active_at >= since_7d,
                User.is_deleted.is_(False),
            )
        )
    ) or 0

    matches_30d = (
        await db.scalar(
            select(func.count(Match.id)).where(
                Match.created_at >= since_30d,
                Match.status == "matched",
            )
        )
    ) or 0
    matches_per_day = round(matches_30d / 30.0, 2) if matches_30d else 0.0

    # Ratio H/F par ville
    gender_rows = (
        await db.execute(
            select(City.name, Profile.gender, func.count(User.id))
            .select_from(User)
            .join(Profile, Profile.user_id == User.id)
            .join(City, City.id == User.city_id, isouter=True)
            .where(User.is_deleted.is_(False), User.is_active.is_(True))
            .group_by(City.name, Profile.gender)
        )
    ).all()
    ratio: dict[str, dict[str, int]] = {}
    for city_name, gender, count in gender_rows:
        city_key = city_name or "unknown"
        ratio.setdefault(city_key, {})[gender] = int(count)

    # Churn 30j : comptes supprimés ces 30 derniers jours / total comptes actifs il y a 30 j.
    deleted_30d = (
        await db.scalar(
            select(func.count(User.id)).where(
                User.deleted_at.isnot(None),
                User.deleted_at >= since_30d,
            )
        )
    ) or 0
    active_30d_ago = (
        await db.scalar(
            select(func.count(User.id)).where(
                User.created_at <= since_30d,
                User.is_active.is_(True),
            )
        )
    ) or 0
    churn = round(deleted_30d / active_30d_ago, 4) if active_30d_ago else 0.0

    premium_count = (
        await db.scalar(
            select(func.count(User.id)).where(
                User.is_premium.is_(True),
                User.is_deleted.is_(False),
            )
        )
    ) or 0

    revenue_30d = (
        await db.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == "success",
                Payment.completed_at >= since_30d,
            )
        )
    ) or 0

    return AdminDashboardStats(
        active_users_7d=int(active_users_7d),
        matches_per_day=float(matches_per_day),
        gender_ratio_by_city=ratio,
        churn_30d=float(churn),
        premium_count=int(premium_count),
        revenue_estimated_30d=int(revenue_30d),
    )


# ══════════════════════════════════════════════════════════════════════
# Events (4)
# ══════════════════════════════════════════════════════════════════════


def _event_item(ev: Event) -> AdminEventItem:
    return AdminEventItem(
        id=ev.id,
        title=ev.title,
        city_id=ev.city_id,
        spot_id=ev.spot_id,
        starts_at=ev.starts_at,
        status=ev.status,
        current_attendees=ev.current_attendees,
        max_attendees=ev.max_attendees,
    )


@router.post(
    "/events",
    response_model=AdminEventItem,
    status_code=status.HTTP_201_CREATED,
)
async def create_event(
    body: AdminEventCreateBody,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AdminEventItem:
    spot = await db.get(Spot, body.spot_id)
    if spot is None or not spot.is_active:
        raise AppException(status.HTTP_400_BAD_REQUEST, "spot_invalid")
    city = await db.get(City, body.city_id)
    if city is None:
        raise AppException(status.HTTP_400_BAD_REQUEST, "city_invalid")

    ev = Event(
        title=body.title,
        description=body.description,
        spot_id=body.spot_id,
        city_id=body.city_id,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        category=body.category,
        max_attendees=body.max_attendees,
        is_admin_created=True,
        is_approved=body.status == "published",
        is_sponsored=body.is_sponsored,
        sponsor_name=body.sponsor_name,
        created_by_user_id=admin.id,
        status=body.status,
    )
    db.add(ev)
    await db.commit()
    await db.refresh(ev)
    log.info("admin_event_created", admin_id=str(admin.id), event_id=str(ev.id))
    return _event_item(ev)


@router.patch("/events/{event_id}", response_model=AdminEventItem)
async def update_event(
    event_id: UUID,
    body: AdminEventUpdateBody,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AdminEventItem:
    ev = await db.get(Event, event_id)
    if ev is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "event_not_found")

    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(ev, field, value)
    if body.status is not None:
        ev.is_approved = body.status == "published"
        if body.status == "cancelled":
            ev.is_active = False

    await db.commit()
    await db.refresh(ev)
    log.info("admin_event_updated", admin_id=str(admin.id), event_id=str(ev.id))
    return _event_item(ev)


@router.get("/events", response_model=AdminEventListResponse)
async def list_events_admin(
    status_filter: str | None = Query(
        default=None,
        alias="status",
        pattern="^(draft|published|full|ongoing|completed|cancelled)$",
    ),
    city_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> AdminEventListResponse:
    stmt = select(Event).order_by(Event.starts_at.desc())
    if status_filter:
        stmt = stmt.where(Event.status == status_filter)
    if city_id:
        stmt = stmt.where(Event.city_id == city_id)

    total = (
        await db.scalar(select(func.count()).select_from(stmt.subquery()))
    ) or 0
    rows = (
        await db.execute(stmt.offset(offset).limit(limit))
    ).scalars().all()
    return AdminEventListResponse(
        items=[_event_item(ev) for ev in rows], total=total
    )


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(
    event_id: UUID,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    ev = await db.get(Event, event_id)
    if ev is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "event_not_found")
    ev.is_active = False
    ev.status = "cancelled"
    await db.commit()
    log.info("admin_event_deleted", admin_id=str(admin.id), event_id=str(event_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ══════════════════════════════════════════════════════════════════════
# Spots (1)
# ══════════════════════════════════════════════════════════════════════


@router.patch("/spots/{spot_id}/validate", response_model=AdminAckResponse)
async def validate_spot(
    spot_id: UUID,
    body: AdminSpotValidateBody,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AdminAckResponse:
    spot = await db.get(Spot, spot_id)
    if spot is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "spot_not_found")

    if body.action == "approve":
        spot.is_verified = True
        spot.is_active = True
    else:  # reject → désactive + garde pour audit
        spot.is_verified = False
        spot.is_active = False

    await db.commit()
    log.info(
        "admin_spot_validate",
        admin_id=str(admin.id),
        spot_id=str(spot_id),
        action=body.action,
        reason=body.reason,
    )
    return AdminAckResponse()


# ══════════════════════════════════════════════════════════════════════
# Photos moderation (4)
# ══════════════════════════════════════════════════════════════════════


def _photo_item(p: Photo) -> AdminPhotoItem:
    return AdminPhotoItem(
        id=p.id,
        user_id=p.user_id,
        thumbnail_url=p.thumbnail_url,
        moderation_status=p.moderation_status,
        moderation_score=p.moderation_score,
        rejection_reason=p.rejection_reason,
        created_at=p.created_at,
    )


@router.get("/photos/pending", response_model=AdminPhotoListResponse)
async def list_pending_photos(
    status_filter: str = Query(
        default="pending", alias="status", pattern="^(pending|review|rejected)$"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> AdminPhotoListResponse:
    stmt = (
        select(Photo)
        .where(Photo.moderation_status == status_filter)
        .order_by(Photo.created_at.asc())
    )
    total = (
        await db.scalar(select(func.count()).select_from(stmt.subquery()))
    ) or 0
    rows = (
        await db.execute(stmt.offset(offset).limit(limit))
    ).scalars().all()
    return AdminPhotoListResponse(
        items=[_photo_item(p) for p in rows], total=total
    )


@router.get("/photos/stats", response_model=AdminPhotoStats)
async def get_photo_stats(
    db: AsyncSession = Depends(get_db),
) -> AdminPhotoStats:
    rows = (
        await db.execute(
            select(Photo.moderation_status, func.count(Photo.id)).group_by(
                Photo.moderation_status
            )
        )
    ).all()
    counts = {status_: int(n) for status_, n in rows}
    return AdminPhotoStats(
        pending=counts.get("pending", 0),
        approved=counts.get("approved", 0),
        rejected=counts.get("rejected", 0),
        review=counts.get("review", 0),
    )


@router.patch("/photos/{photo_id}/moderate", response_model=AdminAckResponse)
async def moderate_photo(
    photo_id: UUID,
    body: AdminPhotoModerateBody,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AdminAckResponse:
    p = await db.get(Photo, photo_id)
    if p is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "photo_not_found")

    if body.action == "approve":
        p.moderation_status = "approved"
        p.rejection_reason = None
    else:
        p.moderation_status = "rejected"
        p.rejection_reason = body.rejection_reason
    await db.commit()
    log.info(
        "admin_photo_moderate",
        admin_id=str(admin.id),
        photo_id=str(photo_id),
        action=body.action,
    )
    return AdminAckResponse()


@router.post("/photos/bulk-approve", response_model=AdminBulkApproveResponse)
async def bulk_approve_photos(
    body: AdminPhotoBulkApproveBody,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AdminBulkApproveResponse:
    rows = (
        await db.execute(select(Photo).where(Photo.id.in_(body.photo_ids)))
    ).scalars().all()
    count = 0
    for p in rows:
        if p.moderation_status in ("pending", "review"):
            p.moderation_status = "approved"
            p.rejection_reason = None
            count += 1
    await db.commit()
    log.info(
        "admin_photo_bulk_approve",
        admin_id=str(admin.id),
        approved=count,
    )
    return AdminBulkApproveResponse(approved=count)


# ══════════════════════════════════════════════════════════════════════
# Matching config (2)
# ══════════════════════════════════════════════════════════════════════


@router.get(
    "/matching-config", response_model=AdminMatchingConfigListResponse
)
async def list_matching_config(
    db: AsyncSession = Depends(get_db),
) -> AdminMatchingConfigListResponse:
    rows = (
        await db.execute(
            select(MatchingConfig).order_by(
                MatchingConfig.category.asc(), MatchingConfig.key.asc()
            )
        )
    ).scalars().all()
    return AdminMatchingConfigListResponse(
        items=[
            AdminMatchingConfigItem(
                key=c.key,
                value=c.value,
                category=c.category,
                description=c.description,
            )
            for c in rows
        ]
    )


@router.patch(
    "/matching-config/{key}", response_model=AdminMatchingConfigItem
)
async def update_matching_config(
    key: str,
    body: AdminMatchingConfigUpdateBody,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> AdminMatchingConfigItem:
    row = await db.execute(
        select(MatchingConfig).where(MatchingConfig.key == key)
    )
    cfg = row.scalar_one_or_none()
    if cfg is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "config_key_not_found")

    if cfg.min_value is not None and body.value < cfg.min_value:
        raise AppException(status.HTTP_400_BAD_REQUEST, "value_below_min")
    if cfg.max_value is not None and body.value > cfg.max_value:
        raise AppException(status.HTTP_400_BAD_REQUEST, "value_above_max")

    cfg.value = body.value
    cfg.updated_by = str(admin.id)
    await db.commit()

    # Invalidate cache pour prise en compte immédiate
    await redis.delete(REDIS_CONFIG_KEY.format(key=key))

    log.info(
        "admin_matching_config_update",
        admin_id=str(admin.id),
        key=key,
        value=body.value,
    )
    return AdminMatchingConfigItem(
        key=cfg.key,
        value=cfg.value,
        category=cfg.category,
        description=cfg.description,
    )


# ══════════════════════════════════════════════════════════════════════
# Prompts stats (1)
# ══════════════════════════════════════════════════════════════════════


@router.get("/prompts/stats", response_model=AdminPromptStatsResponse)
async def get_prompts_stats(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> AdminPromptStatsResponse:
    """
    Agrège les prompts du JSONB Profile.prompts par question, classés
    par total de likes (Feature B de S9). Raw SQL : les cast int sur
    JSONB via SQLAlchemy ORM sont lourds à exprimer.
    """
    sql = text(
        """
        SELECT
            (elt->>'question') AS question,
            COALESCE(SUM((elt->>'like_count')::int), 0) AS total_likes,
            COUNT(*) AS usage_count
        FROM profiles, jsonb_array_elements(COALESCE(prompts, '[]'::jsonb)) AS elt
        WHERE elt ? 'question'
        GROUP BY (elt->>'question')
        ORDER BY total_likes DESC, usage_count DESC
        LIMIT :lim
        """
    )
    result = await db.execute(sql, {"lim": limit})
    rows = result.all()
    return AdminPromptStatsResponse(
        items=[
            AdminPromptStatItem(
                question=r.question or "",
                total_likes=int(r.total_likes or 0),
                usage_count=int(r.usage_count or 0),
            )
            for r in rows
        ]
    )


# ══════════════════════════════════════════════════════════════════════
# Batch trigger (1)
# ══════════════════════════════════════════════════════════════════════


@router.post(
    "/batch/generate-feeds",
    response_model=AdminBatchTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_feed_batch(
    body: AdminBatchTriggerBody,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> AdminBatchTriggerResponse:
    """
    Déclenche un batch de génération de feeds (fire-and-forget asyncio
    en attendant le câblage Celery en S11).
    """
    import asyncio
    import uuid as _uuid

    from app.tasks.matching_tasks import generate_all_feeds, generate_user_feed

    task_id = str(_uuid.uuid4())

    async def _run() -> None:
        try:
            if body.city_id is not None:
                # Batch ciblé ville : on boucle sur les users de la ville.
                rows = (
                    await db.execute(
                        select(User.id).where(
                            User.city_id == body.city_id,
                            User.is_active.is_(True),
                            User.is_deleted.is_(False),
                        )
                    )
                ).all()
                for (uid,) in rows:
                    await generate_user_feed(uid, db, redis)
            else:
                await generate_all_feeds(db, redis)
        except Exception as exc:  # noqa: BLE001
            log.error("admin_batch_failed", task_id=task_id, error=str(exc))

    asyncio.create_task(_run())
    log.info(
        "admin_batch_queued",
        admin_id=str(admin.id),
        task_id=task_id,
        city_id=str(body.city_id) if body.city_id else None,
    )
    return AdminBatchTriggerResponse(task_id=task_id, status="queued")


# ══════════════════════════════════════════════════════════════════════
# Ambassadors (3)
# ══════════════════════════════════════════════════════════════════════


@router.get("/ambassadors", response_model=AdminAmbassadorListResponse)
async def list_ambassadors(
    db: AsyncSession = Depends(get_db),
) -> AdminAmbassadorListResponse:
    users_rows = (
        await db.execute(
            select(User).where(User.is_ambassador.is_(True)).order_by(
                User.created_at.desc()
            )
        )
    ).scalars().all()

    items: list[AdminAmbassadorItem] = []
    for u in users_rows:
        generated = (
            await db.scalar(
                select(func.count(InviteCode.id)).where(
                    InviteCode.creator_id == u.id
                )
            )
        ) or 0
        redeemed = (
            await db.scalar(
                select(func.count(InviteCode.id)).where(
                    InviteCode.creator_id == u.id,
                    InviteCode.used_by_id.isnot(None),
                )
            )
        ) or 0
        items.append(
            AdminAmbassadorItem(
                user_id=u.id,
                display_name=u.profile.display_name if u.profile else None,
                phone_hash=u.phone_hash,
                codes_generated=int(generated),
                codes_redeemed=int(redeemed),
                created_at=u.created_at,
            )
        )
    return AdminAmbassadorListResponse(items=items)


@router.post(
    "/ambassadors",
    response_model=AdminAckResponse,
    status_code=status.HTTP_201_CREATED,
)
async def promote_ambassador(
    body: AdminAmbassadorPromoteBody,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AdminAckResponse:
    u = await db.get(User, body.user_id)
    if u is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "user_not_found")
    if u.is_ambassador:
        raise AppException(status.HTTP_400_BAD_REQUEST, "already_ambassador")

    u.is_ambassador = True
    await db.commit()

    # Génère ses 50 codes d'emblée via le service existant
    await invite_service.generate_codes(u, db)

    log.info(
        "admin_ambassador_promote",
        admin_id=str(admin.id),
        user_id=str(body.user_id),
    )
    return AdminAckResponse()


@router.delete(
    "/ambassadors/{user_id}",
    response_model=AdminAckResponse,
)
async def demote_ambassador(
    user_id: UUID,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AdminAckResponse:
    u = await db.get(User, user_id)
    if u is None:
        raise AppException(status.HTTP_404_NOT_FOUND, "user_not_found")
    if not u.is_ambassador:
        raise AppException(status.HTTP_400_BAD_REQUEST, "not_ambassador")

    # Démote uniquement — ne révoque PAS les codes déjà générés (spec).
    u.is_ambassador = False
    await db.commit()
    log.info(
        "admin_ambassador_demote",
        admin_id=str(admin.id),
        user_id=str(user_id),
    )
    return AdminAckResponse()


# ══════════════════════════════════════════════════════════════════════
# Waitlist (2)
# ══════════════════════════════════════════════════════════════════════


@router.get("/waitlist/stats", response_model=AdminWaitlistStats)
async def get_waitlist_stats(
    db: AsyncSession = Depends(get_db),
) -> AdminWaitlistStats:
    total_waiting = (
        await db.scalar(
            select(func.count(WaitlistEntry.id)).where(
                WaitlistEntry.status == "waiting"
            )
        )
    ) or 0
    min_pos = await db.scalar(
        select(func.min(WaitlistEntry.position)).where(
            WaitlistEntry.status == "waiting"
        )
    )
    max_pos = await db.scalar(
        select(func.max(WaitlistEntry.position)).where(
            WaitlistEntry.status == "waiting"
        )
    )

    gender_rows = (
        await db.execute(
            select(WaitlistEntry.gender, func.count(WaitlistEntry.id))
            .where(WaitlistEntry.status == "waiting")
            .group_by(WaitlistEntry.gender)
        )
    ).all()
    gender_ratio = {g: int(n) for g, n in gender_rows}

    city_rows = (
        await db.execute(
            select(WaitlistEntry.city_id, func.count(WaitlistEntry.id))
            .where(WaitlistEntry.status == "waiting")
            .group_by(WaitlistEntry.city_id)
        )
    ).all()
    by_city = {str(cid): int(n) for cid, n in city_rows}

    return AdminWaitlistStats(
        total_waiting=int(total_waiting),
        min_position=int(min_pos) if min_pos is not None else None,
        max_position=int(max_pos) if max_pos is not None else None,
        gender_ratio=gender_ratio,
        by_city=by_city,
    )


@router.post(
    "/waitlist/release", response_model=AdminWaitlistReleaseResponse
)
async def release_waitlist(
    body: AdminWaitlistReleaseBody,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AdminWaitlistReleaseResponse:
    result = await waitlist_service.release_batch(
        body.city_id, db, size=body.count
    )
    log.info(
        "admin_waitlist_release",
        admin_id=str(admin.id),
        city_id=str(body.city_id),
        released=result.get("released", 0),
    )
    return AdminWaitlistReleaseResponse(released=int(result.get("released", 0)))


# ══════════════════════════════════════════════════════════════════════
# Daily KPIs — historique persiste (S13)
# ══════════════════════════════════════════════════════════════════════


@router.get("/stats/kpis")
async def get_kpis(
    date: str | None = Query(default=None, description="YYYY-MM-DD"),
    city_id: UUID | None = Query(default=None),
    metric: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Retourne les KPIs historiques persistes dans daily_kpis."""
    from datetime import date as date_type

    from app.models.daily_kpi import DailyKpi

    q = select(DailyKpi)
    if date:
        try:
            d = date_type.fromisoformat(date)
        except ValueError:
            raise AppException(status.HTTP_400_BAD_REQUEST, "invalid_date_format")
        q = q.where(DailyKpi.date == d)
    if city_id is not None:
        q = q.where(DailyKpi.city_id == city_id)
    if metric:
        q = q.where(DailyKpi.metric == metric)
    q = q.order_by(DailyKpi.date.desc()).limit(500)

    result = await db.execute(q)
    rows = result.scalars().all()
    return [
        {
            "date": str(r.date),
            "city_id": str(r.city_id) if r.city_id else None,
            "metric": r.metric,
            "value": r.value,
        }
        for r in rows
    ]
