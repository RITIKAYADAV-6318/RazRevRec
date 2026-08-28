"""Append-only, hash-chained audit log for recovery decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any


GENESIS_HASH = "0" * 64


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class AuditEvent:
    sequence: int
    timestamp: str
    event_type: str
    payload: dict[str, Any]
    previous_hash: str
    event_hash: str


class AuditTrail:
    """In-memory event log that can be persisted through ``to_json`` later."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)

    def append(self, event_type: str, payload: dict[str, Any], timestamp: str | None = None) -> AuditEvent:
        sequence = len(self._events) + 1
        previous_hash = self._events[-1].event_hash if self._events else GENESIS_HASH
        timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        signing_payload = {
            "sequence": sequence,
            "timestamp": timestamp,
            "event_type": event_type,
            "payload": payload,
            "previous_hash": previous_hash,
        }
        event_hash = hashlib.sha256(_canonical_json(signing_payload).encode("utf-8")).hexdigest()
        event = AuditEvent(sequence, timestamp, event_type, payload, previous_hash, event_hash)
        self._events.append(event)
        return event

    def verify(self) -> tuple[bool, str]:
        previous_hash = GENESIS_HASH
        for expected_sequence, event in enumerate(self._events, start=1):
            if event.sequence != expected_sequence:
                return False, f"Invalid sequence at event {expected_sequence}."
            if event.previous_hash != previous_hash:
                return False, f"Broken previous-hash link at event {event.sequence}."
            signing_payload = {
                "sequence": event.sequence,
                "timestamp": event.timestamp,
                "event_type": event.event_type,
                "payload": event.payload,
                "previous_hash": event.previous_hash,
            }
            computed = hashlib.sha256(_canonical_json(signing_payload).encode("utf-8")).hexdigest()
            if computed != event.event_hash:
                return False, f"Tampering detected at event {event.sequence}."
            previous_hash = event.event_hash
        return True, "Audit trail integrity verified."

    def to_json(self) -> str:
        return json.dumps([asdict(event) for event in self._events], indent=2, sort_keys=True)

