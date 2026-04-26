"""Trace complet du matching pour Linda — bypass cache."""
import asyncio
from datetime import datetime
from sqlalchemy import select, delete
from app.db.session import async_session
from app.db.redis import redis_pool
from app.models.user import User
from app.models.profile import Profile
from app.models.feed_cache import FeedCache
from app.services.matching_engine.hard_filters import apply_hard_filters
from app.services.matching_engine.pipeline import generate_feed_for_user

LINDA_ID = "04d3d244-f52a-4126-b4d4-88124d6c7ac4"

async def main():
    await redis_pool.initialize()
    async with async_session() as db:
        linda = await db.get(User, LINDA_ID)
        print(f"\n=== LINDA ===")
        print(f"  city_id = {linda.city_id}")
        print(f"  active={linda.is_active} visible={linda.is_visible} selfie_verif={linda.is_selfie_verified}")
        if linda.profile:
            from datetime import date
            today = date.today()
            age = today.year - linda.profile.birth_date.year - (
                (today.month, today.day) < (linda.profile.birth_date.month, linda.profile.birth_date.day)
            )
            print(f"  age={age}, gender={linda.profile.gender}")
            print(f"  seeks: {linda.profile.seeking_gender} age {linda.profile.seeking_age_min}-{linda.profile.seeking_age_max}")

        print(f"\n=== HARD FILTER ===")
        candidate_ids = await apply_hard_filters(linda, db)
        print(f"  → {len(candidate_ids)} candidates")
        for cid in candidate_ids[:30]:
            r = await db.execute(
                select(Profile.display_name, Profile.gender, Profile.seeking_gender,
                       Profile.seeking_age_min, Profile.seeking_age_max, Profile.birth_date)
                .where(Profile.user_id == cid)
            )
            row = r.first()
            if row:
                from datetime import date
                today = date.today()
                cage = today.year - row[5].year - (
                    (today.month, today.day) < (row[5].month, row[5].day)
                )
                print(f"    {row[0]:14} {row[1]:6} ({cage}yo) seeks {row[2]:8} {row[3]}-{row[4]}")

        print(f"\n=== FULL PIPELINE (force regen) ===")
        await db.execute(delete(FeedCache).where(FeedCache.user_id == linda.id))
        await db.commit()
        redis = redis_pool.client
        result = await generate_feed_for_user(linda.id, db, redis)
        print(f"  result keys: {list(result.keys())}"); print(f"  raw result: {result}")
        for label, ids in [("TOP", result['top_ids']), ("WILD", result['wildcard_ids']), ("NEW", result['new_user_ids'])]:
            for pid in ids:
                r = await db.execute(select(Profile.display_name, Profile.gender).where(Profile.user_id == pid))
                row = r.first()
                if row:
                    print(f"    [{label}] {row[0]} ({row[1]})")
    pass  # cleanup

asyncio.run(main())
