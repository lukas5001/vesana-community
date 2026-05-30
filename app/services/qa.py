"""Q&A service: questions + answers (C5).

Voting reuses the unified votes table (``app.services.voting``). ``vote_score``
on questions/answers is a cached SUM kept in sync by that service. The question
``answer_count`` is recomputed on answer create/delete. Edit windows (24h) and
the ``author_display`` fallback are reused from ``app.services.comments``. New
answers / accepted answers fire the C6 notification seam.

Callers (the router) are responsible for committing; service functions flush so
generated ids/defaults are populated.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.answer import Answer
from app.models.instance import Instance
from app.models.moderation_report import ModerationReport
from app.models.question import Question
from app.services import notifications
from app.services.comments import author_display, within_edit_window

DEFAULT_LIMIT = 30
MAX_LIMIT = 100

SORT_OPTIONS = ("newest", "votes", "active")
DEFAULT_SORT = "newest"

# Filters supported by the question list.
FILTER_OPTIONS = ("open", "answered", "unanswered", "accepted")


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def _display_name_for(db: Session, instance_uuid: str) -> str | None:
    instance = db.get(Instance, instance_uuid)
    return instance.display_name if instance is not None else None


def display_names_for(db: Session, uuids: list[str]) -> dict[str, str | None]:
    if not uuids:
        return {}
    rows = db.execute(
        select(Instance.uuid, Instance.display_name).where(Instance.uuid.in_(uuids))
    ).all()
    return {uuid_: display_name for uuid_, display_name in rows}


def _require_question(db: Session, question_id: str) -> Question:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")
    return question


def _require_answer(db: Session, answer_id: str) -> Answer:
    answer = db.get(Answer, answer_id)
    if answer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Answer not found")
    return answer


def has_accepted(question: Question) -> bool:
    return any(a.is_accepted for a in question.answers)


# ---- Questions -------------------------------------------------------------


def create_question(
    db: Session,
    instance_uuid: str,
    title_text: str,
    body_md: str,
    tags: list[str],
    profile_id: str | None,
    is_vesana_team: bool,
) -> Question:
    question = Question(
        instance_uuid=instance_uuid,
        title_text=title_text,
        body_md=body_md,
        tags=tags or None,
        profile_id=profile_id,
        is_vesana_team=is_vesana_team,
    )
    db.add(question)
    db.flush()
    db.refresh(question)
    return question


def edit_question(
    db: Session,
    question_id: str,
    caller_uuid: str,
    title_text: str,
    body_md: str,
    tags: list[str],
) -> Question:
    question = _require_question(db, question_id)
    if question.instance_uuid != caller_uuid:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your question")
    if not within_edit_window(question.created_at):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Edit window (24h) has expired"
        )
    question.title_text = title_text
    question.body_md = body_md
    question.tags = tags or None
    db.flush()
    db.refresh(question)
    return question


def _question_sort_key(question: Question, sort: str):
    if sort == "votes":
        return (question.vote_score, question.created_at)
    if sort == "active":
        return (question.updated_at, question.created_at)
    # newest (default)
    return (question.created_at,)


def list_questions(
    db: Session,
    *,
    filter_: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    sort: str = DEFAULT_SORT,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> tuple[list[Question], int]:
    """Return (page_of_questions, total_matching).

    Sorting is done in Python so cached counters and the active-by-updated-at
    rule stay simple and identical between the JSON API and the HTML pages.
    """
    stmt = select(Question)
    if filter_ == "open":
        stmt = stmt.where(Question.is_closed.is_(False))
    elif filter_ == "answered":
        stmt = stmt.where(Question.answer_count > 0)
    elif filter_ == "unanswered":
        stmt = stmt.where(Question.answer_count == 0)
    elif filter_ == "accepted":
        stmt = stmt.where(
            Question.id.in_(select(Answer.question_id).where(Answer.is_accepted.is_(True)))
        )
    if tag:
        stmt = stmt.where(Question.tags.any(tag))  # type: ignore[attr-defined]
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            or_(
                Question.title_text.ilike(like),
                Question.body_md.ilike(like),
                Question.tags.any(search),  # type: ignore[attr-defined]
            )
        )

    sort = sort if sort in SORT_OPTIONS else DEFAULT_SORT
    limit = clamp_limit(limit)
    offset = max(0, offset)

    questions = list(db.execute(stmt).scalars().all())
    total = len(questions)
    questions.sort(key=lambda q: _question_sort_key(q, sort), reverse=True)
    page = questions[offset : offset + limit]
    return page, total


def get_question(db: Session, question_id: str) -> Question | None:
    return db.get(Question, question_id)


def sorted_answers(question: Question) -> list[Answer]:
    """Answers accepted-first, then higher vote_score, then oldest first."""
    return sorted(
        question.answers,
        key=lambda a: (0 if a.is_accepted else 1, -a.vote_score, a.created_at),
    )


def similar_questions(db: Session, title: str, limit: int = 5) -> list[Question]:
    title = (title or "").strip()
    if not title:
        return []
    like = f"%{title}%"
    stmt = (
        select(Question)
        .where(Question.title_text.ilike(like))
        .order_by(Question.created_at.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


# ---- Answers ---------------------------------------------------------------


def _recount_answers(db: Session, question: Question) -> None:
    total = db.execute(
        select(func.count()).select_from(Answer).where(Answer.question_id == question.id)
    ).scalar_one()
    question.answer_count = int(total)
    db.flush()


def create_answer(
    db: Session,
    question_id: str,
    instance_uuid: str,
    body_md: str,
    is_vesana_team: bool,
) -> Answer:
    question = _require_question(db, question_id)
    if question.is_closed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Question is closed; no new answers",
        )
    answer = Answer(
        question_id=question_id,
        instance_uuid=instance_uuid,
        body_md=body_md,
        is_vesana_team=is_vesana_team,
    )
    db.add(answer)
    db.flush()
    db.refresh(answer)
    db.refresh(question)
    _recount_answers(db, question)

    # New answer -> notify the question's author (skipped if they answered
    # their own question).
    notifications.enqueue(
        db,
        recipient_uuid=question.instance_uuid,
        actor_uuid=instance_uuid,
        type="qa_answer",
        payload={
            "question_id": question_id,
            "question_title": question.title_text,
            "answerer_display": author_display(_display_name_for(db, instance_uuid), instance_uuid),
        },
    )
    return answer


def edit_answer(
    db: Session,
    answer_id: str,
    caller_uuid: str,
    body_md: str,
) -> Answer:
    answer = _require_answer(db, answer_id)
    if answer.instance_uuid != caller_uuid:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your answer")
    if not within_edit_window(answer.created_at):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Edit window (24h) has expired"
        )
    answer.body_md = body_md
    db.flush()
    db.refresh(answer)
    return answer


def accept_answer(
    db: Session,
    answer_id: str,
    caller_uuid: str,
) -> Answer:
    answer = _require_answer(db, answer_id)
    question = _require_question(db, answer.question_id)
    if question.instance_uuid != caller_uuid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the question author may accept an answer",
        )
    # Exactly one accepted answer: flip every other answer false, then this one
    # true, all in the same transaction (mirrors the partial unique index).
    for other in question.answers:
        if other.id != answer.id and other.is_accepted:
            other.is_accepted = False
    db.flush()
    answer.is_accepted = True
    db.flush()
    db.refresh(answer)

    # Accepted -> notify the answer's author (skipped if they accept their own
    # answer to their own question).
    notifications.enqueue(
        db,
        recipient_uuid=answer.instance_uuid,
        actor_uuid=caller_uuid,
        type="answer_accepted",
        payload={
            "question_id": question.id,
            "question_title": question.title_text,
            "answer_id": answer.id,
        },
    )
    return answer


# ---- Moderation: duplicate-close + report ----------------------------------


def close_as_duplicate(
    db: Session,
    question_id: str,
    is_admin: bool,
    duplicate_of_id: str | None,
    reason: str | None,
) -> Question:
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an admin may close a question as duplicate",
        )
    question = _require_question(db, question_id)
    if duplicate_of_id is not None:
        if duplicate_of_id == question_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A question cannot be a duplicate of itself",
            )
        _require_question(db, duplicate_of_id)
    question.is_closed = True
    question.duplicate_of_id = duplicate_of_id
    question.closed_reason = reason
    db.flush()
    db.refresh(question)
    return question


def report(
    db: Session,
    target_type: str,
    target_id: str,
    reporter_uuid: str,
    reason: str,
) -> ModerationReport:
    if target_type == "question":
        _require_question(db, target_id)
    elif target_type == "answer":
        _require_answer(db, target_id)
    moderation_report = ModerationReport(
        target_type=target_type,
        target_id=target_id,
        instance_uuid=reporter_uuid,
        reason=reason,
    )
    db.add(moderation_report)
    db.flush()
    db.refresh(moderation_report)
    return moderation_report


# ---- Serialisation helpers (router-facing) ---------------------------------


def answer_author_display(db: Session, answer: Answer) -> str:
    return author_display(_display_name_for(db, answer.instance_uuid), answer.instance_uuid)


def question_author_display(db: Session, question: Question) -> str:
    return author_display(_display_name_for(db, question.instance_uuid), question.instance_uuid)
