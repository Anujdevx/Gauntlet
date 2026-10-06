"""Challenge browsing and submission intake.

The submission endpoint is the architectural centre of the platform, and the one
thing it must never do is *execute code*. It has no Docker client, no Celery app,
and no path to a container runtime. All it does is validate, persist, enqueue, and
return an opaque id.

Ordering matters and is deliberate:

1. Validate size and path safety. Cheapest rejection, no I/O.
2. Look up the challenge. Rejects an unknown id before any write.
3. **Enqueue before persisting the Submission row.**

Step 3 is the one worth defending. A worker can dequeue and finish before the API's
transaction commits, so persisting first would race: the worker would update a row
that does not exist yet, and the result would be lost. Enqueuing first means the
worst case is a worker that reports "no such submission", which is visible, rather
than a result silently dropped. The submission id is generated in the API and
carried in the message, so the worker never has to discover it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from oa_api.core.deps import CurrentUser, DbSession
from oa_api.models.tables import (
    ConceptNode,
    GeneratedQuestion,
    QuestionTemplate,
    QuestionTemplateNode,
    Submission,
    SubmissionMode,
)
from oa_api.schemas.challenge import (
    ChallengeDetailResponse,
    ChallengeEnvelope,
    ChallengeListResponse,
    ChallengeResponse,
    CreateSubmissionRequest,
    SkillListResponse,
    SkillNodeResponse,
    SubmissionAccepted,
    SubmissionStatusResponse,
)
from oa_api.services import submission_store
from oa_api.services.enqueue import enqueue_submission
from oa_engine.execution.payload import SubmissionPayload
from oa_engine.platform.logging import get_logger
from oa_engine.platform.messaging import SubmissionMessage

router = APIRouter(prefix="/api/v1", tags=["challenges"])
log = get_logger(__name__, service="oa-api")


@router.get("/challenges", response_model=ChallengeListResponse, summary="List challenges")
async def list_challenges(session: DbSession, user: CurrentUser) -> ChallengeListResponse:
    """Return every active challenge, ordered by difficulty then title.

    Auth required even though the data is not sensitive: it keeps the index behind a
    login so an unauthenticated visitor cannot enumerate the question bank.
    """
    rows = await session.execute(
        select(QuestionTemplate)
        .where(QuestionTemplate.is_active.is_(True))
        .order_by(QuestionTemplate.difficulty, QuestionTemplate.title)
    )
    templates = list(rows.scalars())
    node_maps = await _nodes_for(session, [t.id for t in templates])
    return ChallengeListResponse(
        challenges=[_to_challenge(t, node_maps.get(t.id, [])) for t in templates]
    )


@router.get(
    "/challenges/{slug}",
    response_model=ChallengeEnvelope,
    summary="Fetch one challenge",
)
async def get_challenge(slug: str, session: DbSession, user: CurrentUser) -> ChallengeEnvelope:
    """Return a challenge's body and starting files.

    Raises:
        HTTPException: ``404`` when the slug is unknown or inactive.
    """
    template = await session.scalar(
        select(QuestionTemplate).where(
            QuestionTemplate.slug == slug, QuestionTemplate.is_active.is_(True)
        )
    )
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found")

    node_maps = await _nodes_for(session, [template.id])
    detail = _to_challenge(template, node_maps.get(template.id, []))
    # `summary` doubles as the prompt body for now. Phase 3 replaces this with the
    # per-candidate generated question, whose text may have been mutated; keeping
    # the template's text as the fallback means a Phase 2 deployment works before
    # the mutation pipeline exists.
    return ChallengeEnvelope(
        challenge=ChallengeDetailResponse(
            **detail.model_dump(),
            prompt=template.summary,
            starter_files=dict(template.starter_files),
        )
    )


@router.post(
    "/submissions",
    response_model=SubmissionAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue a submission for grading",
)
async def create_submission(
    payload: CreateSubmissionRequest,
    session: DbSession,
    user: CurrentUser,
) -> SubmissionAccepted:
    """Accept candidate code and hand it to the queue.

    Returns ``202`` immediately. The response carries no result because the API
    cannot produce one -- it holds no Docker socket. See the module docstring for
    why the enqueue happens before the row is committed.

    Raises:
        HTTPException: ``404`` for an unknown challenge, ``413`` when the
            submission exceeds the sandbox's file-count or byte ceiling.
    """
    limits = CreateSubmissionRequest.size_limits()
    if len(payload.files) > limits["max_files"]:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Too many files: {len(payload.files)}, limit is {limits['max_files']}",
        )
    total_bytes = sum(len(content.encode("utf-8")) for content in payload.files.values())
    if total_bytes > limits["max_bytes"]:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Submission is {total_bytes} bytes, limit is {limits['max_bytes']}",
        )

    template = await session.scalar(
        select(QuestionTemplate).where(
            QuestionTemplate.slug == payload.challenge_id, QuestionTemplate.is_active.is_(True)
        )
    )
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found")

    submission_id = uuid.uuid4()
    mode = SubmissionMode(payload.mode)
    question_id = await _ensure_question(session, user_id=user.id, template=template)

    # Build and validate the payload before enqueueing. SubmissionPayload performs
    # the path-traversal and size checks, and doing them here means a hostile
    # submission is rejected at the edge rather than after a worker picks it up.
    worker_payload = _build_payload(
        submission_id=submission_id,
        template=template,
        files=payload.files,
        mode=mode,
    )

    await submission_store.store_payload(
        submission_id=str(submission_id),
        payload=worker_payload.as_dict(),
    )

    message = SubmissionMessage(
        submission_id=str(submission_id),
        question_id=None,
        language=template.language,
        payload_key=submission_store.payload_key_for(str(submission_id)),
        stream_key=submission_store.stream_key_for(str(submission_id)),
    )
    try:
        task_id = enqueue_submission(message)
    except RuntimeError as exc:
        # The payload is deleted so a hidden-test bundle does not linger in Redis
        # for a submission that will never run.
        await submission_store.delete_payload(submission_id=str(submission_id))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The grading queue is unavailable. Please retry.",
        ) from exc

    row = Submission(
        id=submission_id,
        user_id=user.id,
        question_id=question_id,
        challenge_id=payload.challenge_id,
        mode=mode.value,
        language=template.language,
        status="queued",
        files=dict(payload.files),
        started_at=datetime.now(UTC),
        celery_task_id=task_id,
    )
    session.add(row)
    await session.flush()

    log.info(
        "submission_accepted",
        submission_id=str(submission_id),
        user_id=str(user.id),
        language=template.language,
        mode=mode.value,
        bytes=total_bytes,
    )
    return SubmissionAccepted(
        submission_id=str(submission_id),
        stream_key=submission_store.stream_key_for(str(submission_id)),
        queued_at=row.created_at or datetime.now(UTC),
    )


@router.get(
    "/submissions/{submission_id}",
    response_model=SubmissionStatusResponse,
    summary="Poll a submission's result",
)
async def get_submission(
    submission_id: str,
    session: DbSession,
    user: CurrentUser,
) -> SubmissionStatusResponse:
    """Return a submission's durable result. The polling fallback for the stream.

    Ownership is enforced in the ``WHERE`` clause, not by comparing afterwards: a
    fetch-then-compare would return ``403`` for someone else's id and ``404`` for a
    nonexistent one, which is an enumeration oracle over submission ids.
    """
    parsed = _parse_uuid(submission_id)
    # `selectinload` is mandatory, not an optimisation. Rendering the result touches
    # `submission.test_results`, and under async SQLAlchemy a lazy load outside a
    # greenlet context raises MissingGreenlet -- which surfaces as a 500 on a
    # submission that graded perfectly well.
    row = await session.execute(
        select(Submission)
        .options(selectinload(Submission.test_results))
        .where(Submission.id == parsed, Submission.user_id == user.id)
        .limit(1)
    )
    submission = row.scalar_one_or_none()
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")

    from oa_api.services.results import build_result_document

    return SubmissionStatusResponse(
        submission_id=str(submission.id),
        status=submission.status,
        result=build_result_document(submission) if submission.status != "queued" else None,
        score_percentage=submission.score_percentage,
        finished_at=submission.finished_at,
    )


@router.get("/skills", response_model=SkillListResponse, summary="Skill mastery matrix")
async def list_skills(session: DbSession, user: CurrentUser) -> SkillListResponse:
    """Every Concept DNA node with this candidate's mastery of it.

    Returns all nodes, not just attempted ones. An unseen node is information: the
    dashboard needs to show what the syllabus covers that the candidate has not
    touched, which is exactly the set the adaptive engine will draw from next.
    """
    from oa_api.models.tables import SkillState

    nodes = list(
        (
            await session.execute(
                select(ConceptNode).order_by(ConceptNode.domain, ConceptNode.depth, ConceptNode.id)
            )
        ).scalars()
    )
    states = {
        state.node_id: state
        for state in (
            await session.execute(select(SkillState).where(SkillState.user_id == user.id))
        ).scalars()
    }

    return SkillListResponse(
        nodes=[
            SkillNodeResponse(
                id=node.id,
                label=node.label,
                domain=node.domain,
                depth=node.depth,
                parent_id=_parent_of(node.id, nodes),
                mastery=states[node.id].p_mastery if node.id in states else 0.0,
                attempts=states[node.id].attempts if node.id in states else 0,
                correct_attempts=states[node.id].correct_attempts if node.id in states else 0,
                last_seen_at=states[node.id].last_seen_at if node.id in states else None,
            )
            for node in nodes
        ]
    )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _parse_uuid(value: str) -> uuid.UUID:
    """Parse a submission id, or raise a 422 naming the problem."""
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="submission_id must be a UUID",
        ) from exc


def _parent_of(node_id: str, nodes: list[ConceptNode]) -> str | None:
    """Derive a node's parent from its dotted id.

    The dotted id encodes the hierarchy, so the parent is the id minus its last
    segment -- no edge query needed. Falls back to ``None`` when no such node
    exists, which is the honest answer for a root or an orphaned node rather than
    an id that resolves to nothing.
    """
    head, sep, _tail = node_id.rpartition(".")
    if not sep:
        return None
    candidate = head
    return candidate if any(n.id == candidate for n in nodes) else None


#: Bounds for the advertised time budget, in minutes.
_MIN_ESTIMATED_MINUTES = 5
_MAX_ESTIMATED_MINUTES = 45


def _estimated_minutes(reference_runtime_ms: int | None) -> int:
    """Estimate a candidate's time budget from the reference runtime.

    Args:
        reference_runtime_ms: Measured grader runtime for the reference solution,
            or ``None`` when the template has never been timed.

    Returns:
        Minutes, clamped to a sane band. An untimed template gets the middle of the
        range rather than an optimistic guess, because under-estimating a question
        reads to a candidate as the platform being wrong about its own content.
    """
    if reference_runtime_ms is None:
        return 15
    scaled = (reference_runtime_ms // 1000) // 3
    return max(
        _MIN_ESTIMATED_MINUTES, min(_MAX_ESTIMATED_MINUTES, scaled or _MIN_ESTIMATED_MINUTES)
    )


def _to_challenge(template: QuestionTemplate, nodes: list[str]) -> ChallengeResponse:
    """Project a template into the list-response shape."""
    return ChallengeResponse(
        id=template.slug,
        slug=template.slug,
        title=template.title,
        summary=template.summary,
        language=template.language,
        difficulty=template.difficulty,
        concept_nodes=nodes,
        # An estimate a candidate plans around, so it is bounded at both ends: the
        # grader's own runtime is not a human's (dividing by 3 approximates the
        # writing/thinking overhead), and an unmeasured template must not advertise
        # 40 minutes for a one-function question.
        estimated_minutes=_estimated_minutes(template.reference_runtime_ms),
        # The first path segment of the run command names the runner, not a topic,
        # so tags come from the concept nodes instead: those are the syllabus labels
        # the dashboard already groups by.
        tags=sorted({node.split(".")[0] for node in nodes}),
    )


async def _nodes_for(
    session: DbSession, template_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """Map template id to its concept node ids, in a stable order."""
    if not template_ids:
        return {}
    rows = await session.execute(
        select(QuestionTemplateNode.template_id, QuestionTemplateNode.node_id)
        .where(QuestionTemplateNode.template_id.in_(template_ids))
        .order_by(QuestionTemplateNode.node_id)
    )
    mapping: dict[uuid.UUID, list[str]] = {}
    for template_id, node_id in rows:
        mapping.setdefault(template_id, []).append(node_id)
    return mapping


async def _ensure_question(
    session: DbSession, *, user_id: uuid.UUID, template: QuestionTemplate
) -> uuid.UUID:
    """Return a ``generated_questions`` row for this serving of the template.

    Phase 2 serves the template verbatim, so one row per (user, template) is
    created and reused. Phase 3 replaces this with the mutation pipeline, which
    produces a distinct row per serving so the exact text a candidate was graded
    against is preserved. The indirection exists now so that change does not
    require touching the submission path.
    """
    existing = await session.scalar(
        select(GeneratedQuestion.id)
        .where(GeneratedQuestion.user_id == user_id, GeneratedQuestion.template_id == template.id)
        .order_by(GeneratedQuestion.served_at)
        .limit(1)
    )
    if existing is not None:
        return existing

    question = GeneratedQuestion(
        id=uuid.uuid4(),
        template_id=template.id,
        user_id=user_id,
        source="template",
        title=template.title,
        prompt=template.summary,
        starter_files=dict(template.starter_files),
        language=template.language,
        difficulty=template.difficulty,
        hidden_tests_hash=template.hidden_tests_hash,
    )
    session.add(question)
    await session.flush()
    return question.id


def _build_payload(
    *,
    submission_id: uuid.UUID,
    template: QuestionTemplate,
    files: dict[str, str],
    mode: SubmissionMode,
) -> SubmissionPayload:
    """Assemble the worker payload, with or without hidden tests.

    ``run`` mode sends an empty ``hidden_tests`` map. That is the whole point of
    Run: the candidate's own code executes with nothing to read, so they cannot
    discover the grader by listing a directory. ``submit`` sends the real bundle.
    """
    return SubmissionPayload(
        submission_id=str(submission_id),
        language=template.language,
        files=dict(files),
        hidden_tests={} if mode is SubmissionMode.RUN else dict(template.hidden_tests),
        run_command=tuple(template.run_command) or ("/runner/run-python.sh",),
        result_path="result.json",
    )
