"""Traceability and citation tracker for RAG-grounded email drafts."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("harvey.rag.trace")

TRACES_FILE = Path("data/rag/traces.json")


def _load_traces() -> dict[str, Any]:
    """Load trace registry from disk."""
    if not TRACES_FILE.exists():
        return {}
    try:
        return json.loads(TRACES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Failed to read RAG traces from {TRACES_FILE}: {e}")
        return {}


def _save_traces(traces: dict[str, Any]):
    """Persist trace registry to disk."""
    TRACES_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = TRACES_FILE.with_suffix(".tmp")
    temp_file.write_text(json.dumps(traces, indent=2), encoding="utf-8")
    temp_file.replace(TRACES_FILE)


def record_rag_trace(
    *,
    item_id: str = "",
    prospect_id: str = "",
    to_email: str = "",
    subject: str = "",
    sources: list[str] | None = None,
    chunks: list[dict[str, Any]] | None = None,
    query: str = "",
) -> dict[str, Any]:
    """Record an audit trace linking a generated draft to its retrieved RAG chunks."""
    traces = _load_traces()
    key = item_id or prospect_id or f"trace_{len(traces)+1}"

    clean_chunks = []
    for c in chunks or []:
        clean_chunks.append(
            {
                "chunk_id": c.get("chunk_id", ""),
                "source": c.get("source", ""),
                "page": c.get("page", 1),
                "score": c.get("score", 0.0),
                "snippet": (c.get("text") or "")[:200],
            }
        )

    record = {
        "item_id": item_id,
        "prospect_id": prospect_id,
        "to_email": to_email,
        "subject": subject,
        "sources": sources or [],
        "chunks": clean_chunks,
        "query": query,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    traces[key] = record
    if prospect_id and item_id and key != prospect_id:
        traces[prospect_id] = record
    _save_traces(traces)
    return record


def get_rag_trace(identifier: str) -> Optional[dict[str, Any]]:
    """Retrieve trace for an item_id or prospect_id."""
    if not identifier:
        return None
    traces = _load_traces()
    return traces.get(identifier)


def list_rag_traces() -> list[dict[str, Any]]:
    """Return all recorded traces ordered by timestamp."""
    traces = _load_traces()
    # Deduplicate by item_id / timestamp
    seen = set()
    out = []
    for t in traces.values():
        uid = t.get("item_id") or t.get("prospect_id")
        if uid and uid not in seen:
            seen.add(uid)
            out.append(t)
    return out

