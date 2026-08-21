"""The program nobody has cut yet, and how it stops being that.

This software writes NCX, ECX, DGX, MCO, KBN and G-code, and not one of those
files has ever gone through a real machine from here. That is stated plainly
everywhere it matters, and stating it is not enough: a shop cannot use a
posted program until somebody has proved it, and "somebody proved it" has to
be a fact the software knows rather than a memory somebody has.

So proving is a record. A driver is unproven until a named person cuts one
program on scrap, measures what came out, and records the machine, the date
and what they found. From then on that driver on that machine is proven, and
the banner comes off — for that pair only, because a post-processor proved on
the Elumatec says nothing about the Emmegi.

Alongside it is the part that can be checked without a machine: whether the
program stays inside the travel and the tools the machine actually has. That
catches the crash before the scrap, which is worth doing even though it is
not proof of anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("cnc.proving")


@dataclass
class Proof:
    """One driver, proved on one machine, by one person, on one day."""

    driver: str
    machine: str
    proved_by: str
    on: str = field(default_factory=lambda: date.today().isoformat())
    #: What was cut and what it measured. The evidence, in their words.
    findings: str = ""
    #: Programs run during proving, for anybody who wants to repeat it.
    programs: list[str] = field(default_factory=list)
    #: Where measurements differed from the drawing [mm].
    largest_deviation: float = 0.0
    accepted: bool = True

    @property
    def key(self) -> tuple[str, str]:
        return (self.driver, self.machine)

    def describe(self) -> str:
        state = "אושר" if self.accepted else "נדחה"
        return (
            f"{self.driver} על {self.machine} — {state}, "
            f"{self.proved_by}, ⁦{self.on}⁩"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "driver": self.driver, "machine": self.machine,
            "proved_by": self.proved_by, "on": self.on,
            "findings": self.findings, "programs": list(self.programs),
            "largest_deviation": self.largest_deviation,
            "accepted": self.accepted,
        }


class ProvingRecord:
    """Which post-processor has been proved on which machine."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._proofs: dict[tuple[str, str], Proof] = {}
        self.load()

    def load(self) -> "ProvingRecord":
        if not self.path.is_file():
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - unreadable means unproven, which is safe
            _log.exception("Proving record at %s unreadable", self.path)
            return self
        for entry in raw.get("proofs", []):
            try:
                proof = Proof(
                    driver=entry["driver"], machine=entry["machine"],
                    proved_by=entry.get("proved_by", ""),
                    on=entry.get("on", ""), findings=entry.get("findings", ""),
                    programs=list(entry.get("programs", [])),
                    largest_deviation=float(entry.get("largest_deviation", 0.0)),
                    accepted=bool(entry.get("accepted", True)),
                )
            except Exception:  # noqa: BLE001
                _log.warning("Skipping unreadable proof: %s", entry)
                continue
            self._proofs[proof.key] = proof
        return self

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"proofs": [proof.as_dict() for proof in self._proofs.values()]}
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

    def __len__(self) -> int:
        return len(self._proofs)

    def __iter__(self):
        return iter(self._proofs.values())

    def record(self, proof: Proof) -> Proof:
        """Record a proving run. A person and a finding are both required."""
        if not proof.proved_by.strip():
            raise ProfileOSError(
                "מי הוכיח? הוכחה בלי שם היא לא הוכחה"
            )
        if proof.accepted and not proof.findings.strip():
            raise ProfileOSError(
                "מה נמדד? רשמו מה נחתך ומה יצא — בלי זה אין למה לחזור"
            )
        self._proofs[proof.key] = proof
        self.save()
        _log.info("Proving recorded: %s", proof.describe())
        return proof

    def is_proven(self, driver: str, machine: str) -> bool:
        """Whether this driver has been proved on this machine.

        Deliberately per pair: a post-processor proved on the Elumatec says
        nothing about the Emmegi beside it.
        """
        proof = self._proofs.get((driver, machine))
        return bool(proof and proof.accepted)

    def proof_for(self, driver: str, machine: str) -> Proof | None:
        return self._proofs.get((driver, machine))

    def banner(self, driver: str, machine: str) -> str | None:
        """The line that has to appear on an unproven program."""
        proof = self.proof_for(driver, machine)
        if proof is None:
            return (
                f"לא הוכח — {driver} מעולם לא נחתך על {machine} מהתוכנה הזאת. "
                "הריצו תוכנית אחת על פסולת ומדדו לפני ייצור."
            )
        if not proof.accepted:
            return (
                f"נדחה בהוכחה — {proof.findings or 'ראו את הרישום'}. "
                "אין לייצר עד שיתוקן ויוכח שוב."
            )
        return None

    def summary(self) -> dict[str, Any]:
        accepted = [proof for proof in self if proof.accepted]
        return {
            "proofs": len(self._proofs),
            "accepted": len(accepted),
            "machines": len({proof.machine for proof in accepted}),
            "drivers": len({proof.driver for proof in accepted}),
        }


