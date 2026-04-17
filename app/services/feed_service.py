from __future__ import annotations

"""
Feed service (spec §5.6).

Expose :
- get_daily_feed()          : lecture cache Redis/FeedCache + hydratation
- like_profile()             : idempotent, quota, match mutuel, ice-breaker
- skip_profile()             : idempotent, stocké comme Match(status="skipped")
- log_view()                 : BehaviorLog + behavior_multiplier
- get_likes_received()       : premium only, gens qui m'ont liké
- get_crossed_feed()         : section « Déjà croisés »

Idempotence X-Idempotency-Key (spec MàJ 6B) :
  Redis "idempotency:{action}:{user_id}:{key}" TTL 24h. Si hit → on renvoie
  la réponse cachée sans réexécuter l'action. Protège des retries 3G.

Quotas :
  Redis "daily_likes:{user_id}:{YYYY-MM-DD}" TTL 48h. INCR sur like
  effectif (pas sur replay idempotent).

Câblage update_behavior_on_action : effectué dans like/skip/view.
"""

import json
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import redis.asyncio as aioredis
import structlog
from fastapi import status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import (
    MATCHING_FEED_MIN_SIZE,
    MATCHING_FEED_SIZE,
    MATCHING_SKIP_COOLDOWN_DAYS,
)
from app.core.cache import cache_invalidate
from app.core.errors import FlaamError
from app.core.exceptions import AppException
from app.core.i18n import t
from app.models.behavior_log import BehaviorLog
from app.models.feed_cache import FeedCache
from app.models.match import Match
from app.models.profile import Profile
from app.models.user import User
from app.models.user_quartier import UserQuartier
from app.models.user_spot import UserSpot
from app.services.config_service import get_config, get_configs
from app.services.icebreaker_service import (
    FIDELITY_RANK,
    generate_icebreaker,
)
from app.services.matching_engine.behavior_scorer import (
    update_behavior_on_action,
)
from app.services.matching_engine.pipeline import generate_feed_for_user

log = structlog.get_logger()


# ── Clés Redis ────────────────────────────────────────────────────────

FEED_CACHE_KEY = "feed:{user_id}"
FEED_CACHE_TTL_SECONDS = 24 * 3600

DAILY_LIKES_KEY = "daily_likes:{user_id}:{day}"
DAILY_LIKES_TTL_SECONDS = 48 * 3600

IDEMPOTENCY_KEY = "idempotency:{action}:{user_id}:{key}"
IDEMPOTENCY_TTL_SECONDS = 24 * 3600

MATCH_EXPIRE_DAYS = 7

# Clés de config consommées par update_behavior_on_action
_BEHAVIOR_CONFIG_KEYS = (
    "behavior_response_min",
    "behavior_response_max",
    "behavior_selectivity_min",
    "behavior_selectivity_max",
    "behavior_richness_min",
    "behavior_richness_max",
    "behavior_depth_min",
    "behavior_depth_max",
    "behavior_min_multiplier",
    "behavior_max_multiplier",
)


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _next_midnight_utc() -> datetime:
    tomorrow = _today_utc() + timedelta(days=1)
    return datetime.combine(tomorrow, time(3, 0), tzinfo=timezone.utc)


def _age_from_birth(birth: date) -> int:
    today = date.today()
    return today.year - birth.year - (
        (today.month, today.day) < (birth.month, birth.day)
    )


