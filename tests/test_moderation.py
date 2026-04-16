from __future__ import annotations

"""
Tests moderation_service.check_message (S7, §18).

Cible le point de bascule. Ne touche pas à la DB ni à Redis.
"""

from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.services import moderation_service
from app.services.moderation_service import ModerationResult, check_message

pytestmark = pytest.mark.asyncio(loop_scope="session")


# Des UUIDs random suffisent : check_message ne lit pas la DB en mode rules.
_SENDER = uuid4()
_MATCH = uuid4()


async def _check(content: str, *, is_first: bool = False) -> ModerationResult:
    return await check_message(
        content=content,
        sender_id=_SENDER,
        match_id=_MATCH,
        is_first_message=is_first,
    )


# ══════════════════════════════════════════════════════════════════════
# Mode rules (défaut)
# ══════════════════════════════════════════════════════════════════════


async def test_moderation_allows_normal_message():
    r = await _check("Salut ! Tu vas souvent au Café 21 ?")
    assert r.allowed is True
    assert r.action == "allow"
    assert r.reason is None


async def test_moderation_blocks_insult():
    r = await _check("T'es une pute")
    assert r.allowed is False
    assert r.action == "block"
    assert r.reason == "insult"
    assert r.user_message_fr is not None
    assert r.user_message_en is not None


async def test_moderation_blocks_insult_english():
    r = await _check("You bitch")
    assert r.allowed is False
    assert r.action == "block"
    assert r.reason == "insult"


async def test_moderation_flags_money_request():
    r = await _check("Envoie moi 5000 via orange money stp", is_first=False)
    assert r.allowed is True  # flag mais pas bloqué
    assert r.action == "flag_for_review"
    assert r.reason == "potential_scam"


async def test_moderation_flags_money_english():
    r = await _check("I need money, send me via western union")
    assert r.allowed is True
    assert r.action == "flag_for_review"


async def test_moderation_logs_phone_number():
    r = await _check("Mon numéro c'est +22890123456")
    assert r.allowed is True
    assert r.action == "log"
    assert r.reason == "phone_shared"


async def test_moderation_blocks_link_first_message():
    r = await _check("Salut, clique ici https://evil.com/promo", is_first=True)
    assert r.allowed is False
    assert r.action == "block"
    assert r.reason in ("suspicious_link_first_message", "suspicious_link")


async def test_moderation_blocks_link_non_first_message():
    r = await _check("Check out this site www.badsite.xyz/deal", is_first=False)
    assert r.allowed is False
    assert r.action == "block"


async def test_moderation_allows_whitelisted_link():
    r = await _check("Rejoins-moi ici https://maps.google.com/?q=Lome", is_first=False)
    assert r.allowed is True
    assert r.action == "allow"


async def test_moderation_allows_flaam_link():
    r = await _check("Check https://flaam.app/event/123", is_first=True)
    assert r.allowed is True
    assert r.action == "allow"


async def test_moderation_insult_wins_over_link():
    """Si un message contient insulte ET lien, on renvoie insult (priorité)."""
    r = await _check("Salope va sur https://evil.com", is_first=False)
    assert r.reason == "insult"
    assert r.action == "block"


# ══════════════════════════════════════════════════════════════════════
# Mode off
# ══════════════════════════════════════════════════════════════════════


async def test_moderation_off_mode_allows_everything(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "message_moderation_mode", "off")
    # Même un message qui serait bloqué en rules doit passer.
    r = await _check("salope https://evil.com", is_first=True)
    assert r.allowed is True
    assert r.action == "allow"
    assert r.reason is None


# ══════════════════════════════════════════════════════════════════════
# Structure de retour
# ══════════════════════════════════════════════════════════════════════


async def test_moderation_result_is_pydantic_model():
    r = await _check("Hello")
    assert isinstance(r, ModerationResult)
    # Sérialisable
    dumped = r.model_dump()
    assert "allowed" in dumped
    assert "action" in dumped