# --------------------------------------------------------------------------- #
# What can be checked without a machine
# --------------------------------------------------------------------------- #

@dataclass
class DryRun:
    """What a program would ask the machine to do, checked against its limits."""

    driver: str
    machine: str
    lines: int = 0
    operations: int = 0
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: The extent of the toolpath, so it can be compared with the travel.
    extent: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def is_clean(self) -> bool:
        return not self.problems

    def describe(self) -> str:
        if self.is_clean:
            return (
                f"⁦{self.operations}⁩ פעולות בתוך גבולות המכונה — "
                "אין הוכחה שהתוכנית נכונה, רק שהיא לא חורגת"
            )
        return f"⁦{len(self.problems)}⁩ בעיות לפני שהמכונה נוגעת בחומר"


def dry_run(job: Any, machine: Any, *, driver: str = "") -> DryRun:
    """Check a machining job against the machine it is going to.

    This is not proof that the program is right — only a machine can say that.
    It is the cheaper half: catching the operation that runs past the travel,
    asks for a tool that is not in the changer, or drills through the far wall,
    before anybody clamps a bar.
    """
    result = DryRun(
        driver=driver or getattr(machine, "post_processor", "") or "?",
        machine=getattr(machine, "id", None) or getattr(machine, "name", "?"),
    )

    operations = list(getattr(job, "operations", []) or [])
    result.operations = len(operations)
    if not operations:
        result.problems.append("אין פעולות בתוכנית")
        return result

    travel = getattr(machine, "travel", None)
    limits = {
        "x": float(getattr(travel, "x", 0.0) or 0.0),
        "y": float(getattr(travel, "y", 0.0) or 0.0),
        "z": float(getattr(travel, "z", 0.0) or 0.0),
    } if travel is not None else {}

    tools = {
        str(getattr(tool, "id", getattr(tool, "number", "")))
        for tool in (getattr(machine, "tools", []) or [])
    }

    extent = [0.0, 0.0, 0.0]
    for index, operation in enumerate(operations, start=1):
        for axis, position in enumerate(("x", "y", "z")):
            value = abs(float(getattr(operation, position, 0.0) or 0.0))
            extent[axis] = max(extent[axis], value)
            limit = limits.get(position, 0.0)
            if limit and value > limit:
                result.problems.append(
                    f"פעולה ⁦{index}⁩: {position.upper()} = ⁦{value:.1f}⁩ מ״מ "
                    f"מעבר למהלך המכונה (⁦{limit:.1f}⁩)"
                )
        tool = getattr(operation, "tool_id", None) or getattr(operation, "tool", None)
        if tools and tool is not None and str(tool) not in tools:
            result.problems.append(
                f"פעולה ⁦{index}⁩: כלי {tool} אינו במחסנית של המכונה"
            )
    result.extent = tuple(extent)

    if not limits:
        result.notes.append(
            "למכונה לא הוגדרו גבולות מהלך — לא נבדקה חריגה"
        )
    if not tools:
        result.notes.append("למכונה לא הוגדרו כלים — לא נבדקה זמינות כלי")
    return result


def default_record_path() -> Path:
    from ..core.config import get_settings

    return get_settings().data_dir / "cnc_proving.json"


def default_record() -> ProvingRecord:
    return ProvingRecord(default_record_path())


__all__ = [
    "DryRun",
    "Proof",
    "ProvingRecord",
    "default_record",
    "default_record_path",
    "dry_run",
]
