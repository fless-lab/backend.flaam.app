from __future__ import annotations

"""
Constantes du matching engine (spec §6, §6.3b/c, MàJ 5/6/7).

Deux familles de constantes :

1. MATCHING_DEFAULTS : valeurs scalaires (float) paramétrables à chaud
   via la table `matching_configs` et le `config_service`. Ce sont les
   valeurs de fallback lorsqu'aucun override n'existe en DB/Redis.

2. Dicts / matrices : non-paramétrables (MatchingConfig.value est Float
   par design). Les surcharges d'une matrice 4×4 ou d'un schedule
   imbriqué ne rentrent pas dans une colonne scalaire. Si le besoin se
   présente, on créera une table MatchingMatrix ou on passera value en
   JSONB (hors MVP).
"""


# ── Valeurs scalaires paramétrables (config_service → DB → ce fichier) ──

MATCHING_DEFAULTS: dict[str, float] = {
    # ── L2 géo ──
    # Poids des relations utilisateur↔quartier (L2 passe 1/2)
    "geo_w_quartier_lives": 2.0,
    "geo_w_quartier_works": 1.5,
    "geo_w_quartier_hangs": 1.0,
    "geo_w_quartier_interested": 0.8,
    # Seuil sous lequel on ignore la proximité (bruit des quartiers éloignés)
    "geo_proximity_threshold": 0.40,
    # Poids des composantes agrégées du score géo
    "geo_w_quartier": 0.45,
    "geo_w_spot": 0.30,
    "geo_w_fidelity": 0.15,
    "geo_w_freshness": 0.10,
    # Demi-vie (en jours) du décay exponentiel de fraîcheur des check-ins
    "freshness_decay_halflife_days": 30.0,

    # ── L3 lifestyle ──
    "lifestyle_w_tags": 0.50,
    "lifestyle_w_intention": 0.25,
    "lifestyle_w_rhythm": 0.15,
    "lifestyle_w_languages": 0.10,

    # ── L4 behavior ──
    "behavior_min_multiplier": 0.6,
    "behavior_max_multiplier": 1.4,
    # Sous-composantes (lerp min→max selon le signal)
    "behavior_response_min": 0.6,
    "behavior_response_max": 1.4,
    "behavior_selectivity_min": 0.7,
    "behavior_selectivity_max": 1.3,
    "behavior_richness_min": 0.8,
    "behavior_richness_max": 1.2,
    "behavior_depth_min": 0.8,
    "behavior_depth_max": 1.3,

    # ── Weight schedule adaptatif (MàJ 5) ──
    # 0-30j : nouveau, on se fie à la géo/déclarations
    "weight_geo_0_30": 0.55,
    "weight_lifestyle_0_30": 0.35,
    "weight_behavior_0_30": 0.10,
    # 30-90j : établi, le comportement commence à compter
    "weight_geo_30_90": 0.40,
    "weight_lifestyle_30_90": 0.30,
    "weight_behavior_30_90": 0.30,
    # 90j+ : mature, l'algo s'adapte à l'utilisateur
    "weight_geo_90_plus": 0.30,
    "weight_lifestyle_90_plus": 0.25,
    "weight_behavior_90_plus": 0.45,

    # ── Préférences implicites (MàJ 6) ──
    # Nombre minimum de signaux pour générer un profil implicite non-vide
    "implicit_confidence_threshold": 5.0,
    # Cap de l'ajustement sur le score L3
    "implicit_adjustment_cap": 0.15,
    # Cap logarithmique temps passé sur un profil
    "implicit_time_cap_seconds": 60.0,
    "implicit_time_min_seconds": 8.0,

    # ── L5 corrections ──
    "new_user_boost_3d": 3.0,
    "new_user_boost_7d": 2.0,
    "new_user_boost_10d": 1.5,
    "wildcard_count": 2.0,
    "new_user_boost_count": 2.0,

    # ── First impression (MàJ 7) ──
    "first_impression_active_feeds": 3.0,
    "first_impression_min_completeness": 0.75,
    "first_impression_min_behavior": 1.0,
    "first_impression_min_photos": 3.0,

    # ── Event boost ──
    "event_boost_points": 15.0,
    "event_boost_window_days": 7.0,
    "event_boost_decay_days": 14.0,

    # ── Likes limits (§5.6) ──
    "daily_likes_free": 5.0,
    # Likes/jour en premium : 10 (2x le free).
    # Choix assumé "qualité > volume" — voir docs/flaam-business-model.md.
    # Configurable en prod via admin API si besoin de passer à 15.
    "daily_likes_premium": 10.0,
}


