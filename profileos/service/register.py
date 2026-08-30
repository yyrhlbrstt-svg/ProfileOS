"""The register: every call kept, so the pattern in them can be seen.

One call is an annoyance. Five calls about dropped sashes on the same series
is a hinge that is being fitted wrong, or specified wrong, and the only way to
tell the difference is to have written down what each one turned out to be.

The store is a single file written atomically, the same as the job book, so a
shop that loses power in the middle of logging a call loses the call and not
the register.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from ..core.logging_setup import get_logger
from .model import CallState, Cause, ServiceCall, Severity, Symptom, Visit

_log = get_logger("service.register")


def _to_json(call: ServiceCall) -> dict[str, Any]:
    data = asdict(call)
    data["symptom"] = call.symptom.value
    data["severity"] = call.severity.value if call.severity else None
    data["state"] = call.state.value
    data["cause"] = call.cause.value
    data["opened"] = call.opened.isoformat()
    data["delivered"] = call.delivered.isoformat() if call.delivered else None
    data["closed"] = call.closed.isoformat() if call.closed else None
    data["visits"] = [
        {**visit, "on": visit["on"].isoformat()} for visit in data["visits"]
    ]
    return data


def _from_json(data: dict[str, Any]) -> ServiceCall:
    return ServiceCall(
        call_id=data.get("call_id", ""),
        job_id=data.get("job_id", ""),
        customer_name=data.get("customer_name", ""),
        element_id=data.get("element_id", ""),
        element_name=data.get("element_name", ""),
        symptom=Symptom(data.get("symptom", "other")),
        description=data.get("description", ""),
        severity=Severity(data["severity"]) if data.get("severity") else None,
        opened=date.fromisoformat(data["opened"]),
        delivered=(
            date.fromisoformat(data["delivered"]) if data.get("delivered") else None
        ),
        state=CallState(data.get("state", "open")),
        cause=Cause(data.get("cause", "unknown")),
        visits=[
            Visit(
                on=date.fromisoformat(visit["on"]),
                engineer=visit.get("engineer", ""),
                minutes=int(visit.get("minutes", 0)),
                note=visit.get("note", ""),
                resolved=bool(visit.get("resolved", False)),
            )
            for visit in data.get("visits", [])
        ],
        parts=list(data.get("parts", [])),
        closed=date.fromisoformat(data["closed"]) if data.get("closed") else None,
        charged=float(data.get("charged", 0.0)),
        site=data.get("site", ""),
        phone=data.get("phone", ""),
    )


class ServiceRegister:
    """Every call this shop has taken."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._calls: dict[str, ServiceCall] = {}
        self.load()

    # -- persistence --------------------------------------------------------- #
    def load(self) -> "ServiceRegister":
        if not self.path.is_file():
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt file must not stop the phone
            _log.exception("Service register at %s could not be read", self.path)
            return self
        for entry in raw.get("calls", []):
            try:
                call = _from_json(entry)
            except Exception:  # noqa: BLE001 - one bad row, not the register
                _log.warning("Skipping unreadable service call: %s", entry.get("call_id"))
                continue
            self._calls[call.call_id] = call
        return self

    def save(self) -> None:
        """Write atomically: a half-written register is worse than none."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"calls": [_to_json(call) for call in self._calls.values()]}
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

    # -- writing ------------------------------------------------------------- #
    def add(self, call: ServiceCall) -> ServiceCall:
        self._calls[call.call_id] = call
        self.save()
        _log.info("Service call %s: %s", call.call_id, call.describe())
        return call

    def update(self, call: ServiceCall) -> ServiceCall:
        return self.add(call)

    def get(self, call_id: str) -> ServiceCall | None:
        return self._calls.get(call_id)

    # -- reading ------------------------------------------------------------- #
    def __len__(self) -> int:
        return len(self._calls)

    def __iter__(self):
        return iter(sorted(self._calls.values(), key=lambda c: c.opened, reverse=True))

    def all(self) -> list[ServiceCall]:
        return list(self)

    def open_calls(self) -> list[ServiceCall]:
        return [call for call in self if call.state.is_open]

    def overdue(self, today: date | None = None, calendar: Any = None) -> list[ServiceCall]:
        """The calls somebody should already have been to."""
        return [call for call in self.open_calls() if call.is_overdue(today, calendar)]

    def for_job(self, job_id: str) -> list[ServiceCall]:
        return [call for call in self if call.job_id == job_id]

    def for_customer(self, name: str) -> list[ServiceCall]:
        needle = name.strip().casefold()
        return [call for call in self if needle in call.customer_name.casefold()]

    # -- what the register is for -------------------------------------------- #
    def by_cause(self) -> dict[str, int]:
        """How many calls each cause accounted for, once diagnosed."""
        return dict(Counter(call.cause.hebrew for call in self if call.cause is not Cause.UNKNOWN))

    def by_symptom(self) -> dict[str, int]:
        return dict(Counter(call.symptom.hebrew for call in self))

    def recurring(self, minimum: int = 3) -> list[tuple[str, int]]:
        """Faults seen often enough to be a message to the workshop."""
        counts = Counter(
            (call.symptom.hebrew, call.cause.hebrew)
            for call in self
            if call.cause.is_ours
        )
        return [
            (f"{symptom} — {cause}", count)
            for (symptom, cause), count in counts.most_common()
            if count >= minimum
        ]

    def cost_of_quality(self, since: date | None = None) -> dict[str, float]:
        """What going back has cost, split by whose fault it was.

        The number a shop never has: hours spent returning to finished work.
        It is the difference between a margin on paper and a margin in the
        bank, and it is only knowable because every call records a cause.
        """
        calls = [call for call in self if since is None or call.opened >= since]
        ours = sum(call.minutes_spent for call in calls if call.cause.is_ours)
        theirs = sum(call.minutes_spent for call in calls if call.cause.is_chargeable)
        other = sum(call.minutes_spent for call in calls) - ours - theirs
        return {
            "hours_our_fault": round(ours / 60.0, 2),
            "hours_chargeable": round(theirs / 60.0, 2),
            "hours_other": round(other / 60.0, 2),
            "visits": sum(len(call.visits) for call in calls),
            "recovered": round(sum(call.charged for call in calls), 2),
        }

    def response_performance(self) -> dict[str, float]:
        """How fast calls are actually answered, against what was promised."""
        closed = [call for call in self if call.closed is not None]
        if not closed:
            return {"closed": 0, "median_days": 0.0, "within_target": 0.0}
        days = sorted((call.closed - call.opened).days for call in closed)
        on_time = sum(
            1 for call in closed
            if (call.closed - call.opened).days <= call.severity.response_days
        )
        middle = days[len(days) // 2]
        return {
            "closed": len(closed),
            "median_days": float(middle),
            "within_target": round(on_time / len(closed) * 100.0, 1),
        }

    def due_this_week(self, today: date | None = None) -> list[ServiceCall]:
        today = today or date.today()
        horizon = today + timedelta(days=7)
        return [
            call for call in self.open_calls()
            if call.due_by() <= horizon
        ]


def default_register_path() -> Path:
    from ..core.config import get_settings

    return get_settings().data_dir / "service_calls.json"


def default_register() -> ServiceRegister:
    return ServiceRegister(default_register_path())


__all__ = [
    "ServiceRegister",
    "default_register",
    "default_register_path",
]
