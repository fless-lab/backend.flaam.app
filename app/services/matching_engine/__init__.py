from __future__ import annotations

"""
Matching engine — pipeline L1→L5 + préférences implicites + first-impression.

Point d'entrée : `generate_feed_for_user(user_id, db, redis)`.
"""

from app.services.matching_engine.pipeline import generate_feed_for_user

__all__ = ["generate_feed_for_user"]
