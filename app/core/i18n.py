from __future__ import annotations

"""
Système i18n minimal pour Flaam (§21, CLAUDE.md principes produit).

Langue par défaut : FR (marché primaire — Togo, Côte d'Ivoire, Sénégal).
Langue secondaire : EN (Nigeria, Ghana — plus tard).

Source unique des messages utilisateur : toutes les erreurs métier et
templates de notifications passent par ``t(key, lang, **kwargs)``.

Principes produit respectés (CLAUDE.md) :
- Messages honnêtes, jamais manipulateurs.
- Pas de FOMO, pas de faux compte à rebours.
- Ton chaleureux mais direct.
"""


MESSAGES: dict[str, dict[str, str]] = {
    # ── Auth ──
    "otp_sent": {
        "fr": "Code envoye par {channel}. Verifie ton telephone.",
        "en": "Code sent via {channel}. Check your phone.",
    },
    "otp_invalid": {
        "fr": "Code incorrect. Reessaie.",
        "en": "Invalid code. Try again.",
    },
    "otp_expired": {
        "fr": "Code expire. Demande un nouveau code.",
        "en": "Code expired. Request a new one.",
    },
    "otp_rate_limited": {
        "fr": "Trop de tentatives. Reessaie dans {retry_after} secondes.",
        "en": "Too many attempts. Try again in {retry_after} seconds.",
    },
    "account_creation_blocked": {
        "fr": "Creation de compte impossible. Contacte le support.",
        "en": "Account creation blocked. Contact support.",
    },
    "token_expired": {
        "fr": "Session expiree. Reconnecte-toi.",
        "en": "Session expired. Please log in again.",
    },
    "token_invalid": {
        "fr": "Session invalide. Reconnecte-toi.",
        "en": "Invalid session. Please log in again.",
    },

    # ── Profile ──
    "gender_not_modifiable": {
        "fr": "Le genre ne peut pas etre modifie. Contacte le support.",
        "en": "Gender cannot be changed. Contact support.",
    },
    "profile_incomplete": {
        "fr": "Complete ton profil avant de continuer.",
        "en": "Complete your profile before continuing.",
    },
    "selfie_required": {
        "fr": "Prends un selfie pour verifier ton profil.",
        "en": "Take a selfie to verify your profile.",
    },
    "photo_limit_reached": {
        "fr": "Tu as atteint la limite de {limit} photos.",
        "en": "You've reached the {limit} photo limit.",
    },
    "invalid_display_name": {
        "fr": "Ce nom n'est pas autorise.",
        "en": "This name is not allowed.",
    },
    "profile_not_found": {
        "fr": "Profil introuvable.",
        "en": "Profile not found.",
    },

    # ── Feed / Matching ──
    "daily_likes_exhausted": {
        "fr": "Tu as utilise tes {limit} likes du jour. Reviens demain ou passe Premium.",
        "en": "You've used your {limit} daily likes. Come back tomorrow or go Premium.",
    },
    "premium_required": {
        "fr": "Cette fonctionnalite est reservee aux membres Premium.",
        "en": "This feature is for Premium members.",
    },
    "no_feed_available": {
        "fr": "Pas de profils disponibles pour le moment. Reviens plus tard.",
        "en": "No profiles available right now. Come back later.",
    },
    "already_liked": {
        "fr": "Tu as deja like ce profil.",
        "en": "You already liked this profile.",
    },
    "already_skipped": {
        "fr": "Tu as deja passe ce profil.",
        "en": "You already skipped this profile.",
    },
    "match_not_found": {
        "fr": "Match introuvable.",
        "en": "Match not found.",
    },

    # ── Chat ──
    "message_blocked_insult": {
        "fr": "Ton message contient du contenu inapproprie.",
        "en": "Your message contains inappropriate content.",
    },
    "message_blocked_link": {
        "fr": "Les liens ne sont pas autorises pour ta securite.",
        "en": "Links are not allowed for your safety.",
    },
    "message_flagged_money": {
        "fr": "Attention : ce type de message peut etre associe a une arnaque.",
        "en": "Warning: this type of message may be associated with a scam.",
    },
    "not_match_participant": {
        "fr": "Tu ne fais pas partie de cette conversation.",
        "en": "You are not part of this conversation.",
    },
    "match_expired": {
        "fr": "Ce match a expire.",
        "en": "This match has expired.",
    },

    # ── Safety ──
    "user_blocked": {
        "fr": "Cet utilisateur a ete bloque.",
        "en": "This user has been blocked.",
    },
    "user_unblocked": {
        "fr": "Cet utilisateur a ete debloque.",
        "en": "This user has been unblocked.",
    },
    "report_submitted": {
        "fr": "Signalement envoye. Merci de nous aider a garder Flaam safe.",
        "en": "Report submitted. Thanks for keeping Flaam safe.",
    },
    "emergency_timer_started": {
        "fr": "Timer d'urgence active. Si tu ne l'annules pas dans {hours}h, ton contact sera prevenu.",
        "en": "Emergency timer started. If not cancelled within {hours}h, your contact will be notified.",
    },
    "emergency_timer_cancelled": {
        "fr": "Timer d'urgence annule. Tout va bien.",
        "en": "Emergency timer cancelled. All good.",
    },
    "already_blocked": {
        "fr": "Cet utilisateur est deja bloque.",
        "en": "This user is already blocked.",
    },
    "cannot_block_self": {
        "fr": "Tu ne peux pas te bloquer toi-meme.",
        "en": "You cannot block yourself.",
    },

    # ── Subscription ──
    "premium_activated": {
        "fr": "Premium active ! Profite de tes avantages.",
        "en": "Premium activated! Enjoy your benefits.",
    },
    "premium_expired": {
        "fr": "Ton premium a expire. Tes quartiers et spots extras sont en pause.",
        "en": "Your premium has expired. Your extra quartiers and spots are paused.",
    },
    "payment_failed": {
        "fr": "Le paiement a echoue. Reessaie ou change de moyen de paiement.",
        "en": "Payment failed. Try again or use a different payment method.",
    },
    "payment_confirmed": {
        "fr": "Paiement confirme. Merci !",
        "en": "Payment confirmed. Thank you!",
    },
    "plan_not_found": {
        "fr": "Ce plan n'existe pas.",
        "en": "This plan does not exist.",
    },
    "already_premium": {
        "fr": "Tu es deja Premium.",
        "en": "You are already Premium.",
    },

    # ── Events ──
    "event_full": {
        "fr": "Cet evenement est complet.",
        "en": "This event is full.",
    },
    "event_not_found": {
        "fr": "Evenement introuvable.",
        "en": "Event not found.",
    },
    "event_checkin_success": {
        "fr": "Check-in confirme ! Amuse-toi bien.",
        "en": "Check-in confirmed! Have fun.",
    },
    "event_checkin_invalid_qr": {
        "fr": "QR code invalide ou expire.",
        "en": "Invalid or expired QR code.",
    },
    "already_registered": {
        "fr": "Tu es deja inscrit a cet evenement.",
        "en": "You are already registered for this event.",
    },

    # ── Likes received ──
    "likes_received_free": {
        "fr": "Tu as {count} personnes qui t'ont like. L'algorithme va les mettre progressivement dans ton feed si vous vous correspondez. Passe Premium si tu veux les voir maintenant.",
        "en": "{count} people have liked you. The algorithm will progressively add them to your feed if you match. Go Premium to see them now.",
    },
    "likes_received_empty": {
        "fr": "Personne ne t'a encore like. Ca viendra.",
        "en": "No one has liked you yet. It will come.",
    },

    # ── RGPD ──
    "account_deleted": {
        "fr": "Ton compte a ete supprime. Tes donnees seront effacees sous 30 jours.",
        "en": "Your account has been deleted. Your data will be erased within 30 days.",
    },
    "export_ready": {
        "fr": "Ton export est pret. Telecharge-le.",
        "en": "Your export is ready. Download it.",
    },
    "export_rate_limited": {
        "fr": "Un seul export par 24h. Reessaie demain.",
        "en": "One export per 24h. Try again tomorrow.",
    },

    # ── Notifications push (templates) ──
    "notif_new_match": {
        "fr": "C'est un match ! Toi et {name} vous etes trouves.",
        "en": "It's a match! You and {name} found each other.",
    },
    "notif_new_message": {
        "fr": "{name} : {preview}",
        "en": "{name}: {preview}",
    },
    "notif_reply_reminder": {
        "fr": "Tu n'as pas encore repondu a {name}.",
        "en": "You haven't replied to {name} yet.",
    },
    "notif_event_reminder": {
        "fr": "{event_name} dans 2h ! On t'attend.",
        "en": "{event_name} in 2 hours! We're waiting for you.",
    },
    "notif_daily_feed": {
        "fr": "Tes profils du jour sont prets !",
        "en": "Your daily profiles are ready!",
    },
    "notif_selfie_required": {
        "fr": "Reprends un selfie pour verifier ton profil.",
        "en": "Take a new selfie to verify your profile.",
    },
    "notif_premium_expired": {
        "fr": "Ton premium a expire. Tes extras sont en pause.",
        "en": "Your premium has expired. Your extras are paused.",
    },
    "notif_payment_confirmed": {
        "fr": "Premium active ! Profite de tes avantages.",
        "en": "Premium activated! Enjoy your benefits.",
    },
    "notif_safety_alert": {
        "fr": "Timer expire. Tout va bien ?",
        "en": "Timer expired. Are you OK?",
    },
    "notif_match_expiring": {
        "fr": "Ton match avec {name} expire demain. Envoie-lui un message !",
        "en": "Your match with {name} expires tomorrow. Send a message!",
    },
    "notif_likes_received_count": {
        "fr": "Tu plais : {count} personnes t'ont like cette semaine.",
        "en": "You're popular: {count} people liked you this week.",
    },

    # ── Admin ──
    "admin_required": {
        "fr": "Acces reserve aux administrateurs.",
        "en": "Admin access required.",
    },
    "user_not_found": {
        "fr": "Utilisateur introuvable.",
        "en": "User not found.",
    },
    "report_not_found": {
        "fr": "Signalement introuvable.",
        "en": "Report not found.",
    },
}


def t(key: str, lang: str = "fr", **kwargs) -> str:
    """
    Traduit une cle en la langue demandee.

    Fallbacks :
    - Langue inconnue → FR.
    - Cle inconnue → retourne la cle brute (utile pour debug).
    - Formatting KeyError → retourne le template brut (non formatte).

    Exemples :
        t("otp_invalid")                    # FR par defaut
        t("otp_sent", "en", channel="SMS")  # "Code sent via SMS..."
    """
    entry = MESSAGES.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get("fr") or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


def detect_lang(request) -> str:
    """
    Detecte la langue depuis le header Accept-Language.

    Retourne "fr" ou "en". Defaut : "fr".

    Reconnait "en" si present dans la valeur Accept-Language
    (ex: "en-US,en;q=0.9,fr;q=0.8" → "en"). Tout le reste → "fr".
    """
    headers = getattr(request, "headers", None)
    if headers is None:
        return "fr"
    try:
        accept_val = headers.get("accept-language", "fr") or "fr"
    except (AttributeError, TypeError):
        return "fr"
    if "en" in accept_val.lower():
        return "en"
    return "fr"


__all__ = ["MESSAGES", "t", "detect_lang"]
