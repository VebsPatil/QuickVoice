"""
langfuse_handler.py
-------------------
Langfuse observability integration for QuickVoice voice sessions.

Each voice call is modelled as one Langfuse Trace. Individual conversation
turns become child Spans, and QuickVoice call evaluations are submitted as
Langfuse Scores when the call finalises.

The integration is fully opt-in: if LANGFUSE_PUBLIC_KEY or
LANGFUSE_SECRET_KEY are absent (empty or unset), every public function in
this module is a no-op and no import of the langfuse package is attempted
at module load time.

Usage example (inside entrypoint):

    from handlers.langfuse_handler import (
        create_call_trace,
        record_turn,
        submit_scores,
        flush_langfuse,
    )

    trace = create_call_trace(
        call_id=call_context["call_id"],
        agent_id=config.get("agent_id", ""),
        organization_id=config.get("organization_id", ""),
        system_prompt=system_prompt,
        call_context=call_context,
        started_at=call_start_time,
    )

    # ... later, after each turn:
    record_turn(trace, role="user", content="Hello", started_at=t0, ended_at=t1)

    # ... on finalisation:
    submit_scores(trace, config.get("data_evaluated", []))
    flush_langfuse()
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from utils.logger import logger

# ---------------------------------------------------------------------------
# Singleton client
# ---------------------------------------------------------------------------

_langfuse_client: Any = None


def get_langfuse_client() -> Any | None:
    """Return (or lazily create) the singleton Langfuse client.

    Returns None – without raising – if credentials are missing or if the
    langfuse package is not installed.  All callers must treat None as
    "disabled" and skip observability work gracefully.
    """
    global _langfuse_client

    if _langfuse_client is not None:
        return _langfuse_client

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()

    if not public_key or not secret_key:
        # Credentials absent – Langfuse is disabled for this deployment.
        return None

    try:
        from langfuse import Langfuse  # type: ignore[import]

        host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com").strip()
        _langfuse_client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
        logger.info("[langfuse] client initialised (host={})", host)
    except Exception as exc:
        logger.warning("[langfuse] failed to initialise client: {}", exc)
        _langfuse_client = None

    return _langfuse_client


# ---------------------------------------------------------------------------
# Trace lifecycle
# ---------------------------------------------------------------------------


def create_call_trace(
    *,
    call_id: str,
    agent_id: str,
    organization_id: str,
    system_prompt: str,
    call_context: dict[str, Any],
    started_at: datetime,
) -> Any | None:
    """Open a Langfuse Trace for a single voice call.

    Args:
        call_id:         Unique call identifier (used as the trace ID so
                         traces are idempotent across retries).
        agent_id:        QuickVoice agent identifier.
        organization_id: Organisation that owns this agent.
        system_prompt:   Full system prompt sent to the LLM at session start.
        call_context:    Call-level metadata dict (direction, numbers, etc.).
        started_at:      UTC datetime when the call session started.

    Returns:
        A Langfuse Trace object, or None if Langfuse is disabled.
    """
    client = get_langfuse_client()
    if client is None:
        return None

    # Sanitise context – remove phone-number keys for orgs that prefer not to
    # send PII to a third-party service.
    safe_context: dict[str, Any] = {
        k: v
        for k, v in call_context.items()
        if k not in {"from_number", "to_number", "fromNumber", "toNumber"}
    }

    try:
        trace = client.trace(
            id=call_id,
            name="voice-call",
            input={"system_prompt": system_prompt},
            user_id=organization_id,
            tags=[
                f"agent:{agent_id}",
                f"org:{organization_id}",
                f"direction:{call_context.get('direction', 'unknown')}",
            ],
            metadata=safe_context,
            timestamp=started_at,
        )
        logger.info("[langfuse] trace created for call_id={}", call_id)
        return trace
    except Exception as exc:
        logger.warning("[langfuse] failed to create trace for call_id={}: {}", call_id, exc)
        return None


# ---------------------------------------------------------------------------
# Turn recording
# ---------------------------------------------------------------------------


def record_turn(
    trace: Any | None,
    *,
    role: str,
    content: str,
    started_at: datetime,
    ended_at: datetime,
) -> None:
    """Append a conversation turn as a child Span on the given trace.

    Args:
        trace:      The trace returned by :func:`create_call_trace`.
                    If None, the call is silently skipped.
        role:       Either ``"user"`` or ``"agent"``.
        content:    The transcript text for this turn.
        started_at: UTC datetime when this turn started.
        ended_at:   UTC datetime when this turn ended.
    """
    if trace is None:
        return
    try:
        trace.span(
            name=f"{role}-turn",
            input={"role": role, "content": content},
            start_time=started_at,
            end_time=ended_at,
        )
    except Exception as exc:
        logger.warning("[langfuse] failed to record {} turn: {}", role, exc)


# ---------------------------------------------------------------------------
# Score submission
# ---------------------------------------------------------------------------


def submit_scores(
    trace: Any | None,
    evaluated_data: list[dict[str, Any]],
    extracted_data: list[dict[str, Any]] | None = None,
) -> None:
    """Submit QuickVoice call evaluations as Langfuse Scores.

    Evaluated data items contain ``identifier``, ``description``, and
    ``value`` fields. Values are normalised as follows:

    * ``True``/``False`` -> ``1.0``/``0.0``
    * Numeric-parseable strings -> float
    * All other strings -> stored as a comment with value ``0.0``

    If ``extracted_data`` is provided it is attached to the trace output so
    the structured call data is visible alongside the trace in the dashboard.

    Args:
        trace:          The trace returned by :func:`create_call_trace`.
        evaluated_data: List of evaluation result dicts from ``data_evaluated``.
        extracted_data: Optional list of extracted-data dicts from
                        ``data_extracted``.
    """
    if trace is None:
        return

    # Attach extracted data to the trace output for dashboard visibility.
    if extracted_data:
        try:
            trace.update(output={"extracted_data": extracted_data})
        except Exception as exc:
            logger.warning("[langfuse] failed to update trace output: {}", exc)

    for item in (evaluated_data or []):
        identifier = str(item.get("identifier") or item.get("name") or "").strip()
        if not identifier:
            continue

        raw_value = item.get("value")
        comment: str | None = None

        if isinstance(raw_value, bool):
            numeric = 1.0 if raw_value else 0.0
        elif isinstance(raw_value, (int, float)):
            numeric = float(raw_value)
        elif isinstance(raw_value, str):
            lower = raw_value.strip().lower()
            if lower in {"true", "yes"}:
                numeric = 1.0
            elif lower in {"false", "no"}:
                numeric = 0.0
            else:
                try:
                    numeric = float(raw_value)
                except (ValueError, TypeError):
                    numeric = 0.0
                    comment = raw_value
        else:
            numeric = 0.0

        try:
            score_kwargs: dict[str, Any] = {
                "name": identifier,
                "value": numeric,
            }
            if comment:
                score_kwargs["comment"] = comment
            elif item.get("description"):
                score_kwargs["comment"] = str(item["description"])

            trace.score(**score_kwargs)
            logger.debug("[langfuse] score submitted: {}={}", identifier, numeric)
        except Exception as exc:
            logger.warning("[langfuse] failed to submit score {}: {}", identifier, exc)


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


def flush_langfuse() -> None:
    """Flush the Langfuse SDK background queue.

    Should be called once during agent shutdown so that any buffered events
    are delivered before the process exits.  Safe to call when Langfuse is
    disabled.
    """
    client = _langfuse_client
    if client is None:
        return
    try:
        client.flush()
        logger.info("[langfuse] flushed pending events")
    except Exception as exc:
        logger.warning("[langfuse] flush failed: {}", exc)
