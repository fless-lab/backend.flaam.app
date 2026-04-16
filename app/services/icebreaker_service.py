from __future__ import annotations

"""
Ice-breaker service (spec §14).

Génère le premier message contextuel d'un match mutuel.
Structuré en 3 étapes DISTINCTES pour permettre le swap LLM ultérieur
(AI scoping doc) :

  1. extract_match_context() — pure I/O DB, collecte les signaux.
     Retourne MatchContext (type stable = contrat pour l'étape 3).
  2. select_priority()       — pure fonction, applique la hiérarchie 1-7.
     Retourne PrioritySelection (type stable).
  3. render_template()       — pure fonction, rendu texte.
     Remplacable par render_llm(selection, ctx) sans toucher 1-2.

Hiérarchie de priorité (§14) :
  1 prompt_liked      — prompt explicitement liké par l'autre
  2 spot_common_high  — spot commun avec fidélité ≥ "regular"
  3 spot_common_low   — spot commun (fidélité < "regular")
  4 tag_common_rare   — tag commun rare (< 5% usage ville)
  5 tag_common_normal — tag commun standard
  6 quartier_common   — quartier commun
  7 fallback          — message générique (jamais vide)
"""

import random
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match import Match
from app.models.quartier import Quartier
from app.models.spot import Spot
from app.models.user import User
from app.models.user_quartier import UserQuartier
from app.models.user_spot import UserSpot


# ── Types stables (contrat d'entrée LLM future) ──────────────────────

PriorityKind = Literal[
    "prompt_liked",
    "spot_common_high",
    "spot_common_low",
    "tag_common_rare",
    "tag_common_normal",
    "quartier_common",
    "fallback",
]


@dataclass
class CommonSpot:
    spot_id: UUID
    name: str
    category: str
    max_fidelity_rank: int  # 0=declared, 1=confirmed, 2=regular, 3=regular_plus


@dataclass
class CommonQuartier:
    quartier_id: UUID
    name: str


@dataclass
class LikedPrompt:
    question: str
    answer: str


@dataclass
class MatchContext:
    """Snapshot des signaux du match, indépendant du rendu."""

    liker_display_name: str
    recipient_lang: str  # "fr" | "en"
    liked_prompt: LikedPrompt | None = None
    common_spots_high: list[CommonSpot] = field(default_factory=list)
    common_spots_low: list[CommonSpot] = field(default_factory=list)
    common_tags_rare: list[str] = field(default_factory=list)
    common_tags_normal: list[str] = field(default_factory=list)
    common_quartiers: list[CommonQuartier] = field(default_factory=list)


@dataclass
class PrioritySelection:
    level: int           # 1-7
    kind: PriorityKind
    payload: dict        # données rendues par l'étape 3 (spot, tag, quartier…)


# ── Constantes de rendu ──────────────────────────────────────────────

# Tags considérés rares au MVP (pre-analytics batch).
# En prod, cette liste sera calculée par la batch analytics ville par ville.
RARE_TAGS_DEFAULT: set[str] = {
    "bonsai",
    "writing",
    "diy",
    "volunteering",
    "cars",
    "photography",
    "art",
    "wellness",
}


FIDELITY_RANK: dict[str, int] = {
    "declared": 0,
    "confirmed": 1,
    "regular": 2,
    "regular_plus": 3,
}

FIDELITY_HIGH_THRESHOLD = 2  # ≥ "regular"


