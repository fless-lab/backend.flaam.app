from __future__ import annotations

from app.models.city import City
from app.models.quartier import Quartier
from app.models.quartier_proximity import QuartierProximity
from app.models.user import User
from app.models.device import Device
from app.models.profile import Profile
from app.models.photo import Photo
from app.models.spot import Spot
from app.models.user_spot import UserSpot
from app.models.user_quartier import UserQuartier
from app.models.match import Match
from app.models.message import Message
from app.models.event import Event
from app.models.event_registration import EventRegistration
from app.models.report import Report
from app.models.block import Block
from app.models.contact_blacklist import ContactBlacklist
from app.models.subscription import Subscription
from app.models.notification_preference import NotificationPreference
from app.models.behavior_log import BehaviorLog
from app.models.feed_cache import FeedCache
from app.models.matching_config import MatchingConfig
from app.models.account_history import AccountHistory
from app.models.city_launch_status import CityLaunchStatus
from app.models.payment import Payment
from app.models.waitlist_entry import WaitlistEntry
from app.models.invite_code import InviteCode
from app.models.emergency_contact import EmergencyContact
from app.models.emergency_session import EmergencySession
from app.models.daily_kpi import DailyKpi
from app.models.user_flame import UserFlame
from app.models.event_checkin import EventCheckin
from app.models.flame_scan_attempt import FlameScanAttempt

__all__ = [
    "City",
    "Quartier",
    "QuartierProximity",
    "User",
    "Device",
    "Profile",
    "Photo",
    "Spot",
    "UserSpot",
    "UserQuartier",
    "Match",
    "Message",
    "Event",
    "EventRegistration",
    "Report",
    "Block",
    "ContactBlacklist",
    "Subscription",
    "NotificationPreference",
    "BehaviorLog",
    "FeedCache",
    "MatchingConfig",
    "AccountHistory",
    "CityLaunchStatus",
    "Payment",
    "WaitlistEntry",
    "InviteCode",
    "EmergencyContact",
    "EmergencySession",
    "DailyKpi",
    "UserFlame",
    "EventCheckin",
    "FlameScanAttempt",
]
