"""The submission message contract, shared by producer and consumer.

The API publishes submissions; the worker consumes them. Neither should import the
other, and this module is why that is possible:

* The API must not depend on Celery. It has no Docker client and no path to a
  container runtime, and importing the worker's task module would drag Celery into
  an image whose entire security value is that it *cannot* execute code.
* The worker must not depend on the API. A worker process importing FastAPI to
  learn a message shape is a circular dependency waiting to bite at import time.

So the message shape lives here, in the one package both already depend on, and
both sides reference it. If the two ever disagree about the payload, the mismatch
shows up as a Pydantic validation error naming the field -- not as an AttributeError
somewhere in a worker log at 3am.

``kombu`` is used rather than ``celery`` because kombu is Celery's transport
library, not Celery. It gives a stable publish protocol with no worker machinery
and no task registry.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "SUBMISSION_TASK_NAME",
    "SubmissionMessage",
    "pool_task_name",
]

#: Fully-qualified Celery task name the worker registers. Declared here so the
#: producer never imports the consumer to discover it.
SUBMISSION_TASK_NAME: Final[str] = "oa_worker.tasks.execute.execute_submission"


def pool_task_name(kind: Literal["replenish", "drain"]) -> str:
    """Return the worker's pool-management task name.

    Args:
        kind: Which pool operation.

    Returns:
        The dotted task name.

    Raises:
        ValueError: On an unknown ``kind``, so a typo fails at the call site
            instead of enqueueing a message no worker will ever claim -- which is a
            message that silently disappears from the queue's point of view.
    """
    match kind:
        case "replenish":
            return "oa_worker.tasks.execute.replenish_warm_pool"
        case "drain":
            return "oa_worker.tasks.execute.drain_warm_pool"
    raise ValueError(f"unknown pool task kind: {kind!r}")


class SubmissionMessage(BaseModel):
    """What the API hands the worker.

    Carries no file contents and no hidden tests. Those live in Redis, addressed by
    ``payload_key``, and are read by the worker at grading time. The split is
    deliberate: the broker is a transport that a compromised API instance could
    read, and the payload is the one thing in the system that must never be on it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    submission_id: str = Field(min_length=1, max_length=128)
    question_id: str | None = Field(default=None, max_length=128)
    language: str = Field(min_length=1, max_length=32)
    #: Redis key holding candidate files and hidden tests.
    payload_key: str = Field(min_length=1, max_length=256)
    #: Redis stream key for live progress events.
    stream_key: str = Field(min_length=1, max_length=256)
    #: Pin the grading run to an exact image digest when known.
    image_digest: str | None = Field(default=None, max_length=256)

    def as_task_kwargs(self) -> dict[str, Any]:
        """Render as keyword arguments for ``Celery.send_task``.

        Keyword arguments rather than positional ones so a worker's log line and
        error message name the offending field directly, instead of an arity error
        that says nothing about which value was wrong.
        """
        return self.model_dump(mode="json")
