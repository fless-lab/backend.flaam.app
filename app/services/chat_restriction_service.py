from __future__ import annotations

"""
Anti-scam — restriction des 3 premiers messages.

Contexte produit (notes/scam_detect.txt + roadmap-irl-loop.md) :
- Afrique de l'Ouest : scammers tentent immédiatement de récupérer
  numéro/lien/argent dans les 1-2 premiers messages.
- C'est un killer de rétention féminine (les femmes désinstallent au
  premier message bizarre).
- Solution : bloquer les patterns scam pendant les 3 premiers messages
  de l'expéditeur. Après 3 messages échangés, on assume une vraie
  conversation et on lève la restriction.

Patterns bloqués pendant les 3 premiers messages :
- Numéros de téléphone (international + local Togo/CI/SN/etc.)
- URLs (sauf flaam.app)
- Mots clés argent/transaction
- Tentatives de redirection vers WhatsApp/Telegram explicites

Après 3 messages : `moderation_service` standard reprend (modération
"insult" / "suspicious_link" déjà existante, plus laxe).
"""

import re
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.message import Message


# Threshold : restriction lève après 3 messages SENT par cet expéditeur
# dans cette conversation (compteur par-sender, pas global match).
RESTRICTION_THRESHOLD = 3


# ── Patterns scam (insensible casse) ────────────────────────────────


# Numéros internationaux et locaux. Patterns larges :
# +228 / +225 / +221 / 00228... + 8 chiffres locaux
_PHONE_PATTERNS = [
    r"\+\d{1,3}[\s\-]?\d{6,12}",       # +228 90123456
    r"\b00\d{8,12}\b",                  # 00228 90123456
    r"\b\d{8}\b",                       # 90123456 (Togo: 8 chiffres)
    r"\b\d{2}[\s\-\.]\d{2}[\s\-\.]\d{2}[\s\-\.]\d{2}\b",  # 90 12 34 56
]

# URLs sauf flaam.app
_URL_PATTERN = re.compile(
    r"\bhttps?://(?!(www\.)?flaam\.app)\S+|\bwww\.\S+\.\S+",
    re.IGNORECASE,
)

# Mots clés argent / transaction / redirection externe.
_SCAM_KEYWORDS = [
    r"\bargent\b",
    r"\bmoney\b",
    r"\bwari\b",
    r"\bmtn[\s_-]?money\b",
    r"\bmoov[\s_-]?money\b",
    r"\borange[\s_-]?money\b",
    r"\bmobile[\s_-]?money\b",
    r"\benvoie\b.*\b(argent|money|fcfa|euro|dollar)\b",
    r"\bsend\b.*\b(money|argent|cash|fcfa)\b",
    r"\bpaiement\b",
    r"\bpayment\b",
    r"\bvirement\b",
    r"\btransfert\b",
    r"\b(whatsapp|wsp|telegram|signal|snapchat|snap)\b",
    r"\bnumber\b.*\bplease\b",
    r"\bnumero\b.*\b(stp|svp)\b",
]


_PHONE_RE = [re.compile(p, re.IGNORECASE) for p in _PHONE_PATTERNS]
_KEYWORD_RE = [re.compile(p, re.IGNORECASE) for p in _SCAM_KEYWORDS]


def detect_scam_pattern(content: str) -> str | None:
    """
    Retourne le nom du pattern matché si scam détecté, None sinon.
    Utilisé pour logging et message d'erreur user-friendly.
    """
    for pat in _PHONE_RE:
        if pat.search(content):
            return "phone_number"
    if _URL_PATTERN.search(content):
        return "external_url"
    for pat in _KEYWORD_RE:
        if pat.search(content):
            return "scam_keyword"
    return None


async def count_messages_sent(
    match_id: UUID, sender_id: UUID, db: AsyncSession,
) -> int:
    """Compte messages envoyés par cet expéditeur dans ce match."""
    result = await db.execute(
        select(func.count(Message.id)).where(
            Message.match_id == match_id,
            Message.sender_id == sender_id,
        ),
    )
    return result.scalar_one() or 0


async def is_restricted(
    match_id: UUID, sender_id: UUID, db: AsyncSession,
) -> bool:
    """
    True si l'expéditeur est encore dans la fenêtre des 3 premiers
    messages → patterns scam bloqués. False après 3 messages envoyés.
    """
    sent = await count_messages_sent(match_id, sender_id, db)
    return sent < RESTRICTION_THRESHOLD


async def check_message(
    match_id: UUID, sender_id: UUID, content: str, db: AsyncSession,
) -> str | None:
    """
    Vérifie un message avant envoi. Retourne le pattern violé si bloqué,
    None si OK.

    Usage côté chat_service.send_message — à appeler AVANT le commit du
    message en DB, pour permettre au caller de raise FlaamError.
    """
    if not await is_restricted(match_id, sender_id, db):
        return None
    return detect_scam_pattern(content)
