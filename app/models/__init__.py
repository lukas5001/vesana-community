"""SQLAlchemy models package.

All models must be imported here so that Alembic autogenerate and
``Base.metadata.create_all`` (and the test fixtures) can discover every table.
"""

from app.models.answer import Answer
from app.models.community_event import CommunityEvent
from app.models.community_profile import CommunityProfile
from app.models.community_profile_version import CommunityProfileVersion
from app.models.instance import Instance
from app.models.moderation_report import ModerationReport
from app.models.profile_comment import ProfileComment
from app.models.question import Question
from app.models.used_login_token import UsedLoginToken
from app.models.vote import Vote

__all__ = [
    "Instance",
    "UsedLoginToken",
    "CommunityEvent",
    "CommunityProfile",
    "CommunityProfileVersion",
    "Vote",
    "ProfileComment",
    "ModerationReport",
    "Question",
    "Answer",
]
