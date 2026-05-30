"""Q&A portal API (C5).

Writes require a Bearer API token (``get_current_instance``); reads use an
optional Bearer so ``my_vote``/``can_edit`` reflect the caller when present and
are neutral otherwise. ``is_vesana_team`` is stamped on a new question/answer
ONLY when a valid admin HTTP Basic credential rides along in the
``X-Admin-Authorization`` header (the user's own Bearer occupies ``Authorization``).
The same admin header gates the moderator-only duplicate-close.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.answer import Answer
from app.models.question import Question
from app.routers.community_interactions import (
    AdminFlag,
    CurrentInstance,
    DbDep,
    OptionalInstance,
)
from app.schemas.interactions import ReportIn, VoteIn, VoteResult
from app.schemas.qa import (
    AnswerIn,
    AnswerOut,
    QuestionDetail,
    QuestionEdit,
    QuestionIn,
    QuestionSummary,
    SimilarQuestion,
)
from app.services import qa as qa_service
from app.services import voting as voting_service
from app.services.comments import author_display, within_edit_window

router = APIRouter(tags=["qa"])


def _answer_out(
    db: Session,
    answer: Answer,
    caller_uuid: str | None,
    display_names: dict[str, str | None] | None = None,
) -> AnswerOut:
    if display_names is not None and answer.instance_uuid in display_names:
        display = author_display(display_names.get(answer.instance_uuid), answer.instance_uuid)
    else:
        display = qa_service.answer_author_display(db, answer)
    is_owner = caller_uuid is not None and caller_uuid == answer.instance_uuid
    can_edit = is_owner and within_edit_window(answer.created_at)
    return AnswerOut(
        id=answer.id,
        instance_uuid=answer.instance_uuid,
        author_display=display,
        is_vesana_team=answer.is_vesana_team,
        body_md=answer.body_md,
        vote_score=answer.vote_score,
        is_accepted=answer.is_accepted,
        my_vote=voting_service.get_my_vote(db, caller_uuid, "answer", answer.id),
        can_edit=can_edit,
        created_at=answer.created_at,
        updated_at=answer.updated_at,
    )


def _question_summary(
    db: Session,
    question: Question,
    display_names: dict[str, str | None] | None = None,
) -> QuestionSummary:
    if display_names is not None and question.instance_uuid in display_names:
        display = author_display(display_names.get(question.instance_uuid), question.instance_uuid)
    else:
        display = qa_service.question_author_display(db, question)
    return QuestionSummary(
        id=question.id,
        title_text=question.title_text,
        author_display=display,
        is_vesana_team=question.is_vesana_team,
        tags=list(question.tags or []),
        vote_score=question.vote_score,
        answer_count=question.answer_count,
        is_closed=question.is_closed,
        has_accepted=qa_service.has_accepted(question),
        created_at=question.created_at,
        profile_id=question.profile_id,
    )


def _question_detail(
    db: Session,
    question: Question,
    caller_uuid: str | None,
) -> QuestionDetail:
    answers = qa_service.sorted_answers(question)
    display_names = qa_service.display_names_for(
        db, list({a.instance_uuid for a in answers} | {question.instance_uuid})
    )
    is_owner = caller_uuid is not None and caller_uuid == question.instance_uuid
    can_edit = is_owner and within_edit_window(question.created_at)
    summary = _question_summary(db, question, display_names)
    return QuestionDetail(
        **summary.model_dump(),
        body_md=question.body_md,
        closed_reason=question.closed_reason,
        duplicate_of_id=question.duplicate_of_id,
        my_vote=voting_service.get_my_vote(db, caller_uuid, "question", question.id),
        can_edit=can_edit,
        answers=[_answer_out(db, a, caller_uuid, display_names) for a in answers],
    )


# ---- Questions -------------------------------------------------------------


@router.get("/api/v1/questions", response_model=list[QuestionSummary])
def list_questions(
    db: DbDep,
    instance: OptionalInstance,
    filter: str | None = None,
    tag: str | None = None,
    search: str | None = None,
    sort: str = qa_service.DEFAULT_SORT,
    limit: int = qa_service.DEFAULT_LIMIT,
    offset: int = 0,
) -> list[QuestionSummary]:
    questions, _ = qa_service.list_questions(
        db,
        filter_=filter,
        tag=tag,
        search=search,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    display_names = qa_service.display_names_for(db, [q.instance_uuid for q in questions])
    return [_question_summary(db, q, display_names) for q in questions]


@router.get("/api/v1/questions/similar", response_model=list[SimilarQuestion])
def similar(
    db: DbDep,
    title: str = "",
) -> list[SimilarQuestion]:
    matches = qa_service.similar_questions(db, title)
    return [
        SimilarQuestion(
            id=q.id,
            title_text=q.title_text,
            answer_count=q.answer_count,
            is_closed=q.is_closed,
        )
        for q in matches
    ]


@router.post(
    "/api/v1/questions",
    response_model=QuestionDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_question(
    payload: QuestionIn,
    instance: CurrentInstance,
    is_admin: AdminFlag,
    db: DbDep,
) -> QuestionDetail:
    question = qa_service.create_question(
        db,
        instance_uuid=instance.uuid,
        title_text=payload.title_text,
        body_md=payload.body_md,
        tags=payload.tags,
        profile_id=payload.profile_id,
        is_vesana_team=is_admin,
    )
    db.commit()
    return _question_detail(db, question, instance.uuid)


@router.get("/api/v1/questions/{question_id}", response_model=QuestionDetail)
def get_question(
    question_id: str,
    db: DbDep,
    instance: OptionalInstance,
) -> QuestionDetail:
    question = qa_service.get_question(db, question_id)
    if question is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")
    caller_uuid = instance.uuid if instance else None
    return _question_detail(db, question, caller_uuid)


@router.put("/api/v1/questions/{question_id}", response_model=QuestionDetail)
def edit_question(
    question_id: str,
    payload: QuestionEdit,
    instance: CurrentInstance,
    db: DbDep,
) -> QuestionDetail:
    question = qa_service.edit_question(
        db,
        question_id,
        instance.uuid,
        payload.title_text,
        payload.body_md,
        payload.tags,
    )
    db.commit()
    return _question_detail(db, question, instance.uuid)


# ---- Answers ---------------------------------------------------------------


@router.post(
    "/api/v1/questions/{question_id}/answers",
    response_model=AnswerOut,
    status_code=status.HTTP_201_CREATED,
)
def create_answer(
    question_id: str,
    payload: AnswerIn,
    instance: CurrentInstance,
    is_admin: AdminFlag,
    db: DbDep,
) -> AnswerOut:
    answer = qa_service.create_answer(
        db,
        question_id,
        instance.uuid,
        payload.body_md,
        is_vesana_team=is_admin,
    )
    db.commit()
    return _answer_out(db, answer, instance.uuid)


@router.put("/api/v1/answers/{answer_id}", response_model=AnswerOut)
def edit_answer(
    answer_id: str,
    payload: AnswerIn,
    instance: CurrentInstance,
    db: DbDep,
) -> AnswerOut:
    answer = qa_service.edit_answer(db, answer_id, instance.uuid, payload.body_md)
    db.commit()
    return _answer_out(db, answer, instance.uuid)


@router.post("/api/v1/answers/{answer_id}/accept", response_model=AnswerOut)
def accept_answer(
    answer_id: str,
    instance: CurrentInstance,
    db: DbDep,
) -> AnswerOut:
    answer = qa_service.accept_answer(db, answer_id, instance.uuid)
    db.commit()
    return _answer_out(db, answer, instance.uuid)


# ---- Votes -----------------------------------------------------------------


@router.post("/api/v1/questions/{question_id}/vote", response_model=VoteResult)
def vote_question(
    question_id: str,
    payload: VoteIn,
    instance: CurrentInstance,
    db: DbDep,
) -> VoteResult:
    score = voting_service.cast_vote(
        db, instance.uuid, "question", question_id, payload.value, payload.reason
    )
    db.commit()
    return VoteResult(
        target_type="question", target_id=question_id, value=payload.value, vote_score=score
    )


@router.delete("/api/v1/questions/{question_id}/vote", response_model=VoteResult)
def unvote_question(
    question_id: str,
    instance: CurrentInstance,
    db: DbDep,
) -> VoteResult:
    score = voting_service.remove_vote(db, instance.uuid, "question", question_id)
    db.commit()
    return VoteResult(target_type="question", target_id=question_id, value=0, vote_score=score)


@router.post("/api/v1/answers/{answer_id}/vote", response_model=VoteResult)
def vote_answer(
    answer_id: str,
    payload: VoteIn,
    instance: CurrentInstance,
    db: DbDep,
) -> VoteResult:
    score = voting_service.cast_vote(
        db, instance.uuid, "answer", answer_id, payload.value, payload.reason
    )
    db.commit()
    return VoteResult(
        target_type="answer", target_id=answer_id, value=payload.value, vote_score=score
    )


@router.delete("/api/v1/answers/{answer_id}/vote", response_model=VoteResult)
def unvote_answer(
    answer_id: str,
    instance: CurrentInstance,
    db: DbDep,
) -> VoteResult:
    score = voting_service.remove_vote(db, instance.uuid, "answer", answer_id)
    db.commit()
    return VoteResult(target_type="answer", target_id=answer_id, value=0, vote_score=score)


# ---- Moderation: duplicate-close + report ----------------------------------


class CloseDuplicateIn(BaseModel):
    """Body for closing a question as a duplicate (admin only)."""

    duplicate_of_id: str | None = None
    reason: str | None = None


@router.post("/api/v1/questions/{question_id}/close-duplicate", response_model=QuestionDetail)
def close_duplicate(
    question_id: str,
    payload: CloseDuplicateIn,
    instance: CurrentInstance,
    is_admin: AdminFlag,
    db: DbDep,
) -> QuestionDetail:
    question = qa_service.close_as_duplicate(
        db, question_id, is_admin, payload.duplicate_of_id, payload.reason
    )
    db.commit()
    return _question_detail(db, question, instance.uuid)


@router.post("/api/v1/questions/{question_id}/report")
def report_question(
    question_id: str,
    payload: ReportIn,
    instance: CurrentInstance,
    db: DbDep,
) -> dict[str, str]:
    moderation_report = qa_service.report(
        db, "question", question_id, instance.uuid, payload.reason
    )
    db.commit()
    return {"status": moderation_report.status}


@router.post("/api/v1/answers/{answer_id}/report")
def report_answer(
    answer_id: str,
    payload: ReportIn,
    instance: CurrentInstance,
    db: DbDep,
) -> dict[str, str]:
    moderation_report = qa_service.report(db, "answer", answer_id, instance.uuid, payload.reason)
    db.commit()
    return {"status": moderation_report.status}
