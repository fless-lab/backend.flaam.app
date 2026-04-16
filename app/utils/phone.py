from __future__ import annotations

import hashlib
import re

from app.core.config import get_settings

settings = get_settings()


class InvalidPhoneError(ValueError):
    """Numéro de téléphone invalide (format ou longueur)."""


def normalize_phone(phone: str) -> str:
    """
    Normalise un numéro en E.164 compact.

    Ex : "+228 90 12 34 56" → "+22890123456"

    Règles :
    - Doit commencer par "+" suivi d'un indicatif pays
    - 8 à 15 chiffres au total après le "+"
    - Espaces, tirets et parenthèses tolérés en entrée, retirés en sortie
    """
    if phone is None:
        raise InvalidPhoneError("Phone is required")
    normalized = re.sub(r"[\s\-\(\)]", "", phone)
    if not normalized.startswith("+"):
        raise InvalidPhoneError("Phone must start with country code (+228…)")
    digits = normalized[1:]
    if not digits.isdigit() or not (8 <= len(digits) <= 15):
        raise InvalidPhoneError("Invalid phone number format")
    return normalized


def hash_phone(phone: str) -> str:
    """
    Hash SHA-256 salé (spec §16).

    On ne stocke JAMAIS le numéro en clair. Pas besoin de bcrypt —
    on ne compare pas en temps constant, juste des lookups par hash.

    Format du sel : `flaam:phone:{normalized}:{secret_key[:16]}`.
    """
    normalized = normalize_phone(phone)
    salted = f"flaam:phone:{normalized}:{settings.secret_key[:16]}"
    return hashlib.sha256(salted.encode()).hexdigest()


def country_code_from_phone(phone: str) -> str:
    """
    Extrait l'indicatif pays E.164 (sans le '+').

    MVP simpliste : on prend les 3 premiers chiffres. Couvre les indicatifs
    pays Afrique de l'Ouest (228 Togo, 225 CI, 221 SN, 229 BJ, 233 GH, 234 NG).
    """
    normalized = normalize_phone(phone)
    return normalized[1:4]
