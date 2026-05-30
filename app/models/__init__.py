"""SQLAlchemy models package.

All models must be imported here so that Alembic autogenerate and
``Base.metadata.create_all`` (and the test fixtures) can discover every table.
"""

from app.models.community_profile import CommunityProfile
from app.models.community_profile_version import CommunityProfileVersion
from app.models.instance import Instance
from app.models.used_login_token import UsedLoginToken

__all__ = [
    "Instance",
    "UsedLoginToken",
    "CommunityProfile",
    "CommunityProfileVersion",
]
