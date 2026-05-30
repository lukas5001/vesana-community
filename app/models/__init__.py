"""Model package.

Importing every model here ensures they are all registered on
``Base.metadata`` so that Alembic autogenerate (and ``create_all`` in tests)
can see them.
"""

from app.models.instance import Instance
from app.models.used_login_token import UsedLoginToken

__all__ = ["Instance", "UsedLoginToken"]