TEMPLATES: dict[str, dict[str, list[str]]] = {
    "fr": {
        "prompt_liked": [
            "💬 {liker} a aimé ta réponse à « {question} ». Raconte-lui en plus !",
            "💬 Ta réponse à « {question} » a attiré l'attention de {liker}. À vous de jouer !",
        ],
        "spot_common_high": [
            "📍 Vous êtes tous les deux des habitués de {spot}. C'est quoi votre commande préférée ?",
            "📍 {spot} c'est votre QG à tous les deux ! Vous vous êtes peut-être déjà croisés ?",
            "📍 Deux réguliers de {spot} qui se matchent — c'est quoi votre meilleur souvenir là-bas ?",
        ],
        "spot_common_low": [
            "📍 Vous fréquentez tous les deux {spot}. C'est quoi qui vous y attire ?",
            "📍 {spot} en commun ! Vous y allez plutôt le matin ou le soir ?",
        ],
        "tag_common_rare": [
            "🎯 Vous êtes tous les deux passionnés de {tag} — c'est pas si courant ! Racontez-vous ça.",
            "🎯 {tag} en commun ! C'est quoi qui vous a lancé là-dedans ?",
        ],
        "tag_common_normal": [
            "🎯 Vous partagez le goût pour {tag}. C'est quoi votre truc préféré dans ce domaine ?",
        ],
        "quartier_common": [
            "🏘️ Voisins de {quartier} ! C'est quoi votre endroit secret dans le coin ?",
            "🏘️ Vous êtes du côté de {quartier} tous les deux. Le meilleur spot du quartier ?",
        ],
        "fallback": [
            "👋 Vous avez matché ! Dites-vous bonjour et découvrez ce que vous avez en commun.",
            "👋 Premier pas fait ! Qui commence la conversation ?",
            "👋 Match ! Qu'est-ce qui vous a attiré dans le profil de l'autre ?",
        ],
    },
    "en": {
        "prompt_liked": [
            "💬 {liker} liked your answer to '{question}'. Tell them more!",
            "💬 Your answer to '{question}' caught {liker}'s eye. Your move!",
        ],
        "spot_common_high": [
            "📍 You're both regulars at {spot}. What's your go-to order?",
            "📍 {spot} is your shared spot! Have you maybe crossed paths before?",
        ],
        "spot_common_low": [
            "📍 You both go to {spot}. What draws you there?",
        ],
        "tag_common_rare": [
            "🎯 You're both into {tag} — that's rare! Tell each other about it.",
        ],
        "tag_common_normal": [
            "🎯 You share a love for {tag}. What's your favorite thing about it?",
        ],
        "quartier_common": [
            "🏘️ Neighbors in {quartier}! What's your secret spot in the area?",
        ],
        "fallback": [
            "👋 You matched! Say hi and find out what you have in common.",
            "👋 Match! What caught your eye about each other's profile?",
        ],
    },
}


# ══════════════════════════════════════════════════════════════════════
# Étape 1 — extraction du contexte (pure I/O, pas de décision)
# ══════════════════════════════════════════════════════════════════════


async def _fetch_common_spots(
    user_a_id: UUID, user_b_id: UUID, db: AsyncSession
) -> list[CommonSpot]:
    """
    Retourne les spots que les DEUX utilisateurs ont en commun (visible),
    avec la fidélité max des deux côtés.
    """
    # Sélectionne les UserSpot des deux users + Spot joint
    rows = await db.execute(
        select(
            UserSpot.user_id,
            UserSpot.spot_id,
            UserSpot.fidelity_level,
            Spot.name,
            Spot.category,
        )
        .join(Spot, Spot.id == UserSpot.spot_id)
        .where(
            UserSpot.user_id.in_([user_a_id, user_b_id]),
            UserSpot.is_visible.is_(True),
        )
    )

    # Regroupe par spot_id : on garde les spots vus par les deux users
    per_spot: dict[UUID, dict] = {}
    for user_id, spot_id, fidelity, name, category in rows.all():
        entry = per_spot.setdefault(
            spot_id,
            {"users": set(), "name": name, "category": category, "max_rank": 0},
        )
        entry["users"].add(user_id)
        rank = FIDELITY_RANK.get(fidelity or "declared", 0)
        if rank > entry["max_rank"]:
            entry["max_rank"] = rank

    out: list[CommonSpot] = []
    for spot_id, d in per_spot.items():
        if len(d["users"]) == 2:
            out.append(
                CommonSpot(
                    spot_id=spot_id,
                    name=d["name"],
                    category=d["category"],
                    max_fidelity_rank=d["max_rank"],
                )
            )
    return out