# ── Matrices / dicts non paramétrables ──

# Compatibilité des intentions (L3, §6.3b).
# Symétrique. Valeurs 0-1. Plus c'est élevé, plus les intentions matchent.
INTENTION_COMPATIBILITY_MATRIX: dict[str, dict[str, float]] = {
    "serious": {
        "serious": 1.0,
        "getting_to_know": 0.5,
        "friendship_first": 0.1,
        "open": 0.7,
    },
    "getting_to_know": {
        "serious": 0.5,
        "getting_to_know": 1.0,
        "friendship_first": 0.6,
        "open": 0.8,
    },
    "friendship_first": {
        "serious": 0.1,
        "getting_to_know": 0.6,
        "friendship_first": 1.0,
        "open": 0.5,
    },
    "open": {
        "serious": 0.7,
        "getting_to_know": 0.8,
        "friendship_first": 0.5,
        "open": 1.0,
    },
}


# Mapping entre relation_type d'un UserQuartier et la clé MATCHING_DEFAULTS
# Utilisé par le geo_scorer pour regarder le bon poids via config_service.
QUARTIER_RELATION_WEIGHT_KEYS: dict[str, str] = {
    "lives": "geo_w_quartier_lives",
    "works": "geo_w_quartier_works",
    "hangs": "geo_w_quartier_hangs",
    "interested": "geo_w_quartier_interested",
}


# Poids social par catégorie de spot (L2).
# Un maquis = signal social fort ; un coworking = signal faible.
SPOT_SOCIAL_WEIGHTS: dict[str, float] = {
    "bar": 1.2,
    "maquis": 1.2,
    "restaurant": 1.1,
    "cafe": 1.0,
    "club": 1.3,
    "gym": 0.9,
    "coworking": 0.7,
    "market": 0.8,
    "park": 1.0,
    "beach": 1.1,
    "cultural": 1.0,
}


# Boost nouveaux profils (L5). Clé : intervalle (jours_min, jours_max).
# Valeur : facteur de boost (multiplicateur). Info seulement — les bornes
# sont hard-codées ici mais les valeurs peuvent venir de MATCHING_DEFAULTS
# (new_user_boost_3d / _7d / _10d).
NEW_USER_BOOST_BUCKETS: list[tuple[int, int, str]] = [
    (0, 3, "new_user_boost_3d"),
    (4, 7, "new_user_boost_7d"),
    (8, 10, "new_user_boost_10d"),
]


# ── Taille du feed et garde-fous (non paramétrable — contrat UX) ──

MATCHING_FEED_SIZE: int = 12
MATCHING_FEED_MIN_SIZE: int = 8
MATCHING_SKIP_COOLDOWN_DAYS: int = 30
MATCHING_ACTIVE_WINDOW_DAYS: int = 7


# Redis keys (templates)
REDIS_CONFIG_KEY = "matching:config:{key}"
REDIS_BEHAVIOR_KEY = "behavior:{user_id}"
REDIS_BEHAVIOR_STATS_KEY = "behavior_stats:{user_id}"
REDIS_IMPLICIT_PREFS_KEY = "implicit_prefs:{user_id}"
REDIS_VISIBILITY_KEY = "visibility:{user_id}"

REDIS_CONFIG_TTL_SECONDS = 3600           # 1h
REDIS_IMPLICIT_PREFS_TTL_SECONDS = 25 * 3600  # 25h (batch nocturne)
