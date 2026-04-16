from __future__ import annotations

"""
Moderation service — point de bascule rules → IA (§18, ai-scoping §1).

POINT DE BASCULE UNIQUE : ``check_message()``.

Aujourd'hui (MVP) : règles keyword-based, synchrones, < 5ms.
Demain (Phase 1 IA) : remplaçable par un appel LLM externe sans
toucher aux appelants. Voir docs/flaam-ai-scoping.md section 1 pour
la stratégie complète.

Config ENV : ``MESSAGE_MODERATION_MODE`` (rules | llm_api | off).
- ``rules`` (MVP)   : applique les règles ci-dessous.
- ``llm_api`` (futur) : délègue à un LLM externe (même signature).
- ``off``           : auto-approve (tests).

La signature publique est stable : ne modifiez jamais les paramètres
ni le type de retour sans auditer tous les appelants.
"""

import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from app.core.config import get_settings

settings = get_settings()


ModerationAction = Literal["allow", "block", "flag_for_review", "log"]


class ModerationResult(BaseModel):
    """Résultat de ``check_message`` — structure stable pour tous les modes."""

    allowed: bool
    reason: str | None = None
    action: ModerationAction
    user_message_fr: str | None = None
    user_message_en: str | None = None


# ── Règles MVP (rules mode) ──────────────────────────────────────────

# Liens : seuls ces domaines sont tolérés.
ALLOWED_DOMAINS: frozenset[str] = frozenset(
    {"flaam.app", "maps.google.com", "goo.gl", "instagram.com"}
)

_URL_PATTERN = re.compile(
    r"(?:https?://|www\.)[^\s]+|(?:bit\.ly|tinyurl\.com|t\.co)/\S+",
    re.IGNORECASE,
)
_DOMAIN_PATTERN = re.compile(r"(?:https?://)?(?:www\.)?([^/\s]+)", re.IGNORECASE)

# Demandes d'argent — flag_for_review (pas bloquer).
MONEY_KEYWORDS: tuple[str, ...] = (
    "envoie moi", "envoie-moi", "send me",
    "western union", "moneygram",
    "mon compte", "my account",
    "momo", "tmoney", "wave", "orange money",
    "j'ai besoin d'argent", "i need money",
    "aide moi financ", "aide-moi financ",
    "numero de compte", "numéro de compte", "account number", "iban",
    "transfert", "transfer",
)

# Numéros de téléphone — log (pas bloquer).
_PHONE_PATTERN = re.compile(r"\+?\d[\d\s\-]{7,14}\d")

# Insultes — block. Liste volontairement courte et lexicale ; une vraie
# couverture arrivera avec le LLM (ai-scoping §1).
INSULT_WORDS: frozenset[str] = frozenset(
    {
        # FR
        "connard", "connasse", "salope", "pute", "putain", "enculé", "enculee",
        "encule", "tapette", "pd", "bâtard", "batard",
        # EN
        "bitch", "whore", "slut", "faggot", "motherfucker", "fucker",
        "cunt", "asshole", "bastard",
    }
)

_WORD_BOUNDARY = re.compile(r"[a-zà-ÿ]+", re.IGNORECASE)


def _extract_domain(url: str) -> str:
    m = _DOMAIN_PATTERN.match(url)
    if not m:
        return ""
    return m.group(1).lower().rstrip("/")


def _contains_insult(content_lower: str) -> bool:
    words = {w.lower() for w in _WORD_BOUNDARY.findall(content_lower)}
    return bool(words & INSULT_WORDS)


def _contains_money_keyword(content_lower: str) -> bool:
    return any(kw in content_lower for kw in MONEY_KEYWORDS)


def _contains_phone(content: str) -> bool:
    return bool(_PHONE_PATTERN.search(content))


def _contains_suspicious_link(content_lower: str) -> tuple[bool, str | None]:
    """Retourne (has_suspicious_link, first_suspicious_domain)."""
    urls = _URL_PATTERN.findall(content_lower)
    for url in urls:
        domain = _extract_domain(url)
        # Un domaine est whitelisté si lui ou son "parent" (last 2 segments)
        # est dans la liste.
        parts = domain.split(".")
        tail = ".".join(parts[-2:]) if len(parts) >= 2 else domain
        if domain not in ALLOWED_DOMAINS and tail not in ALLOWED_DOMAINS:
            return True, domain
    return False, None


# ── Point de bascule ─────────────────────────────────────────────────


async def check_message(
    content: str,
    sender_id: UUID,
    match_id: UUID,
    is_first_message: bool,
) -> ModerationResult:
    """
    Point d'entrée UNIQUE de la modération texte (§18).

    Voir docs/flaam-ai-scoping.md §1 : cette fonction est le point de
    bascule pour l'IA future. Sa signature reste stable ; seule
    l'implémentation interne évolue selon ``settings.message_moderation_mode``.

    Args:
        content: texte brut du message (pré-trim conseillé mais pas requis).
        sender_id: UUID de l'émetteur (utilisé par les modes async futurs).
        match_id: UUID du match (contextualisation LLM future).
        is_first_message: True si aucun message n'a encore été envoyé
            dans le match. Détermine l'agressivité de la détection de
            liens (1er message = pattern scam classique).

    Returns:
        ModerationResult. Ne lève jamais d'exception.
    """
    mode = settings.message_moderation_mode

    if mode == "off":
        return ModerationResult(allowed=True, action="allow")

    if mode == "rules":
        return _check_message_rules(content, is_first_message)

    # Mode llm_api : à implémenter en Phase 1 (ai-scoping §1).
    # Fallback rules si non câblé pour ne pas casser la prod.
    return _check_message_rules(content, is_first_message)


def _check_message_rules(content: str, is_first_message: bool) -> ModerationResult:
    content_stripped = content.strip()
    content_lower = content_stripped.lower()

    # 1. Insultes → block (priorité la plus haute : ne jamais laisser passer).
    if _contains_insult(content_lower):
        return ModerationResult(
            allowed=False,
            reason="insult",
            action="block",
            user_message_fr="Ce message contient des propos inappropriés.",
            user_message_en="This message contains inappropriate language.",
        )

    # 2. Liens suspects
    has_bad_link, _ = _contains_suspicious_link(content_lower)
    if has_bad_link:
        # Au 1er message, c'est un pattern scam classique → block dur.
        # Ailleurs : block aussi (la whitelist couvre les cas légitimes).
        reason = "suspicious_link_first_message" if is_first_message else "suspicious_link"
        return ModerationResult(
            allowed=False,
            reason=reason,
            action="block",
            user_message_fr=(
                "Les liens ne sont pas autorisés dans les messages pour ta "
                "sécurité."
            ),
            user_message_en="Links are not allowed in messages for your safety.",
        )

    # 3. Demandes d'argent → flag (on laisse passer, équipe review).
    if _contains_money_keyword(content_lower):
        return ModerationResult(
            allowed=True,
            reason="potential_scam",
            action="flag_for_review",
        )

    # 4. Numéros de téléphone → log (choix personnel).
    if _contains_phone(content_stripped):
        return ModerationResult(
            allowed=True,
            reason="phone_shared",
            action="log",
        )

    return ModerationResult(allowed=True, action="allow")


__all__ = [
    "ModerationAction",
    "ModerationResult",
    "ALLOWED_DOMAINS",
    "MONEY_KEYWORDS",
    "INSULT_WORDS",
    "check_message",
]