async def _fetch_common_quartiers(
    user_a_id: UUID, user_b_id: UUID, db: AsyncSession
) -> list[CommonQuartier]:
    """Quartiers partagés (tout relation_type)."""
    rows = await db.execute(
        select(UserQuartier.user_id, UserQuartier.quartier_id, Quartier.name)
        .join(Quartier, Quartier.id == UserQuartier.quartier_id)
        .where(UserQuartier.user_id.in_([user_a_id, user_b_id]))
    )

    per_q: dict[UUID, dict] = {}
    for user_id, q_id, name in rows.all():
        entry = per_q.setdefault(q_id, {"users": set(), "name": name})
        entry["users"].add(user_id)

    return [
        CommonQuartier(quartier_id=q_id, name=d["name"])
        for q_id, d in per_q.items()
        if len(d["users"]) == 2
    ]


def _find_liked_prompt(
    match: Match, recipient_profile_prompts: list[dict]
) -> LikedPrompt | None:
    """
    `Match.liked_prompt_id` contient la question littérale (string key stable
    cohérente avec FeedPromptEntry.prompt_id côté feed). On résout en parcourant
    les prompts du destinataire.
    """
    if not match.liked_prompt_id or not recipient_profile_prompts:
        return None
    key = match.liked_prompt_id
    for p in recipient_profile_prompts:
        if not isinstance(p, dict):
            continue
        # 2 conventions possibles : "prompt_id" explicite, sinon égalité question
        if p.get("prompt_id") == key or p.get("question") == key:
            return LikedPrompt(
                question=str(p.get("question", "")),
                answer=str(p.get("answer", "")),
            )
    return None


async def extract_match_context(
    match: Match,
    liker: User,
    recipient: User,
    db: AsyncSession,
    *,
    rare_tags: set[str] | None = None,
) -> MatchContext:
    """
    Étape 1 — collecte tous les signaux nécessaires à l'étape 2/3, sans
    décider. Aucune logique de priorité ici.
    """
    rare = rare_tags if rare_tags is not None else RARE_TAGS_DEFAULT

    liker_profile = liker.profile
    recipient_profile = recipient.profile
    if liker_profile is None or recipient_profile is None:
        # Pas de profil → contexte vide (fallback sera servi à l'étape 3)
        return MatchContext(
            liker_display_name=liker.id.hex[:6],
            recipient_lang=recipient.language or "fr",
        )

    liked_prompt = _find_liked_prompt(match, recipient_profile.prompts or [])

    common_spots = await _fetch_common_spots(liker.id, recipient.id, db)
    common_spots_high = [
        s for s in common_spots if s.max_fidelity_rank >= FIDELITY_HIGH_THRESHOLD
    ]
    common_spots_low = [
        s for s in common_spots if s.max_fidelity_rank < FIDELITY_HIGH_THRESHOLD
    ]

    liker_tags = set(liker_profile.tags or [])
    recipient_tags = set(recipient_profile.tags or [])
    common_tags = liker_tags & recipient_tags
    common_tags_rare = sorted(common_tags & rare)
    common_tags_normal = sorted(common_tags - rare)

    common_quartiers = await _fetch_common_quartiers(liker.id, recipient.id, db)

    lang = recipient.language if recipient.language in TEMPLATES else "fr"

    return MatchContext(
        liker_display_name=liker_profile.display_name,
        recipient_lang=lang,
        liked_prompt=liked_prompt,
        common_spots_high=common_spots_high,
        common_spots_low=common_spots_low,
        common_tags_rare=common_tags_rare,
        common_tags_normal=common_tags_normal,
        common_quartiers=common_quartiers,
    )


