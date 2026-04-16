"""initial_schema

Revision ID: 973ebe33a9aa
Revises:
Create Date: 2026-04-16 02:57:09.886497

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import geoalchemy2
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '973ebe33a9aa'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('account_histories',
    sa.Column('phone_hash', sa.String(length=128), nullable=False),
    sa.Column('device_fingerprints', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('total_accounts_created', sa.Integer(), nullable=False),
    sa.Column('total_accounts_deleted', sa.Integer(), nullable=False),
    sa.Column('total_bans', sa.Integer(), nullable=False),
    sa.Column('first_account_created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_account_created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_account_deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_ban_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_departure_reason', sa.String(length=30), nullable=True),
    sa.Column('risk_score', sa.Float(), nullable=False),
    sa.Column('current_restriction', sa.String(length=30), nullable=False),
    sa.Column('restriction_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('blocked_by_hashes', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('admin_notes', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_account_histories_phone_hash'), 'account_histories', ['phone_hash'], unique=False)
    op.create_index('ix_ah_devices', 'account_histories', ['device_fingerprints'], unique=False, postgresql_using='gin')
    op.create_index('ix_ah_phone', 'account_histories', ['phone_hash'], unique=False)
    op.create_index('ix_ah_risk', 'account_histories', ['risk_score'], unique=False)
    op.create_table('cities',
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('country_code', sa.String(length=2), nullable=False),
    sa.Column('country_name', sa.String(length=100), nullable=False),
    sa.Column('timezone', sa.String(length=50), nullable=False),
    sa.Column('currency_code', sa.String(length=3), nullable=False),
    sa.Column('premium_price_monthly', sa.Integer(), nullable=False),
    sa.Column('premium_price_weekly', sa.Integer(), nullable=False),
    sa.Column('min_weekly_visibility', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('matching_configs',
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('value', sa.Float(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('category', sa.String(length=30), nullable=False),
    sa.Column('min_value', sa.Float(), nullable=True),
    sa.Column('max_value', sa.Float(), nullable=True),
    sa.Column('updated_by', sa.String(length=100), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_index('ix_matching_config_category', 'matching_configs', ['category'], unique=False)
    op.create_table('city_launch_statuses',
    sa.Column('city_id', sa.UUID(), nullable=False),
    sa.Column('phase', sa.String(length=20), nullable=False),
    sa.Column('total_registered', sa.Integer(), nullable=False),
    sa.Column('male_registered', sa.Integer(), nullable=False),
    sa.Column('female_registered', sa.Integer(), nullable=False),
    sa.Column('launched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('waitlist_invites_total', sa.Integer(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['city_id'], ['cities.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('city_id')
    )
    op.create_table('quartiers',
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('city_id', sa.UUID(), nullable=False),
    sa.Column('latitude', sa.Float(), nullable=False),
    sa.Column('longitude', sa.Float(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['city_id'], ['cities.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('users',
    sa.Column('phone_hash', sa.String(length=128), nullable=False),
    sa.Column('phone_country_code', sa.String(length=5), nullable=False),
    sa.Column('is_phone_verified', sa.Boolean(), nullable=False),
    sa.Column('is_selfie_verified', sa.Boolean(), nullable=False),
    sa.Column('is_id_verified', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('is_visible', sa.Boolean(), nullable=False),
    sa.Column('is_premium', sa.Boolean(), nullable=False),
    sa.Column('is_banned', sa.Boolean(), nullable=False),
    sa.Column('ban_reason', sa.String(length=500), nullable=True),
    sa.Column('city_id', sa.UUID(), nullable=False),
    sa.Column('last_active_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_feed_generated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('language', sa.String(length=5), nullable=False),
    sa.Column('account_created_count', sa.Integer(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['city_id'], ['cities.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_users_city_active', 'users', ['city_id', 'last_active_at'], unique=False)
    op.create_index('ix_users_city_visible', 'users', ['city_id', 'is_visible', 'is_active'], unique=False)
    op.create_index(op.f('ix_users_phone_hash'), 'users', ['phone_hash'], unique=True)
    op.create_table('behavior_logs',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('event_type', sa.String(length=30), nullable=False),
    sa.Column('target_user_id', sa.UUID(), nullable=True),
    sa.Column('duration_seconds', sa.Float(), nullable=True),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_behavior_created', 'behavior_logs', ['created_at'], unique=False)
    op.create_index('ix_behavior_user_type', 'behavior_logs', ['user_id', 'event_type'], unique=False)
    op.create_table('blocks',
    sa.Column('blocker_id', sa.UUID(), nullable=False),
    sa.Column('blocked_id', sa.UUID(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['blocked_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['blocker_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_blocks_blocked', 'blocks', ['blocked_id'], unique=False)
    op.create_index('ix_blocks_blocker', 'blocks', ['blocker_id'], unique=False)
    op.create_index('uq_block', 'blocks', ['blocker_id', 'blocked_id'], unique=True)
    op.create_table('contact_blacklists',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('phone_hash', sa.String(length=128), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_contact_blacklist_phone', 'contact_blacklists', ['phone_hash'], unique=False)
    op.create_index('uq_contact_blacklist', 'contact_blacklists', ['user_id', 'phone_hash'], unique=True)
    op.create_table('devices',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('device_fingerprint', sa.String(length=256), nullable=False),
    sa.Column('platform', sa.String(length=10), nullable=False),
    sa.Column('fcm_token', sa.String(length=512), nullable=True),
    sa.Column('app_version', sa.String(length=20), nullable=True),
    sa.Column('os_version', sa.String(length=20), nullable=True),
    sa.Column('last_login_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('feed_caches',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('feed_date', sa.Date(), nullable=False),
    sa.Column('profile_ids', postgresql.ARRAY(sa.UUID()), nullable=False),
    sa.Column('wildcard_ids', postgresql.ARRAY(sa.UUID()), nullable=False),
    sa.Column('new_user_ids', postgresql.ARRAY(sa.UUID()), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_feed_date', 'feed_caches', ['feed_date'], unique=False)
    op.create_index('uq_feed_user_date', 'feed_caches', ['user_id', 'feed_date'], unique=True)
    op.create_table('matches',
    sa.Column('user_a_id', sa.UUID(), nullable=False),
    sa.Column('user_b_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('liked_prompt_id', sa.String(length=50), nullable=True),
    sa.Column('matched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_message_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('unmatched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('unmatched_by', sa.UUID(), nullable=True),
    sa.Column('geo_score', sa.Float(), nullable=True),
    sa.Column('lifestyle_score', sa.Float(), nullable=True),
    sa.Column('was_wildcard', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_a_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_b_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_matches_expires', 'matches', ['expires_at'], unique=False, postgresql_where="status = 'matched' AND expires_at IS NOT NULL")
    op.create_index('ix_matches_user_a', 'matches', ['user_a_id', 'status'], unique=False)
    op.create_index('ix_matches_user_b', 'matches', ['user_b_id', 'status'], unique=False)
    op.create_index('uq_match_pair', 'matches', ['user_a_id', 'user_b_id'], unique=True)
    op.create_table('notification_preferences',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('new_match', sa.Boolean(), nullable=False),
    sa.Column('new_message', sa.Boolean(), nullable=False),
    sa.Column('daily_feed', sa.Boolean(), nullable=False),
    sa.Column('events', sa.Boolean(), nullable=False),
    sa.Column('date_reminder', sa.Boolean(), nullable=False),
    sa.Column('weekly_digest', sa.Boolean(), nullable=False),
    sa.Column('daily_feed_hour', sa.Integer(), nullable=False),
    sa.Column('quiet_start_hour', sa.Integer(), nullable=False),
    sa.Column('quiet_end_hour', sa.Integer(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id')
    )
    op.create_table('photos',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('original_url', sa.String(length=500), nullable=False),
    sa.Column('thumbnail_url', sa.String(length=500), nullable=False),
    sa.Column('medium_url', sa.String(length=500), nullable=False),
    sa.Column('display_order', sa.Integer(), nullable=False),
    sa.Column('is_verified_selfie', sa.Boolean(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('file_size_bytes', sa.Integer(), nullable=False),
    sa.Column('moderation_status', sa.String(length=20), nullable=False),
    sa.Column('moderation_score', sa.Float(), nullable=True),
    sa.Column('rejection_reason', sa.String(length=200), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('display_order >= 0 AND display_order <= 5', name='ck_photo_order'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('profiles',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('display_name', sa.String(length=50), nullable=False),
    sa.Column('birth_date', sa.Date(), nullable=False),
    sa.Column('gender', sa.String(length=20), nullable=False),
    sa.Column('seeking_gender', sa.String(length=20), nullable=False),
    sa.Column('intention', sa.String(length=30), nullable=False),
    sa.Column('sector', sa.String(length=30), nullable=False),
    sa.Column('rhythm', sa.String(length=20), nullable=True),
    sa.Column('prompts', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('languages', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('seeking_age_min', sa.Integer(), nullable=False),
    sa.Column('seeking_age_max', sa.Integer(), nullable=False),
    sa.Column('profile_completeness', sa.Float(), nullable=False),
    sa.Column('behavior_multiplier', sa.Float(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('seeking_age_max >= seeking_age_min', name='ck_seeking_age_range'),
    sa.CheckConstraint('seeking_age_min >= 18', name='ck_seeking_age_min'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id')
    )
    op.create_table('quartier_proximities',
    sa.Column('quartier_a_id', sa.UUID(), nullable=False),
    sa.Column('quartier_b_id', sa.UUID(), nullable=False),
    sa.Column('proximity_score', sa.Float(), nullable=False),
    sa.Column('distance_km', sa.Float(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.CheckConstraint('proximity_score >= 0 AND proximity_score <= 1', name='ck_proximity_range'),
    sa.CheckConstraint('quartier_a_id < quartier_b_id', name='ck_quartier_order'),
    sa.ForeignKeyConstraint(['quartier_a_id'], ['quartiers.id'], ),
    sa.ForeignKeyConstraint(['quartier_b_id'], ['quartiers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_qp_quartier_a', 'quartier_proximities', ['quartier_a_id'], unique=False)
    op.create_index('ix_qp_quartier_b', 'quartier_proximities', ['quartier_b_id'], unique=False)
    op.create_index('uq_quartier_proximity', 'quartier_proximities', ['quartier_a_id', 'quartier_b_id'], unique=True)
    op.create_table('reports',
    sa.Column('reporter_id', sa.UUID(), nullable=False),
    sa.Column('reported_user_id', sa.UUID(), nullable=False),
    sa.Column('reason', sa.String(length=30), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('evidence_message_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.Column('resolved_by', sa.String(length=100), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['reported_user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['reporter_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_reports_reported', 'reports', ['reported_user_id'], unique=False)
    op.create_index('ix_reports_status', 'reports', ['status'], unique=False)
    op.create_table('spots',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('category', sa.String(length=30), nullable=False),
    sa.Column('city_id', sa.UUID(), nullable=False),
    sa.Column('location', geoalchemy2.types.Geometry(geometry_type='POINT', srid=4326, from_text='ST_GeomFromEWKT', name='geometry', nullable=False), nullable=False),
    sa.Column('latitude', sa.Float(), nullable=False),
    sa.Column('longitude', sa.Float(), nullable=False),
    sa.Column('address', sa.String(length=300), nullable=True),
    sa.Column('google_place_id', sa.String(length=200), nullable=True),
    sa.Column('total_checkins', sa.Integer(), nullable=False),
    sa.Column('total_users', sa.Integer(), nullable=False),
    sa.Column('is_verified', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_by_user_id', sa.UUID(), nullable=True),
    sa.Column('social_weight', sa.Float(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['city_id'], ['cities.id'], ),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_spots_city_active', 'spots', ['city_id', 'is_active'], unique=False)
    op.create_index('ix_spots_city_category', 'spots', ['city_id', 'category'], unique=False)
    op.create_index('ix_spots_location', 'spots', ['location'], unique=False, postgresql_using='gist')
    op.create_table('subscriptions',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('plan', sa.String(length=20), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('provider_subscription_id', sa.String(length=200), nullable=True),
    sa.Column('provider_customer_id', sa.String(length=200), nullable=True),
    sa.Column('payment_method', sa.String(length=30), nullable=False),
    sa.Column('amount', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('is_auto_renew', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id')
    )
    op.create_table('user_quartiers',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('quartier_id', sa.UUID(), nullable=False),
    sa.Column('relation_type', sa.String(length=15), nullable=False),
    sa.Column('is_primary', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['quartier_id'], ['quartiers.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_user_quartiers_quartier', 'user_quartiers', ['quartier_id'], unique=False)
    op.create_index('ix_user_quartiers_user_type', 'user_quartiers', ['user_id', 'relation_type'], unique=False)
    op.create_index('uq_user_quartier', 'user_quartiers', ['user_id', 'quartier_id', 'relation_type'], unique=True)
    op.create_table('events',
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('spot_id', sa.UUID(), nullable=False),
    sa.Column('city_id', sa.UUID(), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('category', sa.String(length=30), nullable=False),
    sa.Column('max_attendees', sa.Integer(), nullable=True),
    sa.Column('current_attendees', sa.Integer(), nullable=False),
    sa.Column('created_by_user_id', sa.UUID(), nullable=True),
    sa.Column('is_admin_created', sa.Boolean(), nullable=False),
    sa.Column('is_sponsored', sa.Boolean(), nullable=False),
    sa.Column('sponsor_name', sa.String(length=100), nullable=True),
    sa.Column('is_approved', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['city_id'], ['cities.id'], ),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['spot_id'], ['spots.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_events_active', 'events', ['is_active', 'is_approved', 'starts_at'], unique=False)
    op.create_index('ix_events_city_date', 'events', ['city_id', 'starts_at'], unique=False)
    op.create_table('messages',
    sa.Column('match_id', sa.UUID(), nullable=False),
    sa.Column('sender_id', sa.UUID(), nullable=False),
    sa.Column('message_type', sa.String(length=20), nullable=False),
    sa.Column('content', sa.Text(), nullable=True),
    sa.Column('media_url', sa.String(length=500), nullable=True),
    sa.Column('media_duration_seconds', sa.Integer(), nullable=True),
    sa.Column('meetup_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('is_read', sa.Boolean(), nullable=False),
    sa.Column('read_at', sa.String(), nullable=True),
    sa.Column('is_flagged', sa.Boolean(), nullable=False),
    sa.Column('flag_reason', sa.String(length=50), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['match_id'], ['matches.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_messages_match', 'messages', ['match_id', 'created_at'], unique=False)
    op.create_index('ix_messages_sender', 'messages', ['sender_id'], unique=False)
    op.create_table('payments',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('subscription_id', sa.UUID(), nullable=True),
    sa.Column('amount', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('provider_reference', sa.String(length=100), nullable=False),
    sa.Column('payment_method', sa.String(length=30), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('idempotency_key', sa.String(length=64), nullable=True),
    sa.Column('initialized_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('webhook_payload', sa.JSON(), nullable=True),
    sa.Column('failure_reason', sa.String(length=200), nullable=True),
    sa.Column('retry_count', sa.Integer(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_index(op.f('ix_payments_provider_reference'), 'payments', ['provider_reference'], unique=True)
    op.create_index('ix_payments_status', 'payments', ['status'], unique=False)
    op.create_index(op.f('ix_payments_user_id'), 'payments', ['user_id'], unique=False)
    op.create_table('user_spots',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('spot_id', sa.UUID(), nullable=False),
    sa.Column('checkin_count', sa.Integer(), nullable=False),
    sa.Column('last_checkin_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('first_checkin_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('fidelity_level', sa.String(length=20), nullable=False),
    sa.Column('fidelity_score', sa.Float(), nullable=False),
    sa.Column('is_visible', sa.Boolean(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['spot_id'], ['spots.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_user_spots_spot', 'user_spots', ['spot_id'], unique=False)
    op.create_index('ix_user_spots_user', 'user_spots', ['user_id'], unique=False)
    op.create_index('uq_user_spot', 'user_spots', ['user_id', 'spot_id'], unique=True)
    op.create_table('event_registrations',
    sa.Column('event_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_event_registration', 'event_registrations', ['event_id', 'user_id'], unique=True)


def downgrade() -> None:
    op.drop_index('uq_event_registration', table_name='event_registrations')
    op.drop_table('event_registrations')
    op.drop_index('uq_user_spot', table_name='user_spots')
    op.drop_index('ix_user_spots_user', table_name='user_spots')
    op.drop_index('ix_user_spots_spot', table_name='user_spots')
    op.drop_table('user_spots')
    op.drop_index(op.f('ix_payments_user_id'), table_name='payments')
    op.drop_index('ix_payments_status', table_name='payments')
    op.drop_index(op.f('ix_payments_provider_reference'), table_name='payments')
    op.drop_table('payments')
    op.drop_index('ix_messages_sender', table_name='messages')
    op.drop_index('ix_messages_match', table_name='messages')
    op.drop_table('messages')
    op.drop_index('ix_events_city_date', table_name='events')
    op.drop_index('ix_events_active', table_name='events')
    op.drop_table('events')
    op.drop_index('uq_user_quartier', table_name='user_quartiers')
    op.drop_index('ix_user_quartiers_user_type', table_name='user_quartiers')
    op.drop_index('ix_user_quartiers_quartier', table_name='user_quartiers')
    op.drop_table('user_quartiers')
    op.drop_table('subscriptions')
    op.drop_index('ix_spots_location', table_name='spots', postgresql_using='gist')
    op.drop_index('ix_spots_city_category', table_name='spots')
    op.drop_index('ix_spots_city_active', table_name='spots')
    op.drop_table('spots')
    op.drop_index('ix_reports_status', table_name='reports')
    op.drop_index('ix_reports_reported', table_name='reports')
    op.drop_table('reports')
    op.drop_index('uq_quartier_proximity', table_name='quartier_proximities')
    op.drop_index('ix_qp_quartier_b', table_name='quartier_proximities')
    op.drop_index('ix_qp_quartier_a', table_name='quartier_proximities')
    op.drop_table('quartier_proximities')
    op.drop_table('profiles')
    op.drop_table('photos')
    op.drop_table('notification_preferences')
    op.drop_index('uq_match_pair', table_name='matches')
    op.drop_index('ix_matches_user_b', table_name='matches')
    op.drop_index('ix_matches_user_a', table_name='matches')
    op.drop_index('ix_matches_expires', table_name='matches', postgresql_where="status = 'matched' AND expires_at IS NOT NULL")
    op.drop_table('matches')
    op.drop_index('uq_feed_user_date', table_name='feed_caches')
    op.drop_index('ix_feed_date', table_name='feed_caches')
    op.drop_table('feed_caches')
    op.drop_table('devices')
    op.drop_index('uq_contact_blacklist', table_name='contact_blacklists')
    op.drop_index('ix_contact_blacklist_phone', table_name='contact_blacklists')
    op.drop_table('contact_blacklists')
    op.drop_index('uq_block', table_name='blocks')
    op.drop_index('ix_blocks_blocker', table_name='blocks')
    op.drop_index('ix_blocks_blocked', table_name='blocks')
    op.drop_table('blocks')
    op.drop_index('ix_behavior_user_type', table_name='behavior_logs')
    op.drop_index('ix_behavior_created', table_name='behavior_logs')
    op.drop_table('behavior_logs')
    op.drop_index(op.f('ix_users_phone_hash'), table_name='users')
    op.drop_index('ix_users_city_visible', table_name='users')
    op.drop_index('ix_users_city_active', table_name='users')
    op.drop_table('users')
    op.drop_table('quartiers')
    op.drop_table('city_launch_statuses')
    op.drop_index('ix_matching_config_category', table_name='matching_configs')
    op.drop_table('matching_configs')
    op.drop_table('cities')
    op.drop_index('ix_ah_risk', table_name='account_histories')
    op.drop_index('ix_ah_phone', table_name='account_histories')
    op.drop_index('ix_ah_devices', table_name='account_histories', postgresql_using='gin')
    op.drop_index(op.f('ix_account_histories_phone_hash'), table_name='account_histories')
    op.drop_table('account_histories')
