"""Stock: what is on the racks, what it cost, and where it went.

Two things make a stock ledger either trustworthy or decorative.

**Valuation must follow the physical movement.** Aluminium is bought in
batches at prices that move with the LME and the shekel, so what a bar cost
depends on which delivery it came from. FIFO consumes the oldest layer first
and is what a fabricator's accountant will expect; weighted average smooths
the layers into one rate. Both are implemented, both are exact in minor
currency units, and the choice is per item because glass and profile are
usually valued differently.

**The books must agree with the racks.** Every movement produces both a
quantity change and a value change, and the value change is what gets posted
to the ledger. If the two are computed separately they drift; here the
movement returns the value it consumed, and the posting uses that number.

Issuing more than is on hand is refused rather than allowed to go negative.
A negative stock figure is not information, it is a missing goods receipt, and
discovering that at stocktake is far more expensive than at the moment of
issue.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Deque, Iterable, Iterator

from ..core.errors import ProfileOSError
from .ledger import Ledger, money


class StockError(ProfileOSError):
    """A stock movement is not possible."""


class Valuation(StrEnum):
    FIFO = "fifo"
    AVERAGE = "average"


class MovementKind(StrEnum):
    RECEIPT = "receipt"
    ISSUE = "issue"
    #: Stocktake correction, in either direction.
    ADJUSTMENT = "adjustment"
    #: Material returned to the rack from the shop floor.
    RETURN = "return"
    #: An off-cut booked back in, valued at what it cost to make.
    OFFCUT = "offcut"


@dataclass(frozen=True)
class StockItem:
    """Something the shop buys, holds and consumes."""

    code: str
    name: str
    unit: str = "m"
    valuation: Valuation = Valuation.FIFO
    #: Ledger account this item's value sits in.
    account: str = "1300"
    #: Reorder point and quantity, in ``unit``.
    reorder_point: float = 0.0
    reorder_quantity: float = 0.0
    supplier_id: str | None = None
    #: Days from order to delivery, used by the requirements run.
    lead_time_days: int = 14
    category: str = "profile"

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise StockError("A stock item needs a code")


@dataclass
class Layer:
    """One receipt still partly on the rack, at the price it was bought."""

    received: date
    quantity: float
    #: Cost of one ``unit``, in minor currency units. Kept as a float because a
    #: unit rate of an integer number of agorot per metre is the exception, not
    #: the rule; the *value* taken out is rounded to an integer at each issue.
    unit_cost: float
    reference: str | None = None

    @property
    def value(self) -> int:
        return money(self.quantity * self.unit_cost, minor_units=1)


@dataclass(frozen=True)
class Movement:
    """One recorded change, with the value it moved."""

    movement_id: str
    item: str
    kind: MovementKind
    on: date
    quantity: float
    #: Signed value in minor units: positive into stock, negative out of it.
    value: int
    reference: str | None = None
    note: str | None = None
    recorded_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc), compare=False
    )

    @property
    def into_stock(self) -> bool:
        return self.quantity > 0


@dataclass
class ItemState:
    """One item's layers and running totals."""

    item: StockItem
    layers: Deque[Layer] = field(default_factory=deque)
    #: Quantity on order but not yet received.
    on_order: float = 0.0
    #: Quantity promised to jobs but not yet issued.
    allocated: float = 0.0

    @property
    def on_hand(self) -> float:
        return sum(layer.quantity for layer in self.layers)

    @property
    def value(self) -> int:
        return sum(layer.value for layer in self.layers)

    @property
    def available(self) -> float:
        """What may still be promised: on hand, less what is already promised."""
        return self.on_hand - self.allocated

    @property
    def projected(self) -> float:
        """Where the balance lands once orders arrive and allocations ship."""
        return self.on_hand + self.on_order - self.allocated

    @property
    def average_cost(self) -> float:
        quantity = self.on_hand
        return (self.value / quantity) if quantity > 1e-9 else 0.0

    @property
    def below_reorder(self) -> bool:
        return self.projected < self.item.reorder_point - 1e-9


