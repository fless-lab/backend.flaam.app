from __future__ import annotations

"""
Tests Ice-breaker (§14).

Couvre les 3 étapes (extract / select / render) + les 7 niveaux de
priorité + un end-to-end.
"""

import random
from uuid import uuid4

import pytest

from app.services.icebreaker_service import (
    CommonQuartier,
    CommonSpot,
    LikedPrompt,
    MatchContext,
    PrioritySelection,
    extract_match_context,
    generate_icebreaker,
    render_template,
    select_priority,
)
from app.services.matching_engine import geo_scorer
from tests._feed_setup import seed_ama_and_kofi

@pytest.fixture(autouse=True)
def _reset_geo_cache():
    geo_scorer.reset_proximity_cache()
    yield
    geo_scorer.reset_proximity_cache()


# ══════════════════════════════════════════════════════════════════════
# Étape 2 — select_priority (pure, 7 niveaux)
# ══════════════════════════════════════════════════════════════════════


def _base_ctx(**overrides) -> MatchContext:
    defaults = dict(
        liker_display_name="Kofi",
        recipient_lang="fr",
        liked_prompt=None,
        common_spots_high=[],
        common_spots_low=[],
        common_tags_rare=[],
        common_tags_normal=[],
        common_quartiers=[],
    )
    defaults.update(overrides)
    return MatchContext(**defaults)


def test_select_priority_1_prompt_liked():
    ctx = _base_ctx(
        liked_prompt=LikedPrompt(question="Un dimanche", answer="Brunch"),
        common_spots_high=[
            CommonSpot(uuid4(), "Café 21", "cafe", max_fidelity_rank=3)
        ],  # même présent, prompt gagne
    )
    sel = select_priority(ctx)
    assert sel.level == 1
    assert sel.kind == "prompt_liked"
    assert sel.payload["question"] == "Un dimanche"


def test_select_priority_2_spot_common_high():
    ctx = _base_ctx(
        common_spots_high=[
            CommonSpot(uuid4(), "Café 21", "cafe", max_fidelity_rank=3)
        ],
        common_spots_low=[
            CommonSpot(uuid4(), "Chez Tonton", "restaurant", max_fidelity_rank=1)
        ],
    )
    sel = select_priority(ctx)
    assert sel.level == 2
    assert sel.kind == "spot_common_high"
    assert sel.payload["spot"] == "Café 21"


def test_select_priority_3_spot_common_low():
    ctx = _base_ctx(
        common_spots_low=[
            CommonSpot(uuid4(), "Chez Tonton", "restaurant", max_fidelity_rank=1)
        ],
        common_tags_rare=["art"],
    )
    sel = select_priority(ctx)
    assert sel.level == 3
    assert sel.kind == "spot_common_low"


def test_select_priority_4_tag_common_rare():
    ctx = _base_ctx(common_tags_rare=["art"], common_tags_normal=["foodie"])
    sel = select_priority(ctx)
    assert sel.level == 4
    assert sel.kind == "tag_common_rare"
    assert sel.payload["tag"] == "art"


def test_select_priority_5_tag_common_normal():
    ctx = _base_ctx(
        common_tags_normal=["foodie"],
        common_quartiers=[CommonQuartier(uuid4(), "Tokoin")],
    )
    sel = select_priority(ctx)
    assert sel.level == 5
    assert sel.kind == "tag_common_normal"


def test_select_priority_6_quartier_common():
    ctx = _base_ctx(common_quartiers=[CommonQuartier(uuid4(), "Tokoin")])
    sel = select_priority(ctx)
    assert sel.level == 6
    assert sel.kind == "quartier_common"
    assert sel.payload["quartier"] == "Tokoin"


def test_select_priority_7_fallback():
    ctx = _base_ctx()
    sel = select_priority(ctx)
    assert sel.level == 7
    assert sel.kind == "fallback"


# ══════════════════════════════════════════════════════════════════════
# Étape 3 — render_template
# ══════════════════════════════════════════════════════════════════════


def test_render_template_fills_placeholders():
    ctx = _base_ctx(
        liked_prompt=LikedPrompt(question="Un dimanche", answer="Brunch")
    )
    sel = PrioritySelection(
        level=1,
        kind="prompt_liked",
        payload={"liker": "Kofi", "question": "Un dimanche"},
    )
    out = render_template(sel, ctx)
    assert "Kofi" in out
    assert "Un dimanche" in out


def test_render_template_fallback_never_empty():
    ctx = _base_ctx()
    sel = PrioritySelection(level=7, kind="fallback", payload={})
    out = render_template(sel, ctx)
    assert out and len(out) > 5


def test_render_template_en_lang():
    ctx = _base_ctx(recipient_lang="en")
    sel = PrioritySelection(
        level=2, kind="spot_common_high", payload={"spot": "Café 21"}
    )
    out = render_template(sel, ctx, rng=random.Random(0))
    assert "Café 21" in out


# ══════════════════════════════════════════════════════════════════════
# Étape 1 + end-to-end
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio(loop_scope="session")
async def test_extract_context_finds_common_spot(db_session):
    """Ama + Kofi partagent Café 21 en 'regular' → common_spots_high non vide."""
    data = await seed_ama_and_kofi(db_session)
    from app.models.match import Match

    match = Match(
        user_a_id=data["ama"].id,
        user_b_id=data["kofi"].id,
        status="pending",
    )
    db_session.add(match)
    await db_session.flush()

    ctx = await extract_match_context(match, data["ama"], data["kofi"], db_session)
    # Café 21 fidelity_level="regular" des deux côtés → rank=2 ≥ threshold 2
    assert any(s.name == "Café 21" for s in ctx.common_spots_high)
    assert ctx.recipient_lang == "fr"
    assert ctx.liker_display_name == "Ama"


@pytest.mark.asyncio(loop_scope="session")
async def test_generate_icebreaker_end_to_end(db_session):
    """Flow complet : contexte extrait → priorité choisie → texte rendu."""
    data = await seed_ama_and_kofi(db_session)
    from app.models.match import Match

    match = Match(
        user_a_id=data["ama"].id,
        user_b_id=data["kofi"].id,
        status="matched",
    )
    db_session.add(match)
    await db_session.flush()

    text = await generate_icebreaker(
        match, data["ama"], data["kofi"], db_session,
        rng=random.Random(42),
    )
    # Priorité spot_common_high → "Café 21" doit apparaître
    assert "Café 21" in text