# ══════════════════════════════════════════════════════════════════════
# Étape 2 — sélection du niveau de priorité (pure, pas d'I/O)
# ══════════════════════════════════════════════════════════════════════


def select_priority(
    ctx: MatchContext, *, rng: random.Random | None = None
) -> PrioritySelection:
    """
    Étape 2 — applique la hiérarchie 1-7. Pure fonction.

    `rng` permet de rendre le choix déterministe dans les tests.
    """
    r = rng or random

    # 1. prompt_liked
    if ctx.liked_prompt is not None:
        return PrioritySelection(
            level=1,
            kind="prompt_liked",
            payload={
                "liker": ctx.liker_display_name,
                "question": ctx.liked_prompt.question,
            },
        )

    # 2. spot_common_high
    if ctx.common_spots_high:
        spot = r.choice(ctx.common_spots_high)
        return PrioritySelection(
            level=2, kind="spot_common_high", payload={"spot": spot.name}
        )

    # 3. spot_common_low
    if ctx.common_spots_low:
        spot = r.choice(ctx.common_spots_low)
        return PrioritySelection(
            level=3, kind="spot_common_low", payload={"spot": spot.name}
        )

    # 4. tag_common_rare
    if ctx.common_tags_rare:
        tag = r.choice(ctx.common_tags_rare)
        return PrioritySelection(
            level=4, kind="tag_common_rare", payload={"tag": tag}
        )

    # 5. tag_common_normal
    if ctx.common_tags_normal:
        tag = r.choice(ctx.common_tags_normal)
        return PrioritySelection(
            level=5, kind="tag_common_normal", payload={"tag": tag}
        )

    # 6. quartier_common
    if ctx.common_quartiers:
        q = r.choice(ctx.common_quartiers)
        return PrioritySelection(
            level=6, kind="quartier_common", payload={"quartier": q.name}
        )

    # 7. fallback
    return PrioritySelection(level=7, kind="fallback", payload={})


# ══════════════════════════════════════════════════════════════════════
# Étape 3 — rendu du template (remplacable par LLM)
# ══════════════════════════════════════════════════════════════════════


def render_template(
    selection: PrioritySelection,
    ctx: MatchContext,
    *,
    rng: random.Random | None = None,
) -> str:
    """
    Étape 3 — rendu texte par templates. Point d'extension : on peut
    substituer un `render_llm(selection, ctx)` sans rien toucher en amont.
    """
    r = rng or random
    templates = TEMPLATES.get(ctx.recipient_lang) or TEMPLATES["fr"]
    pool = templates.get(selection.kind) or templates["fallback"]
    template = r.choice(pool)

    # Le fallback n'a pas de placeholders — safe même si payload est vide.
    try:
        return template.format(**selection.payload)
    except KeyError:
        return r.choice(templates["fallback"])


# ══════════════════════════════════════════════════════════════════════
# Orchestrateur public
# ══════════════════════════════════════════════════════════════════════


async def generate_icebreaker(
    match: Match,
    liker: User,
    recipient: User,
    db: AsyncSession,
    *,
    rng: random.Random | None = None,
    rare_tags: set[str] | None = None,
) -> str:
    """
    Assemble les 3 étapes. Retourne le texte de l'ice-breaker.

    Contrat de swap LLM : on peut remplacer uniquement `render_template`
    par un appel à un modèle de langage en prenant `selection` et `ctx`
    comme entrées structurées.
    """
    ctx = await extract_match_context(
        match, liker, recipient, db, rare_tags=rare_tags
    )
    selection = select_priority(ctx, rng=rng)
    return render_template(selection, ctx, rng=rng)


__all__ = [
    "CommonSpot",
    "CommonQuartier",
    "LikedPrompt",
    "MatchContext",
    "PrioritySelection",
    "RARE_TAGS_DEFAULT",
    "FIDELITY_RANK",
    "TEMPLATES",
    "extract_match_context",
    "select_priority",
    "render_template",
    "generate_icebreaker",
]