async def _load_user_full(user_id: UUID, db: AsyncSession) -> User | None:
    stmt = (
        select(User)
        .options(
            selectinload(User.profile),
            selectinload(User.photos),
            selectinload(User.user_quartiers).selectinload(UserQuartier.quartier),
            selectinload(User.user_spots).selectinload(UserSpot.spot),
        )
        .where(User.id == user_id)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _load_users_full(
    user_ids: list[UUID], db: AsyncSession
) -> dict[UUID, User]:
    if not user_ids:
        return {}
    stmt = (
        select(User)
        .options(
            selectinload(User.profile),
            selectinload(User.photos),
            selectinload(User.user_quartiers).selectinload(UserQuartier.quartier),
            selectinload(User.user_spots).selectinload(UserSpot.spot),
        )
        .where(User.id.in_(user_ids))
    )
    res = await db.execute(stmt)
    return {u.id: u for u in res.scalars().all()}


def _photo_dicts(user: User) -> list[dict]:
    photos = sorted(
        [p for p in (user.photos or []) if p.moderation_status != "rejected"],
        key=lambda p: p.display_order,
    )
    return [
        {
            "id": p.id,
            "original_url": p.original_url,
            "thumbnail_url": p.thumbnail_url,
            "medium_url": p.medium_url,
            "display_order": p.display_order,
            "moderation_status": p.moderation_status,
            "width": p.width,
            "height": p.height,
            "file_size_bytes": p.file_size_bytes,
            "is_verified_selfie": p.is_verified_selfie,
            "dominant_color": p.dominant_color,
        }
        for p in photos
    ]


async def _increment_prompt_like_count(
    target_user_id: UUID, prompt_id: str, db: AsyncSession
) -> None:
    """
    Feature B (Session 9) — tracking A/B des prompts.

    Incrémente `like_count` dans l'entrée matching `prompt_id` du
    JSONB `prompts` du Profile. Silencieux si le prompt n'existe pas
    (ex : le client a envoyé un mauvais id).
    """
    res = await db.execute(
        select(Profile).where(Profile.user_id == target_user_id)
    )
    profile = res.scalar_one_or_none()
    if profile is None or not profile.prompts:
        return
    updated = False
    new_list: list[dict] = []
    for entry in profile.prompts:
        if not isinstance(entry, dict):
            new_list.append(entry)
            continue
        entry_id = entry.get("prompt_id") or entry.get("question")
        if entry_id == prompt_id:
            count = int(entry.get("like_count") or 0) + 1
            new_list.append({**entry, "like_count": count})
            updated = True
        else:
            new_list.append(entry)
    if updated:
        profile.prompts = new_list


def _prompts_dicts(profile: Profile) -> list[dict]:
    out: list[dict] = []
    for p in profile.prompts or []:
        if not isinstance(p, dict):
            continue
        out.append(
            {
                "question": p.get("question", ""),
                "answer": p.get("answer", ""),
                "prompt_id": p.get("prompt_id") or p.get("question"),
            }
        )
    return out


def _quartier_dicts(user: User) -> list[dict]:
    out: list[dict] = []
    for uq in user.user_quartiers or []:
        q = uq.quartier
        if q is None:
            continue
        out.append(
            {
                "quartier_id": q.id,
                "name": q.name,
                "relation_type": uq.relation_type,
            }
        )
    return out


def _spots_in_common(me: User, other: User) -> list[dict]:
    """Retourne la liste des spots dont les DEUX users ont un UserSpot visible."""
    my_map = {
        us.spot_id: us for us in (me.user_spots or []) if us.is_visible
    }
    other_map = {
        us.spot_id: us for us in (other.user_spots or []) if us.is_visible
    }
    common_ids = set(my_map.keys()) & set(other_map.keys())
    out: list[dict] = []
    for sid in common_ids:
        their = other_map[sid]
        mine = my_map[sid]
        spot = their.spot
        if spot is None:
            continue
        out.append(
            {
                "spot_id": sid,
                "name": spot.name,
                "category": spot.category,
                "their_fidelity": their.fidelity_level,
                "your_fidelity": mine.fidelity_level,
            }
        )
    return out


def _tags_in_common(me: User, other: User) -> list[str]:
    if me.profile is None or other.profile is None:
        return []
    my_tags = set(me.profile.tags or [])
    return sorted(my_tags & set(other.profile.tags or []))


def _hydrate_profile(
    me: User,
    other: User,
    *,
    is_wildcard: bool,
    is_new_user: bool,
    geo_score: float | None = None,
) -> dict:
    """Produit le dict FeedProfileItem à partir des ORM entités."""
    profile = other.profile
    assert profile is not None, "hydrate_profile sur user sans profile"

    return {
        "id": profile.id,
        "user_id": other.id,
        "display_name": profile.display_name,
        "age": _age_from_birth(profile.birth_date),
        "intention": profile.intention,
        "sector": profile.sector,
        "rhythm": profile.rhythm,
        "photos": _photo_dicts(other),
        "prompts": _prompts_dicts(profile),
        "tags": list(profile.tags or []),
        "tags_in_common": _tags_in_common(me, other),
        "languages": list(profile.languages or []),
        "quartiers": _quartier_dicts(other),
        "spots_in_common": _spots_in_common(me, other),
        "geo_score_display": (
            int(round(max(0.0, min(1.0, geo_score or 0.0)) * 100))
        ),
        "is_verified": bool(other.is_selfie_verified),
        "is_new_user": is_new_user,
        "is_wildcard": is_wildcard,
    }


# ══════════════════════════════════════════════════════════════════════
# Cache feed (Redis + FeedCache)
# ══════════════════════════════════════════════════════════════════════


async def _read_feed_cache(
    user_id: UUID, redis_client: aioredis.Redis, db: AsyncSession
) -> dict | None:
    """Redis d'abord, fallback FeedCache DB pour today."""
    raw = await redis_client.get(FEED_CACHE_KEY.format(user_id=str(user_id)))
    if raw:
        try:
            data = json.loads(raw)
            if data.get("feed_date") == _today_utc().isoformat():
                return data
        except (json.JSONDecodeError, TypeError):
            pass

    row = await db.execute(
        select(FeedCache).where(
            FeedCache.user_id == user_id,
            FeedCache.feed_date == _today_utc(),
        )
    )
    fc = row.scalar_one_or_none()
    if fc is None:
        return None
    return {
        "feed_date": fc.feed_date.isoformat(),
        "profile_ids": [str(x) for x in (fc.profile_ids or [])],
        "wildcards": [str(x) for x in (fc.wildcard_ids or [])],
        "new_users": [str(x) for x in (fc.new_user_ids or [])],
    }


async def _write_feed_cache(
    user_id: UUID,
    feed: dict,
    redis_client: aioredis.Redis,
    db: AsyncSession,
) -> None:
    """Persiste Redis (24h) + FeedCache (DB)."""
    today = _today_utc()
    payload = {
        "feed_date": today.isoformat(),
        "profile_ids": [str(x) for x in feed.get("profile_ids") or []],
        "wildcards": [str(x) for x in feed.get("wildcards") or []],
        "new_users": [str(x) for x in feed.get("new_users") or []],
    }
    await redis_client.set(
        FEED_CACHE_KEY.format(user_id=str(user_id)),
        json.dumps(payload),
        ex=FEED_CACHE_TTL_SECONDS,
    )

    # UPSERT FeedCache
    row = await db.execute(
        select(FeedCache).where(
            FeedCache.user_id == user_id, FeedCache.feed_date == today
        )
    )
    existing = row.scalar_one_or_none()
    if existing is None:
        db.add(
            FeedCache(
                user_id=user_id,
                feed_date=today,
                profile_ids=list(feed.get("profile_ids") or []),
                wildcard_ids=list(feed.get("wildcards") or []),
                new_user_ids=list(feed.get("new_users") or []),
            )
        )
    else:
        existing.profile_ids = list(feed.get("profile_ids") or [])
        existing.wildcard_ids = list(feed.get("wildcards") or [])
        existing.new_user_ids = list(feed.get("new_users") or [])
    await db.flush()


# ══════════════════════════════════════════════════════════════════════
# GET /feed
# ══════════════════════════════════════════════════════════════════════


async def get_daily_feed(
    user: User, db: AsyncSession, redis_client: aioredis.Redis
) -> dict:
    cached = await _read_feed_cache(user.id, redis_client, db)
    if cached is None:
        generated = await generate_feed_for_user(user.id, db, redis_client)
        await _write_feed_cache(user.id, generated, redis_client, db)
        await db.commit()
        cached = {
            "feed_date": _today_utc().isoformat(),
            "profile_ids": [str(x) for x in generated["profile_ids"]],
            "wildcards": [str(x) for x in generated["wildcards"]],
            "new_users": [str(x) for x in generated["new_users"]],
        }

    profile_ids = [UUID(x) for x in cached["profile_ids"]]
    wildcards = {UUID(x) for x in cached.get("wildcards") or []}
    new_users = {UUID(x) for x in cached.get("new_users") or []}

    me_full = await _load_user_full(user.id, db)
    others = await _load_users_full(profile_ids, db)

    items: list[dict] = []
    for pid in profile_ids:
        other = others.get(pid)
        if other is None or other.profile is None:
            continue
        items.append(
            _hydrate_profile(
                me_full or user,
                other,
                is_wildcard=(pid in wildcards),
                is_new_user=(pid in new_users),
                geo_score=None,  # L2 non persisté hors pipeline — skip au MVP
            )
        )

    # Quota likes
    is_premium = bool(user.is_premium)
    daily_quota_key = (
        "daily_likes_premium" if is_premium else "daily_likes_free"
    )
    quota = int(await get_config(daily_quota_key, redis_client, db))
    used = await _get_daily_likes_used(user.id, redis_client)
    remaining = max(0, quota - used)

    return {
        "feed_date": _today_utc(),
        "profiles": items,
        "remaining_likes": remaining,
        "is_premium": is_premium,
        "next_refresh_at": _next_midnight_utc(),
    }


async def get_crossed_feed(
    user: User, db: AsyncSession, redis_client: aioredis.Redis
) -> dict:
    """
    Section 'Déjà croisés' — profils vus mais pas encore actionnés.

    Source : BehaviorLog(event_type="view") des 7 derniers jours, où
    target_user_id n'a pas encore de Match de ma part (pending/matched/
    skipped).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    rows = await db.execute(
        select(BehaviorLog.target_user_id)
        .where(
            BehaviorLog.user_id == user.id,
            BehaviorLog.event_type == "view",
            BehaviorLog.created_at >= cutoff,
            BehaviorLog.target_user_id.isnot(None),
        )
        .distinct()
    )
    viewed_ids = [r[0] for r in rows.all() if r[0] is not None]
    if not viewed_ids:
        return {"profiles": []}

    acted = await db.execute(
        select(Match.user_b_id).where(
            Match.user_a_id == user.id,
            Match.user_b_id.in_(viewed_ids),
            Match.status.in_(("pending", "matched", "skipped")),
        )
    )
    acted_set = {r[0] for r in acted.all()}
    pending_ids = [uid for uid in viewed_ids if uid not in acted_set]

    me_full = await _load_user_full(user.id, db)
    others = await _load_users_full(pending_ids, db)
    items: list[dict] = []
    for uid, other in others.items():
        if other.profile is None or not other.is_visible or other.is_banned:
            continue
        items.append(
            _hydrate_profile(
                me_full or user,
                other,
                is_wildcard=False,
                is_new_user=False,
            )
        )
    return {"profiles": items}


# ══════════════════════════════════════════════════════════════════════
# Idempotence + quota helpers
# ══════════════════════════════════════════════════════════════════════


async def _get_idempotent_response(
    action: str, user_id: UUID, key: str | None, redis_client: aioredis.Redis
) -> dict | None:
    if not key:
        return None
    raw = await redis_client.get(
        IDEMPOTENCY_KEY.format(action=action, user_id=str(user_id), key=key)
    )
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def _store_idempotent_response(
    action: str,
    user_id: UUID,
    key: str | None,
    response: dict,
    redis_client: aioredis.Redis,
) -> None:
    if not key:
        return
    await redis_client.set(
        IDEMPOTENCY_KEY.format(action=action, user_id=str(user_id), key=key),
        json.dumps(response, default=str),
        ex=IDEMPOTENCY_TTL_SECONDS,
    )


async def _get_daily_likes_used(
    user_id: UUID, redis_client: aioredis.Redis
) -> int:
    key = DAILY_LIKES_KEY.format(
        user_id=str(user_id), day=_today_utc().isoformat()
    )
    raw = await redis_client.get(key)
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


async def _incr_daily_likes(
    user_id: UUID, redis_client: aioredis.Redis
) -> int:
    key = DAILY_LIKES_KEY.format(
        user_id=str(user_id), day=_today_utc().isoformat()
    )
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, DAILY_LIKES_TTL_SECONDS)
    return int(count)


async def _assert_profile_in_feed(
    user_id: UUID,
    target_id: UUID,
    redis_client: aioredis.Redis,
    db: AsyncSession,
    lang: str = "fr",
) -> None:
    """Vérifie que target_id a été servi dans le feed du user (Redis → DB fallback)."""
    # 1. Check Redis feed cache
    raw = await redis_client.get(FEED_CACHE_KEY.format(user_id=str(user_id)))
    if raw:
        try:
            data = json.loads(raw)
            all_ids = set(data.get("profile_ids", []))
            all_ids.update(data.get("wildcards", []))
            all_ids.update(data.get("new_users", []))
            if str(target_id) in all_ids:
                return
        except (json.JSONDecodeError, TypeError):
            pass

    # 2. Fallback DB FeedCache (today or yesterday)
    yesterday = _today_utc() - timedelta(days=1)
    stmt = select(FeedCache).where(
        FeedCache.user_id == user_id,
        FeedCache.feed_date >= yesterday,
    )
    result = await db.execute(stmt)
    for fc in result.scalars():
        all_ids_db: set[UUID] = set(fc.profile_ids or [])
        all_ids_db.update(fc.wildcard_ids or [])
        all_ids_db.update(fc.new_user_ids or [])
        if target_id in all_ids_db:
            return

    # 3. Ni Redis ni BD → reject
    raise FlaamError("profile_not_in_feed", 400, lang)


# ══════════════════════════════════════════════════════════════════════
# POST /feed/{id}/like
# ══════════════════════════════════════════════════════════════════════


async def like_profile(
    user: User,
    target_id: UUID,
    body: dict,
    idem_key: str | None,
    db: AsyncSession,
    redis_client: aioredis.Redis,
    lang: str = "fr",
) -> dict:
    # 1. Idempotence — si même clé déjà vue, on rejoue la réponse cachée
    cached = await _get_idempotent_response(
        "like", user.id, idem_key, redis_client
    )
    if cached is not None:
        return cached

    # 2. Validations basiques
    if target_id == user.id:
        raise AppException(status.HTTP_400_BAD_REQUEST, "cannot_like_self")

    target = await db.get(User, target_id)
    if (
        target is None
        or not target.is_active
        or not target.is_visible
        or target.is_banned
        or target.is_deleted
    ):
        raise AppException(status.HTTP_404_NOT_FOUND, "target_not_available")

    # 2b. Feed guard — target must have been served in user's feed
    await _assert_profile_in_feed(user.id, target_id, redis_client, db, lang)

    # 3. Quota daily_likes
    is_premium = bool(user.is_premium)
    quota_key = "daily_likes_premium" if is_premium else "daily_likes_free"
    quota = int(await get_config(quota_key, redis_client, db))
    used = await _get_daily_likes_used(user.id, redis_client)
    if used >= quota:
        raise FlaamError(
            "daily_likes_exhausted", 429, lang, limit=quota
        )

    liked_prompt = (body or {}).get("liked_prompt")

    # ── Targeted like (Feature A) ──
    # Lecture du flag synchrone : 1 appel config déjà en cache Redis.
    targeted_enabled = (
        await get_config("flag_targeted_likes_enabled", redis_client, db)
        >= 0.5
    )
    target_type = (
        (body or {}).get("target_type") if targeted_enabled else None
    )
    like_target_id = (
        (body or {}).get("target_id") if targeted_enabled else None
    )
    like_comment = (
        (body or {}).get("comment") if targeted_enabled else None
    )
    if target_type not in (None, "profile", "photo", "prompt"):
        target_type = None
        like_target_id = None
        like_comment = None
    # target_type == "profile" est équivalent au comportement par défaut.
    if target_type == "profile":
        target_type = None

    # 4. Cherche une réciproque pending (target m'a déjà liké)
    reciprocal_row = await db.execute(
        select(Match).where(
            Match.user_a_id == target_id,
            Match.user_b_id == user.id,
            Match.status == "pending",
        )
    )
    reciprocal = reciprocal_row.scalar_one_or_none()

    match_result_kind: str
    match_obj: Match

    if reciprocal is not None:
        # ── Match mutuel : on promeut la row existante ─────────────────
        now = datetime.now(timezone.utc)
        reciprocal.status = "matched"
        reciprocal.matched_at = now
        reciprocal.expires_at = now + timedelta(days=MATCH_EXPIRE_DAYS)
        if liked_prompt and not reciprocal.liked_prompt_id:
            reciprocal.liked_prompt_id = liked_prompt[:50]
        match_obj = reciprocal
        match_result_kind = "matched"
        # Le like est de nous → on capture notre targeting sur le match
        # existant (qui avait l'autre user comme user_a).
        if target_type and not reciprocal.like_target_type:
            reciprocal.like_target_type = target_type
            reciprocal.like_target_id = (like_target_id or "")[:100] or None
            reciprocal.like_comment = like_comment
    else:
        # ── Check si j'ai déjà liké cette personne (idem sans key) ─────
        mine_row = await db.execute(
            select(Match).where(
                Match.user_a_id == user.id,
                Match.user_b_id == target_id,
            )
        )
        mine = mine_row.scalar_one_or_none()
        if mine is not None and mine.status in ("pending", "matched"):
            # Déjà liké — pas de double consommation de quota
            response = {
                "status": "already_liked",
                "match_id": mine.id,
                "ice_breaker": None,
                "remaining_likes": max(0, quota - used),
            }
            await _store_idempotent_response(
                "like", user.id, idem_key, response, redis_client
            )
            return response

        if mine is not None and mine.status == "skipped":
            # Changement d'avis : on réactive en pending
            mine.status = "pending"
            mine.liked_prompt_id = liked_prompt[:50] if liked_prompt else None
            mine.like_target_type = target_type
            mine.like_target_id = (like_target_id or "")[:100] or None
            mine.like_comment = like_comment
            match_obj = mine
        else:
            match_obj = Match(
                user_a_id=user.id,
                user_b_id=target_id,
                status="pending",
                liked_prompt_id=liked_prompt[:50] if liked_prompt else None,
                like_target_type=target_type,
                like_target_id=(like_target_id or "")[:100] or None,
                like_comment=like_comment,
            )
            db.add(match_obj)
        match_result_kind = "liked"

    # ── Feature B : A/B prompts — incrément passif ──
    # Quand le like cible un prompt, on incrémente prompt.like_count
    # dans le JSONB du profile du target. Tracking, pas de feature flag.
    if target_type == "prompt" and like_target_id:
        await _increment_prompt_like_count(target_id, like_target_id, db)

    await db.flush()

    # 5. Ice-breaker si match mutuel
    ice_breaker: str | None = None
    if match_result_kind == "matched":
        # Feature A : si l'un des deux a laissé un comment, il devient
        # l'ice-breaker (priorité au comment du user_a = liker initial).
        if match_obj.like_comment:
            ice_breaker = match_obj.like_comment
        else:
            # Liker du prompt = user_a_id (celui qui a liké en premier)
            # Le recipient = user courant
            liker_id = match_obj.user_a_id
            recipient_id = match_obj.user_b_id
            liker = await _load_user_full(liker_id, db)
            recipient = await _load_user_full(recipient_id, db)
            if liker and recipient:
                ice_breaker = await generate_icebreaker(
                    match_obj, liker, recipient, db
                )

    # 6. INCR quota (like effectif seulement)
    new_used = await _incr_daily_likes(user.id, redis_client)
    remaining = max(0, quota - new_used)

    # 7. Behavior tracking (câblage S6 point A)
    config = await get_configs(_BEHAVIOR_CONFIG_KEYS, redis_client, db)
    await update_behavior_on_action(
        user.id, "like", {"target": str(target_id)}, redis_client, db, config
    )
    if match_result_kind == "matched":
        # Le liker initial obtient un "match_created"
        await update_behavior_on_action(
            match_obj.user_a_id,
            "match_created",
            {"match_id": str(match_obj.id)},
            redis_client,
            db,
            config,
        )

    response = {
        "status": match_result_kind,
        "match_id": match_obj.id if match_result_kind == "matched" else None,
        "ice_breaker": ice_breaker,
        "remaining_likes": remaining,
    }

    await db.commit()
    await _store_idempotent_response(
        "like", user.id, idem_key, response, redis_client
    )
    # Invalidation du cache feed : le target ne doit plus réapparaître.
    await cache_invalidate(
        FEED_CACHE_KEY.format(user_id=str(user.id)), redis_client
    )

    log.info(
        "feed_like",
        user_id=str(user.id),
        target_id=str(target_id),
        status=match_result_kind,
        remaining=remaining,
    )
    return response


# ══════════════════════════════════════════════════════════════════════
# POST /feed/{id}/skip
# ══════════════════════════════════════════════════════════════════════


async def skip_profile(
    user: User,
    target_id: UUID,
    body: dict,
    idem_key: str | None,
    db: AsyncSession,
    redis_client: aioredis.Redis,
) -> dict:
    cached = await _get_idempotent_response(
        "skip", user.id, idem_key, redis_client
    )
    if cached is not None:
        return cached

    if target_id == user.id:
        raise AppException(status.HTTP_400_BAD_REQUEST, "cannot_skip_self")

    # Check existant
    existing_row = await db.execute(
        select(Match).where(
            Match.user_a_id == user.id, Match.user_b_id == target_id
        )
    )
    existing = existing_row.scalar_one_or_none()

    if existing is not None and existing.status == "skipped":
        status_str = "already_skipped"
        created = existing.created_at
    elif existing is not None and existing.status in ("pending", "matched"):
        raise AppException(
            status.HTTP_400_BAD_REQUEST, "cannot_skip_after_like"
        )
    else:
        now = datetime.now(timezone.utc)
        db.add(
            Match(
                user_a_id=user.id,
                user_b_id=target_id,
                status="skipped",
            )
        )
        await db.flush()
        created = now
        status_str = "skipped"

    reason = (body or {}).get("reason")
    db.add(
        BehaviorLog(
            user_id=user.id,
            event_type="skip",
            target_user_id=target_id,
            extra_data={"reason": reason} if reason else None,
        )
    )

    config = await get_configs(_BEHAVIOR_CONFIG_KEYS, redis_client, db)
    await update_behavior_on_action(
        user.id,
        "skip",
        {"target": str(target_id), "reason": reason},
        redis_client,
        db,
        config,
    )

    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    reappear = (created + timedelta(days=MATCHING_SKIP_COOLDOWN_DAYS)).date()

    response = {
        "status": status_str,
        "will_reappear_after": reappear.isoformat(),
    }
    await db.commit()
    await _store_idempotent_response(
        "skip", user.id, idem_key, response, redis_client
    )
    # Invalidation du cache feed : le target skippé ne doit plus réapparaître.
    await cache_invalidate(
        FEED_CACHE_KEY.format(user_id=str(user.id)), redis_client
    )
    return response


# ══════════════════════════════════════════════════════════════════════
# POST /feed/{id}/view
# ══════════════════════════════════════════════════════════════════════


async def log_view(
    user: User,
    target_id: UUID,
    body: dict,
    db: AsyncSession,
    redis_client: aioredis.Redis,
) -> None:
    if target_id == user.id:
        return

    duration = float((body or {}).get("duration_seconds") or 0.0)
    scrolled = bool((body or {}).get("scrolled_full") or False)
    prompts_viewed = int((body or {}).get("prompts_viewed") or 0)

    db.add(
        BehaviorLog(
            user_id=user.id,
            event_type="view",
            target_user_id=target_id,
            duration_seconds=duration,
            extra_data={
                "scrolled_full": scrolled,
                "prompts_viewed": prompts_viewed,
            },
        )
    )

    config = await get_configs(_BEHAVIOR_CONFIG_KEYS, redis_client, db)
    await update_behavior_on_action(
        user.id,
        "profile_viewed",
        {
            "target": str(target_id),
            "duration_s": duration,
            "scrolled_full": scrolled,
        },
        redis_client,
        db,
        config,
    )

    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# GET /matches/likes-received (2-tier : free preview / premium complet)
# ══════════════════════════════════════════════════════════════════════


def _first_letter(name: str | None) -> str:
    if not name:
        return "?"
    return name.strip()[:1].upper() or "?"


def _first_thumbnail(user_obj: User) -> str | None:
    for p in sorted(
        [p for p in (user_obj.photos or []) if p.moderation_status != "rejected"],
        key=lambda ph: ph.display_order,
    ):
        return p.thumbnail_url
    return None


async def get_likes_received(
    user: User,
    db: AsyncSession,
    redis_client: aioredis.Redis,
    lang: str = "fr",
) -> dict:
    """
    Mode 2-tier (voir docs/flaam-business-model.md).

    - Free   : total_count + 3 aperçus floutés + message bilingue.
    - Premium: total_count + profils complets FeedProfileItem.

    Filtrage commun : exclut les likers que j'ai déjà skippés ou matchés
    de mon côté. Tri par plus récent.
    """
    pending_rows = await db.execute(
        select(Match)
        .where(
            Match.user_b_id == user.id,
            Match.status == "pending",
        )
        .order_by(Match.created_at.desc())
        .limit(200)  # surdimensionne avant filtre mine
    )
    pending_matches = pending_rows.scalars().all()

    if not pending_matches:
        is_premium = bool(user.is_premium)
        if is_premium:
            return {
                "is_premium_user": True,
                "total_count": 0,
                "profiles": [],
            }
        return {
            "is_premium_user": False,
            "total_count": 0,
            "preview": [],
            "message": t("likes_received_empty", lang),
        }

    liker_ids = [m.user_a_id for m in pending_matches]

    # Exclure les gens avec qui j'ai déjà une action (skip/pending/matched)
    mine_rows = await db.execute(
        select(Match.user_b_id, Match.status).where(
            Match.user_a_id == user.id,
            Match.user_b_id.in_(liker_ids),
            Match.status.in_(("pending", "matched", "skipped")),
        )
    )
    acted = {r[0] for r in mine_rows.all()}

    # Liste des likers à afficher, ordre conservé (plus récent d'abord)
    filtered: list[UUID] = []
    for m in pending_matches:
        if m.user_a_id in acted:
            continue
        filtered.append(m.user_a_id)

    is_premium = bool(user.is_premium)

    if is_premium:
        me_full = await _load_user_full(user.id, db)
        others = await _load_users_full(filtered[:50], db)
        items: list[dict] = []
        for uid in filtered[:50]:
            other = others.get(uid)
            if other is None or other.profile is None:
                continue
            if not other.is_visible or other.is_banned or other.is_deleted:
                continue
            items.append(
                _hydrate_profile(
                    me_full or user,
                    other,
                    is_wildcard=False,
                    is_new_user=False,
                )
            )
        return {
            "is_premium_user": True,
            "total_count": len(filtered),
            "profiles": items,
        }

    # Free : preview floutée des 3 plus récents
    preview_ids = filtered[:3]
    preview_users = await _load_users_full(preview_ids, db)
    preview: list[dict] = []
    for uid in preview_ids:
        other = preview_users.get(uid)
        if other is None or other.profile is None:
            continue
        if not other.is_visible or other.is_banned or other.is_deleted:
            continue
        # Thumbnail 150px comme "flou" server-side (MVP, voir business-model).
        preview.append(
            {
                "blurred_photo_url": _first_thumbnail(other),
                "first_letter": _first_letter(other.profile.display_name),
            }
        )

    total = len(filtered)
    return {
        "is_premium_user": False,
        "total_count": total,
        "preview": preview,
        "message": t("likes_received_free", lang, count=total),
    }


__all__ = [
    "get_daily_feed",
    "get_crossed_feed",
    "like_profile",
    "skip_profile",
    "log_view",
    "get_likes_received",
]
