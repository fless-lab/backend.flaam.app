from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    app_name: str = "flaam"
    app_env: str = "development"
    app_debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_workers: int = 4
    api_v1_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:3000"

    # Security
    secret_key: str = "change-me"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7
    jwt_algorithm: str = "HS256"
    otp_length: int = 6
    otp_expire_seconds: int = 600
    otp_max_attempts: int = 3
    otp_cooldown_seconds: int = 60

    # Database
    database_url: str = "postgresql+asyncpg://flaam:password@db:5432/flaam"
    database_pool_size: int = 20
    database_max_overflow: int = 10
    database_echo: bool = False

    # Redis
    redis_url: str = "redis://redis:6379/0"
    redis_feed_db: int = 1
    redis_cache_db: int = 2

    # Celery (broker + result backend sur Redis, DB 3) — pas de RabbitMQ.
    celery_broker_url: str = "redis://redis:6379/3"
    celery_result_backend: str = "redis://redis:6379/3"

    # R2 Storage (Session 11 — stub pour l'instant)
    r2_endpoint: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_photos: str = "flaam-photos"
    r2_bucket_voice: str = "flaam-voice"
    cdn_base_url: str = "https://cdn.flaam.app"

    # Local storage (MVP photos — remplacé par R2 en Session 11)
    # STORAGE_ROOT est monté via docker-compose sur /app/uploads.
    storage_root: str = "/app/uploads"
    public_base_url: str = "http://localhost:8000"

    # Photo constraints (§3.7, §5.3)
    photo_max_size_bytes: int = 10 * 1024 * 1024  # 10 MB
    photo_min_count: int = 2  # minimum pour passer l'onboarding
    photo_max_count_free: int = 3  # plafond user free
    photo_max_count_premium: int = 6  # plafond user premium
    # Legacy — env var PHOTO_MAX_COUNT. Conservé pour compat mais
    # non utilisé : photo_service.upload_photo route via free/premium.
    photo_max_count: int = 6

    # Selfie verification (§13 SELFIE_VERIFICATION)
    # Passe à True quand le pipeline liveness (Session 11 / ML Kit) est
    # câblé. En MVP on accepte tel quel mais on loggue la photo comme
    # pending modération asynchrone (§17).
    selfie_liveness_required: bool = False

    # Photo moderation pipeline (§16.1b)
    # Modes : manual | onnx | external | off
    #   - manual   : photo reste pending jusqu'à action admin (MVP Lomé)
    #   - onnx     : worker Celery, modèles NSFW + face detection locaux
    #   - external : API tierce (Sightengine / Google Vision)
    #   - off      : auto-approve (tests/dev uniquement)
    photo_moderation_mode: str = "manual"
    nsfw_model_path: str = "/models/nsfw_detector.onnx"
    face_model_path: str = "/models/yunet_face.onnx"
    nsfw_threshold_reject: float = 0.7
    nsfw_threshold_review: float = 0.4
    # Face verification (§10.2, InsightFace ArcFace ONNX)
    face_verification_enabled: bool = True
    face_verification_model_path: str = "/models/arcface_r50.onnx"

    sightengine_user: str = ""
    sightengine_secret: str = ""
    sightengine_models: str = "nudity-2.0,face-attributes"

    # SMS (Termii — unique provider au MVP)
    termii_api_key: str = ""
    termii_sender_id: str = "Flaam"
    termii_base_url: str = "https://api.ng.termii.com"
    termii_sandbox: bool = False
    # Mode dev : on loggue simplement le code OTP sans appeler Termii
    sms_simulate: bool = True

    # Rate limiting OTP (spec §15, §5.1)
    rate_limit_otp_per_window: int = 3
    rate_limit_otp_window_seconds: int = 600

    # Payment (Paystack)
    paystack_secret_key: str = ""
    paystack_webhook_secret: str = ""
    paystack_base_url: str = "https://api.paystack.co"
    # En dev : initialize_payment retourne une fausse URL et le endpoint
    # POST /subscriptions/webhook/simulate est exposé. En prod : 404.
    paystack_simulate: bool = True

    # FCM (Firebase Cloud Messaging)
    fcm_service_account_json: str = ""
    # Au MVP : FCM_ENABLED=false → send_push log seulement, pas d'appel réseau.
    fcm_enabled: bool = False
    firebase_project_id: str = ""

    # Frontend (liens dans emails, WhatsApp teasers, QR event URLs)
    frontend_base_url: str = "https://flaam.app"

    # App versioning (§27, GET /config/version)
    app_min_version: str = "1.0.0"
    app_current_version: str = "1.0.0"
    app_force_update: bool = False
    app_update_url: str = "https://play.google.com/store/apps/details?id=app.flaam"

    # Matching
    matching_batch_hour: int = 3
    matching_feed_size: int = 12
    matching_wildcard_count: int = 2
    matching_new_user_boost_days: int = 10
    matching_match_expire_days: int = 7
    matching_skip_cooldown_days: int = 30
    matching_min_weekly_visibility: int = 15

    # Rate limiting
    rate_limit_default: int = 60
    rate_limit_premium: int = 120
    rate_limit_likes_free: int = 5
    rate_limit_likes_premium: int = 50

    # Chat — messages & WebSocket (S7)
    message_moderation_mode: str = "rules"  # rules | llm_api | off
    voice_max_size_bytes: int = 2 * 1024 * 1024  # 2 MB
    voice_max_duration_seconds: int = 60
    ws_heartbeat_seconds: int = 30
    ws_online_ttl_seconds: int = 300
    ws_idle_timeout_seconds: int = 60

    # Sentry
    sentry_dsn: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
