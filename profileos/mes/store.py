"""A job's work order on disk, so the floor's scans outlive the window.

A work order held only in the program's memory forgets every cut, every
glazed sash and every scrapped piece the moment the window closes. Here it
lives beside the job, in the job's own folder, and each scan is written as
it happens.

Two screens may scan the same order: the office PC and the tablet by the
saw. Each item's history is an append-only log, so a save never chooses one
screen over the other — it takes the union of both logs, ordered by time,
and the item stands at the stage of its latest event.

A second release replaces the order only when asked to, and the order it
replaces is kept in the same folder under its own number.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.errors import ProfileOSError
from ..core.storage import path_lock, read_json, write_json
from .tracking import ItemKind, ProductionItem, Stage, StageEvent, WorkOrder

FILE_NAME = "work_order.json"


# -- reading back ------------------------------------------------------------ #
def _event(raw: dict[str, Any]) -> StageEvent:
    return StageEvent(
        stage=Stage(raw["stage"]),
        at=datetime.fromisoformat(raw["at"]),
        operator=raw.get("operator"),
        station=raw.get("station"),
        note=raw.get("note"),
    )


def item_from_dict(raw: dict[str, Any]) -> ProductionItem:
    history = [_event(event) for event in raw.get("history") or []]
    return ProductionItem(
        item_id=raw["item_id"],
        kind=ItemKind(raw["kind"]),
        description=raw.get("description", ""),
        project_id=raw.get("project_id", ""),
        element_ref=raw.get("element_ref"),
        barcode=raw.get("barcode"),
        quantity=int(raw.get("quantity", 1)),
        stage=history[-1].stage if history else Stage(raw.get("stage", "planned")),
        history=history,
        metadata=dict(raw.get("metadata") or {}),
    )


def order_to_dict(order: WorkOrder) -> dict[str, Any]:
    items = []
    for item in order.items:
        entry = item.as_dict()
        entry["metadata"] = dict(item.metadata)
        items.append(entry)
    return {
        "work_order_id": order.work_order_id,
        "project_id": order.project_id,
        "name": order.name,
        "created_at": order.created_at.isoformat(),
        "due_date": order.due_date.isoformat() if order.due_date else None,
        "metadata": dict(order.metadata),
        "items": items,
    }


def order_from_dict(raw: dict[str, Any]) -> WorkOrder:
    return WorkOrder(
        work_order_id=raw["work_order_id"],
        project_id=raw.get("project_id", ""),
        name=raw.get("name", ""),
        created_at=datetime.fromisoformat(raw["created_at"]),
        due_date=datetime.fromisoformat(raw["due_date"]) if raw.get("due_date") else None,
        items=[item_from_dict(entry) for entry in raw.get("items") or []],
        metadata=dict(raw.get("metadata") or {}),
    )


# -- merging two screens' logs ----------------------------------------------- #
def _key(event: StageEvent) -> tuple[Any, ...]:
    return (event.stage.value, event.at.isoformat(), event.operator, event.station, event.note)


def merge(disk: WorkOrder, mine: WorkOrder) -> WorkOrder:
    """Both screens' scans, none lost: each item's two logs joined in time order."""
    if disk.work_order_id != mine.work_order_id:
        raise ProfileOSError(
            f"פקודת העבודה שוחררה מחדש (⁦{disk.work_order_id}⁩) — פתחו את רצפת הייצור "
            "שוב כדי לעבוד על הפקודה העדכנית"
        )
    mine_items = {item.item_id: item for item in mine.items}
    items: list[ProductionItem] = []
    for item in disk.items:
        other = mine_items.pop(item.item_id, None)
        if other is not None:
            events = {_key(event): event for event in item.history}
            events.update({_key(event): event for event in other.history})
            item.history = sorted(events.values(), key=lambda event: event.at)
            item.stage = item.history[-1].stage
        items.append(item)
    items.extend(mine_items.values())
    disk.items = items
    return disk


# -- the job folder ----------------------------------------------------------- #
def work_order_path(job_id: str, store: Any = None) -> Path:
    if store is None:
        from ..projects import default_store

        store = default_store()
    job_file = store.path_for(job_id)
    return job_file.parent / job_file.stem / FILE_NAME


def load_work_order(job_id: str, store: Any = None) -> WorkOrder | None:
    """The job's work order, or ``None`` when none was released.

    A damaged file raises — an empty floor would invite a second release over
    the record of what was already cut.
    """
    path = work_order_path(job_id, store)
    raw = read_json(path, default=None)
    if not raw:
        return None
    return order_from_dict(raw)


def save_work_order(order: WorkOrder, job_id: str, store: Any = None, *,
                    replace: bool = False) -> WorkOrder:
    """Write ``order`` merged with whatever another screen saved meanwhile.

    ``replace`` is a new release: the order on disk, if different, is kept
    beside it as ``work_order-<number>.json`` and this one takes its place.
    Returns the order as it now stands on disk.
    """
    path = work_order_path(job_id, store)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path_lock(path), _between_programs(path):
        raw = read_json(path, default=None)
        current = order_from_dict(raw) if raw else None
        if current is not None and current.work_order_id != order.work_order_id and replace:
            write_json(path.with_name(f"work_order-{current.work_order_id}.json"), raw)
            current = None
        merged = merge(current, order) if current is not None else order
        write_json(path, order_to_dict(merged))
    return merged


@contextmanager
def _between_programs(path: Path, *, wait_s: float = 10.0, stale_s: float = 60.0):
    """One writer at a time across programs — the window and `profileos
    serve`, or two computers on a shared folder. The thread lock does not
    reach another process; a lock file beside the order does. A lock left by
    a program that died is taken over after a minute."""
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + wait_s
    while True:
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(handle, str(os.getpid()).encode())
            os.close(handle)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale_s:
                    lock.unlink()
                    continue
            except OSError:
                continue
            if time.monotonic() > deadline:
                raise ProfileOSError(
                    "פקודת העבודה נשמרת כרגע ממחשב אחר — נסו שוב בעוד רגע")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def open_work_orders(store: Any = None) -> list[tuple[str, WorkOrder]]:
    """``(job_id, order)`` for every job being built — what the tablet by the
    saw works from. A damaged order is left out and logged, not guessed at."""
    from ..core.logging_setup import get_logger
    from ..projects import JobStatus

    if store is None:
        from ..projects import default_store

        store = default_store()
    found: list[tuple[str, WorkOrder]] = []
    for job in store.all():
        if job.status not in (JobStatus.WON, JobStatus.IN_PRODUCTION):
            continue
        try:
            order = load_work_order(job.job_id, store)
        except Exception:  # noqa: BLE001 - the office sees it on its own screen
            get_logger("mes.store").exception("Work order of %s unreadable", job.job_id)
            continue
        if order is not None:
            found.append((job.job_id, order))
    return found


def scans(order: WorkOrder) -> int:
    """How many stage changes the floor has recorded on this order."""
    return sum(
        1 for item in order.items for event in item.history if event.stage is not Stage.PLANNED
    )


__all__ = [
    "FILE_NAME",
    "item_from_dict",
    "load_work_order",
    "merge",
    "open_work_orders",
    "order_from_dict",
    "order_to_dict",
    "save_work_order",
    "scans",
    "work_order_path",
]
