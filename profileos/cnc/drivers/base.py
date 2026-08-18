"""The driver contract every machine post-processor implements.

A driver is a pure translator: it takes a :class:`~profileos.cnc.job.MachiningJob`
expressed in the neutral IR and returns the bytes a specific control expects.
Drivers never mutate the job, so the same job can be posted to several machines
and diffed.

Registration is declarative::

    @register_driver
    class MyPost(BasePostProcessor):
        key = "vendor.format"
        extension = ".xyz"

which adds it to the hot-reloadable ``POST_PROCESSORS`` registry, so a plugin
dropped into the machines directory can add or override a driver at runtime.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Sequence

from ...core.config import CncDefaults, get_settings
from ...core.errors import PostProcessorError
from ...core.events import Topic, publish
from ...core.logging_setup import get_logger
from ...core.registry import POST_PROCESSORS
from ...models.machines import Tool, ToolLibrary
from ...models.profile import Face
from ..job import MachiningJob, PieceProgram
from ..operations import Operation, OperationType

_log = get_logger("cnc.drivers")


@dataclass
class PostResult:
    """One posted program."""

    filename: str
    content: str
    #: Set when the format is binary; ``content`` is then a textual preview.
    raw: bytes | None = None
    encoding: str = "utf-8"
    driver_key: str = ""
    machine: str = ""
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.raw) if self.raw is not None else len(self.content.encode(self.encoding))

    @property
    def line_count(self) -> int:
        return self.content.count("\n") + 1 if self.content else 0

    def write(self, directory: str | Path) -> Path:
        """Write the program into ``directory`` and return the file path."""
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        path = target / self.filename
        if self.raw is not None:
            path.write_bytes(self.raw)
        else:
            path.write_text(self.content, encoding=self.encoding)
        _log.info("Wrote %s (%d bytes)", path, self.size)
        return path


class BasePostProcessor(ABC):
    """Base class for machine drivers."""

    #: Registry key, e.g. ``"elumatec.ncx"``.
    key: ClassVar[str] = ""
    #: Human readable name shown in the UI.
    display_name: ClassVar[str] = ""
    vendor: ClassVar[str] = ""
    extension: ClassVar[str] = ".nc"
    #: Format version emitted in file headers.
    format_version: ClassVar[str] = "1.0"
    #: Operation types this control can execute natively.
    supported_operations: ClassVar[frozenset[OperationType]] = frozenset(OperationType)
    #: Faces the format can address.
    supported_faces: ClassVar[frozenset[Face]] = frozenset(Face)
    #: True when the control applies cutter radius compensation itself.
    supports_cutter_compensation: ClassVar[bool] = False
    #: True when one file holds the whole job rather than one file per piece.
    single_file_per_job: ClassVar[bool] = True

    def __init__(self, defaults: CncDefaults | None = None) -> None:
        self.defaults = defaults or get_settings().cnc
        self.warnings: list[str] = []

    # -- the one method a driver must implement ---------------------------- #
    @abstractmethod
    def _render(self, job: MachiningJob) -> str | list[PostResult]:
        """Produce the native program text, or several results for multi-file formats."""

    # -- public entry point ------------------------------------------------- #
    def post(self, job: MachiningJob, *, validate: bool = True) -> list[PostResult]:
        """Translate ``job`` into one or more native programs.

        Raises
        ------
        PostProcessorError
            The job is invalid for this driver (unsupported feature, unreachable
            face, or a failed job validation).
        """
        self.warnings = []
        publish(Topic.CNC_POST_STARTED, source=self.key, job=job.job_id, pieces=len(job.pieces))

        if validate:
            problems = job.validate()
            if problems:
                raise PostProcessorError(
                    "Job failed validation",
                    driver=self.key,
                    job=job.job_id,
                    problems=problems[:10],
                )
        self._check_capabilities(job)

        rendered = self._render(job)
        if isinstance(rendered, list):
            results = rendered
        else:
            results = [
                PostResult(
                    filename=self.default_filename(job),
                    content=rendered,
                    encoding=self.defaults.output_encoding,
                )
            ]

        for result in results:
            result.driver_key = self.key
            result.machine = job.machine.name
            result.warnings.extend(self.warnings)
            result.stats.setdefault("operations", len(job.all_operations()))
            result.stats.setdefault("pieces", len(job.pieces))

        publish(
            Topic.CNC_POST_COMPLETED,
            source=self.key,
            job=job.job_id,
            files=len(results),
            warnings=len(self.warnings),
        )
        _log.info(
            "Posted job %s with %s: %d file(s), %d warning(s)",
            job.job_id,
            self.key,
            len(results),
            len(self.warnings),
        )
        return results

    # -- capability checking ------------------------------------------------ #
    def _check_capabilities(self, job: MachiningJob) -> None:
        """Reject a job this control physically cannot run.

        Failing here, loudly, is the point: a driver that silently drops an
        operation it does not understand produces a part that is quietly wrong,
        which is far worse than a job that refuses to post.
        """
        unsupported: dict[str, int] = {}
        for op in job.all_operations():
            if op.op_type not in self.supported_operations:
                unsupported[op.op_type.value] = unsupported.get(op.op_type.value, 0) + 1
            if op.face not in self.supported_faces:
                key = f"face:{op.face.value}"
                unsupported[key] = unsupported.get(key, 0) + 1

        if unsupported:
            raise PostProcessorError(
                f"{self.display_name or self.key} cannot execute some operations in this job",
                driver=self.key,
                unsupported=unsupported,
            )

    # -- helpers for subclasses --------------------------------------------- #
    def default_filename(self, job: MachiningJob, suffix: str = "") -> str:
        stem = _sanitise(job.name or job.job_id)
        return f"{stem}{suffix}{self.extension}"

    def piece_filename(self, job: MachiningJob, piece: PieceProgram) -> str:
        return f"{_sanitise(piece.label)}{self.extension}"

    def tool_for(self, job: MachiningJob, op: Operation) -> Tool | None:
        """Look up an operation's tool in the job's library."""
        library: ToolLibrary | None = job.tool_library
        if library is None or op.tool_number is None:
            return None
        return library.by_number(op.tool_number)

    def feed_for(self, job: MachiningJob, op: Operation) -> float:
        if op.feed is not None:
            return op.feed
        tool = self.tool_for(job, op)
        return tool.feed_mm_min if tool else self.defaults.default_feed_mm_min

    def speed_for(self, job: MachiningJob, op: Operation) -> int:
        if op.spindle_speed is not None:
            return op.spindle_speed
        tool = self.tool_for(job, op)
        return tool.spindle_rpm if tool else self.defaults.default_spindle_rpm

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        _log.warning("%s: %s", self.key, message)

    def timestamp(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def header_comment_lines(self, job: MachiningJob) -> list[str]:
        """Provenance lines most controls accept as comments."""
        return [
            f"ProfileOS {self.display_name or self.key} post-processor v{self.format_version}",
            f"Job {job.job_id} - {job.name}",
            f"Machine {job.machine.vendor} {job.machine.model}",
            f"Generated {self.timestamp()}",
        ]


def _sanitise(name: str, *, max_length: int = 60) -> str:
    """Reduce a label to something safe as a filename on any control."""
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name.strip())
    safe = safe.strip("_") or "program"
    return safe[:max_length]


def register_driver(cls: type[BasePostProcessor]) -> type[BasePostProcessor]:
    """Class decorator adding a driver to the ``POST_PROCESSORS`` registry."""
    if not cls.key:
        raise PostProcessorError(f"Driver {cls.__name__} has no registry key")
    POST_PROCESSORS.add(
        cls.key,
        cls,
        version=cls.format_version,
        source="builtin",
        vendor=cls.vendor,
        extension=cls.extension,
        display_name=cls.display_name,
    )
    return cls


def get_driver(key: str, defaults: CncDefaults | None = None) -> BasePostProcessor:
    """Instantiate a registered driver by key.

    Raises
    ------
    PostProcessorError
        No driver is registered under ``key``.
    """
    from ...core.errors import PluginError

    try:
        cls = POST_PROCESSORS.get(key)
    except PluginError as exc:
        raise PostProcessorError(
            f"Unknown post-processor {key!r}",
            available=POST_PROCESSORS.keys(),
        ) from exc
    return cls(defaults)


def available_drivers() -> list[dict[str, Any]]:
    """Describe every registered driver, for the UI machine picker."""
    return POST_PROCESSORS.describe()


__all__ = [
    "PostResult",
    "BasePostProcessor",
    "register_driver",
    "get_driver",
    "available_drivers",
]
