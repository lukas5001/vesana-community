"""Pydantic schemas package.

The profile library schemas introduced in 0.2.0, the voting + comment schemas
added in 0.3.0 (C4) and the Q&A portal schemas added in 0.4.0 (C5). Auth
endpoints use inline request bodies / plain dicts, so there are no auth schemas
here.
"""

from app.schemas.interactions import (
    CommentEdit,
    CommentIn,
    CommentOut,
    CommentThread,
    HelpfulIn,
    ReportIn,
    VoteIn,
    VoteResult,
)
from app.schemas.profile import (
    VESANA_TEAM_UPLOADER,
    CheckPreview,
    ProfileDetail,
    ProfileListResponse,
    ProfileSummary,
    VersionSummary,
    check_preview_from_bundle,
)
from app.schemas.qa import (
    AnswerIn,
    AnswerOut,
    QuestionDetail,
    QuestionEdit,
    QuestionIn,
    QuestionSummary,
    SimilarQuestion,
)
from app.schemas.upload import (
    BundleUpload,
    RejectIn,
    ReviewItem,
    UploadResult,
)

__all__ = [
    "ProfileSummary",
    "ProfileListResponse",
    "ProfileDetail",
    "VersionSummary",
    "CheckPreview",
    "check_preview_from_bundle",
    "VESANA_TEAM_UPLOADER",
    "VoteIn",
    "VoteResult",
    "CommentIn",
    "CommentEdit",
    "CommentOut",
    "CommentThread",
    "HelpfulIn",
    "ReportIn",
    "QuestionIn",
    "QuestionEdit",
    "QuestionSummary",
    "QuestionDetail",
    "AnswerIn",
    "AnswerOut",
    "SimilarQuestion",
    "BundleUpload",
    "UploadResult",
    "ReviewItem",
    "RejectIn",
]