class StockLedger:
    """The stock book: items, layers, movements and their value."""

    #: Quantities closer than this to zero are zero. Bars are measured in
    #: metres to three places; anything smaller is floating-point dust.
    EPSILON = 1e-6

    def __init__(self, items: Iterable[StockItem] = ()) -> None:
        self.items: dict[str, ItemState] = {}
        for item in items:
            self.add_item(item)
        self.movements: list[Movement] = []
        self._counter = 0

    # -- items ---------------------------------------------------------------- #
    def add_item(self, item: StockItem) -> ItemState:
        if item.code in self.items:
            raise StockError(f"Stock item {item.code} already exists", code=item.code)
        state = ItemState(item=item)
        self.items[item.code] = state
        return state

    def state(self, code: str) -> ItemState:
        try:
            return self.items[code]
        except KeyError:
            raise StockError(f"No stock item {code!r}", code=code) from None

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}{self._counter:06d}"

    # -- movements ------------------------------------------------------------ #
    def receive(
        self,
        code: str,
        quantity: float,
        unit_cost: float,
        *,
        on: date | None = None,
        reference: str | None = None,
        kind: MovementKind = MovementKind.RECEIPT,
    ) -> Movement:
        """Put material on the rack at a known price."""
        if quantity <= self.EPSILON:
            raise StockError(
                "A receipt must be for a positive quantity",
                code=code,
                quantity=quantity,
            )
        if unit_cost < 0:
            raise StockError("A negative unit cost is not a price", code=code)
        state = self.state(code)
        when = on or date.today()
        layer = Layer(when, quantity, unit_cost, reference)
        state.layers.append(layer)
        if kind is MovementKind.RECEIPT:
            state.on_order = max(0.0, state.on_order - quantity)
        movement = Movement(
            self._next_id("MV"), code, kind, when, quantity, layer.value, reference
        )
        self.movements.append(movement)
        return movement

    def issue(
        self,
        code: str,
        quantity: float,
        *,
        on: date | None = None,
        reference: str | None = None,
        release_allocation: bool = True,
    ) -> Movement:
        """Take material off the rack, valued by the item's own method."""
        if quantity <= self.EPSILON:
            raise StockError(
                "An issue must be for a positive quantity",
                code=code,
                quantity=quantity,
            )
        state = self.state(code)
        if quantity > state.on_hand + self.EPSILON:
            raise StockError(
                "Not enough stock to issue; a negative balance is a missing "
                "goods receipt, not a quantity",
                code=code,
                requested=round(quantity, 4),
                on_hand=round(state.on_hand, 4),
                unit=state.item.unit,
            )

        consumed = (
            self._consume_fifo(state, quantity)
            if state.item.valuation is Valuation.FIFO
            else self._consume_average(state, quantity)
        )
        if release_allocation:
            state.allocated = max(0.0, state.allocated - quantity)
        movement = Movement(
            self._next_id("MV"),
            code,
            MovementKind.ISSUE,
            on or date.today(),
            -quantity,
            -consumed,
            reference,
        )
        self.movements.append(movement)
        return movement

    def _consume_fifo(self, state: ItemState, quantity: float) -> int:
        """Take from the oldest layers first, returning the value removed."""
        remaining = quantity
        consumed = 0
        while remaining > self.EPSILON:
            layer = state.layers[0]
            take = min(layer.quantity, remaining)
            consumed += money(take * layer.unit_cost, minor_units=1)
            layer.quantity -= take
            remaining -= take
            if layer.quantity <= self.EPSILON:
                state.layers.popleft()
        return consumed

    def _consume_average(self, state: ItemState, quantity: float) -> int:
        """Collapse to one layer at the weighted rate, then take from it."""
        rate = state.average_cost
        remaining_quantity = state.on_hand - quantity
        consumed = money(quantity * rate, minor_units=1)
        state.layers.clear()
        if remaining_quantity > self.EPSILON:
            state.layers.append(Layer(date.today(), remaining_quantity, rate))
        return consumed

    def adjust(
        self,
        code: str,
        counted: float,
        *,
        on: date | None = None,
        reference: str | None = None,
    ) -> Movement | None:
        """Bring the book into line with a stocktake.

        Returns ``None`` when the count already agrees, so a stocktake that
        finds nothing wrong does not litter the ledger with zero movements.
        """
        state = self.state(code)
        difference = counted - state.on_hand
        if abs(difference) <= self.EPSILON:
            return None
        when = on or date.today()
        if difference > 0:
            rate = state.average_cost or 0.0
            layer = Layer(when, difference, rate, reference)
            state.layers.append(layer)
            value = layer.value
        else:
            method = (
                self._consume_fifo
                if state.item.valuation is Valuation.FIFO
                else self._consume_average
            )
            value = -method(state, -difference)
        movement = Movement(
            self._next_id("MV"),
            code,
            MovementKind.ADJUSTMENT,
            when,
            difference,
            value,
            reference,
            note=f"stocktake: counted {counted:g}",
        )
        self.movements.append(movement)
        return movement

    # -- commitments ----------------------------------------------------------- #
    def allocate(self, code: str, quantity: float) -> float:
        """Promise stock to a job. Over-promising is refused."""
        state = self.state(code)
        if quantity > state.available + self.EPSILON:
            raise StockError(
                "Not enough uncommitted stock to allocate",
                code=code,
                requested=round(quantity, 4),
                available=round(state.available, 4),
            )
        state.allocated += quantity
        return state.available

    def release(self, code: str, quantity: float) -> float:
        state = self.state(code)
        state.allocated = max(0.0, state.allocated - quantity)
        return state.available

    def order(self, code: str, quantity: float) -> float:
        state = self.state(code)
        state.on_order += quantity
        return state.on_order

    # -- reporting -------------------------------------------------------------- #
    @property
    def total_value(self) -> int:
        return sum(state.value for state in self.items.values())

    def below_reorder(self) -> list[ItemState]:
        return [state for state in self.items.values() if state.below_reorder]

    def movements_for(self, code: str) -> Iterator[Movement]:
        for movement in self.movements:
            if movement.item == code:
                yield movement

    def valuation_report(self) -> list[dict[str, object]]:
        return [
            {
                "code": state.item.code,
                "name": state.item.name,
                "unit": state.item.unit,
                "on_hand": round(state.on_hand, 3),
                "allocated": round(state.allocated, 3),
                "on_order": round(state.on_order, 3),
                "projected": round(state.projected, 3),
                "value": state.value,
                "unit_cost": round(state.average_cost, 4),
                "method": str(state.item.valuation),
                "below_reorder": state.below_reorder,
            }
            for _, state in sorted(self.items.items())
        ]

    def check(self) -> None:
        """Prove the movements add up to the layers.

        The layers are the answer the system gives; the movement history is
        how it got there. If replaying every movement does not reproduce the
        current value, one of the two is wrong and neither can be trusted.
        """
        replayed: dict[str, int] = {}
        quantities: dict[str, float] = {}
        for movement in self.movements:
            replayed[movement.item] = replayed.get(movement.item, 0) + movement.value
            quantities[movement.item] = (
                quantities.get(movement.item, 0.0) + movement.quantity
            )
        for code, state in self.items.items():
            expected_value = replayed.get(code, 0)
            if state.value != expected_value:
                raise StockError(
                    "Stock value does not match the movement history",
                    code=code,
                    layers=state.value,
                    movements=expected_value,
                    out_by=state.value - expected_value,
                )
            expected_quantity = quantities.get(code, 0.0)
            if abs(state.on_hand - expected_quantity) > 1e-4:
                raise StockError(
                    "Stock quantity does not match the movement history",
                    code=code,
                    layers=round(state.on_hand, 4),
                    movements=round(expected_quantity, 4),
                )

    def summary(self) -> dict[str, object]:
        return {
            "items": len(self.items),
            "movements": len(self.movements),
            "value": self.total_value,
            "below_reorder": len(self.below_reorder()),
        }


def post_movement(ledger: Ledger, movement: Movement, item: StockItem,
                  *, counter_account: str = "5100", entry_id: str | None = None):
    """Post a stock movement to the accounts.

    Material into stock debits the stock account; material out credits it and
    lands in whatever consumed it — usually materials consumed, sometimes a
    variance account for a stocktake difference.
    """
    from .ledger import JournalEntry, Posting

    if movement.value == 0:
        return None
    narrative = f"{movement.kind.value} {item.code} {abs(movement.quantity):g} {item.unit}"
    if movement.value > 0:
        postings = (
            Posting(item.account, movement.value),
            Posting(counter_account, -movement.value),
        )
    else:
        postings = (
            Posting(counter_account, -movement.value),
            Posting(item.account, movement.value),
        )
    return ledger.post(
        JournalEntry(
            entry_id=entry_id or f"ST-{movement.movement_id}",
            date=movement.on,
            narrative=narrative,
            postings=postings,
            source="stock",
            reference=movement.reference,
        )
    )


__all__ = [
    "StockError",
    "Valuation",
    "MovementKind",
    "StockItem",
    "Layer",
    "Movement",
    "ItemState",
    "StockLedger",
    "post_movement",
]
