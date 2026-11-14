"""Pipe network analysis by the Hardy Cross method.

A looped network has more unknowns than continuity alone can fix: flow can
divide between parallel paths in infinitely many ways that all satisfy mass
balance. The extra condition is energy — around any closed loop the head losses
must sum to zero.

Hardy Cross enforces that iteratively. Starting from any flow distribution that
satisfies continuity, each loop is corrected by

.. math::
    \\Delta Q = -\\frac{\\sum h_f}{n \\sum |h_f / Q|}

where ``h_f = r Q^n`` is the loss in each pipe and ``n`` is the exponent of the
friction law (2 for Darcy-Weisbach, 1.852 for Hazen-Williams). Because the
correction is applied to every pipe in the loop equally, continuity is
preserved at every step — which is what makes the method converge from a crude
starting guess.

Sign convention: a pipe's flow is positive in its declared ``start -> end``
direction. A loop lists pipes with ``+1`` when traversed in that direction and
``-1`` against it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from ..core.errors import HydraulicsError
from ..core.logging_setup import get_logger
from ..core.profiling import timed
from .hydraulics import Fluid, darcy_weisbach_loss, fitting_loss, total_k
from .pipes import PipeCatalogue, PipeSize

_log = get_logger("plumbing.network")


@dataclass
class Node:
    """A junction in the network."""

    node_id: str
    #: Elevation above datum [m].
    elevation: float = 0.0
    #: Positive is demand drawn off, negative is supply into the network [L/s].
    demand: float = 0.0
    #: Fixed head [m] for a reservoir or a mains connection; ``None`` if free.
    fixed_head: float | None = None
    label: str | None = None

    @property
    def is_source(self) -> bool:
        return self.fixed_head is not None


@dataclass
class Pipe:
    """A pipe connecting two nodes."""

    pipe_id: str
    start: str
    end: str
    length: float
    size: PipeSize
    catalogue: PipeCatalogue
    fittings: dict[str, int] = field(default_factory=dict)
    #: Current flow estimate, positive from ``start`` to ``end`` [L/s].
    flow: float = 0.0

    @property
    def bore(self) -> float:
        return self.size.internal_diameter

    def head_loss(self, fluid: Fluid | None = None) -> float:
        """Signed head loss [Pa], positive when flow runs start -> end."""
        magnitude = abs(self.flow)
        if magnitude < 1e-12:
            return 0.0
        friction = darcy_weisbach_loss(
            magnitude,
            self.bore,
            self.length,
            roughness_mm=self.catalogue.effective_roughness,
            fluid=fluid,
        )
        minor = fitting_loss(magnitude, self.bore, total_k(self.fittings), fluid)
        return math.copysign(friction + minor, self.flow)

    def resistance(self, fluid: Fluid | None = None) -> float:
        """``r`` in ``h = r Q^2``, evaluated at the current flow [Pa/(L/s)^2].

        Recomputed each iteration because the Darcy friction factor depends on
        Reynolds number, so ``r`` is only locally constant.
        """
        magnitude = abs(self.flow)
        if magnitude < 1e-9:
            return 0.0
        return abs(self.head_loss(fluid)) / (magnitude**2)

    def velocity(self) -> float:
        from .hydraulics import velocity as _velocity

        return _velocity(abs(self.flow), self.bore)


@dataclass
class Loop:
    """A closed circuit of pipes, with a traversal direction for each."""

    loop_id: str
    #: ``(pipe_id, +1 or -1)`` pairs.
    members: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class NetworkResult:
    """The outcome of a network solve."""

    converged: bool
    iterations: int
    max_correction: float
    flows: dict[str, float] = field(default_factory=dict)
    velocities: dict[str, float] = field(default_factory=dict)
    head_losses: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        return {
            "converged": self.converged,
            "iterations": self.iterations,
            "max_correction_lps": round(self.max_correction, 6),
            "pipes": len(self.flows),
            "max_velocity": round(max(self.velocities.values(), default=0.0), 3),
            "warnings": len(self.warnings),
        }


class PipeNetwork:
    """A looped pipe network solved by Hardy Cross."""

    def __init__(self, fluid: Fluid | None = None) -> None:
        self.nodes: dict[str, Node] = {}
        self.pipes: dict[str, Pipe] = {}
        self.loops: list[Loop] = []
        self.fluid = fluid or Fluid()

    # -- construction -------------------------------------------------------- #
    def add_node(self, node: Node) -> "PipeNetwork":
        if node.node_id in self.nodes:
            raise HydraulicsError("Duplicate node id", node=node.node_id)
        self.nodes[node.node_id] = node
        return self

    def add_pipe(self, pipe: Pipe) -> "PipeNetwork":
        if pipe.pipe_id in self.pipes:
            raise HydraulicsError("Duplicate pipe id", pipe=pipe.pipe_id)
        for endpoint in (pipe.start, pipe.end):
            if endpoint not in self.nodes:
                raise HydraulicsError(
                    "Pipe references an unknown node", pipe=pipe.pipe_id, node=endpoint
                )
        self.pipes[pipe.pipe_id] = pipe
        return self

    def add_loop(self, loop: Loop) -> "PipeNetwork":
        for pipe_id, direction in loop.members:
            if pipe_id not in self.pipes:
                raise HydraulicsError(
                    "Loop references an unknown pipe", loop=loop.loop_id, pipe=pipe_id
                )
            if direction not in (1, -1):
                raise HydraulicsError(
                    "Loop direction must be +1 or -1", loop=loop.loop_id, direction=direction
                )
        self.loops.append(loop)
        return self

    # -- checks --------------------------------------------------------------- #
    def continuity_error(self) -> dict[str, float]:
        """Net inflow minus demand at each node [L/s]; zero when balanced."""
        balance = {node_id: -node.demand for node_id, node in self.nodes.items()}
        for pipe in self.pipes.values():
            balance[pipe.start] -= pipe.flow
            balance[pipe.end] += pipe.flow
        # Source nodes absorb whatever imbalance remains.
        for node_id, node in self.nodes.items():
            if node.is_source:
                balance[node_id] = 0.0
        return balance

    def validate(self) -> list[str]:
        problems: list[str] = []
        total_demand = sum(node.demand for node in self.nodes.values() if node.demand > 0)
        total_supply = sum(-node.demand for node in self.nodes.values() if node.demand < 0)
        sources = [n for n in self.nodes.values() if n.is_source]

        if not sources and abs(total_supply - total_demand) > 1e-6:
            problems.append(
                f"Demand ({total_demand:.3f} L/s) does not match supply "
                f"({total_supply:.3f} L/s) and there is no fixed-head source."
            )
        if not self.loops:
            problems.append("No loops defined; a branched network needs no Hardy Cross solve.")

        for node_id, error in self.continuity_error().items():
            if abs(error) > 1e-6:
                problems.append(
                    f"Initial flows violate continuity at {node_id} by {error:.4f} L/s."
                )
        return problems

    # -- the solve -------------------------------------------------------------- #
    @timed("plumbing.hardy_cross")
    def solve(
        self, *, tolerance: float = 1e-6, max_iterations: int = 200, exponent: float = 2.0
    ) -> NetworkResult:
        """Balance the network by Hardy Cross.

        ``tolerance`` is on the loop flow correction [L/s]. The initial flows
        must already satisfy continuity — :meth:`validate` checks that, because
        starting out of balance means the method converges to a solution that
        does not conserve mass.
        """
        if not self.loops:
            raise HydraulicsError("Hardy Cross needs at least one loop")

        problems = [p for p in self.validate() if "continuity" in p]
        if problems:
            raise HydraulicsError(
                "Initial flows must satisfy continuity before solving", problems=problems
            )

        max_correction = 0.0
        iteration = 0
        for iteration in range(1, max_iterations + 1):
            max_correction = 0.0

            for loop in self.loops:
                numerator = 0.0
                denominator = 0.0

                for pipe_id, direction in loop.members:
                    pipe = self.pipes[pipe_id]
                    # Head loss as seen when traversing the loop.
                    loss = pipe.head_loss(self.fluid) * direction
                    numerator += loss
                    magnitude = abs(pipe.flow)
                    if magnitude > 1e-12:
                        denominator += abs(loss) / magnitude

                if denominator < 1e-15:
                    continue
                correction = -numerator / (exponent * denominator)

                for pipe_id, direction in loop.members:
                    self.pipes[pipe_id].flow += correction * direction

                max_correction = max(max_correction, abs(correction))

            if max_correction < tolerance:
                break

        converged = max_correction < tolerance
        result = NetworkResult(
            converged=converged,
            iterations=iteration,
            max_correction=max_correction,
            flows={pid: pipe.flow for pid, pipe in self.pipes.items()},
            velocities={pid: pipe.velocity() for pid, pipe in self.pipes.items()},
            head_losses={pid: pipe.head_loss(self.fluid) for pid, pipe in self.pipes.items()},
        )

        if not converged:
            result.warnings.append(
                f"Hardy Cross did not converge in {max_iterations} iterations "
                f"(last correction {max_correction:.3e} L/s)."
            )
        for pipe_id, v in result.velocities.items():
            if v > 3.0:
                result.warnings.append(
                    f"Pipe {pipe_id} runs at {v:.2f} m/s, above the 3 m/s erosion threshold."
                )

        _log.info(
            "Hardy Cross %s after %d iteration(s), max correction %.3e L/s",
            "converged" if converged else "stopped",
            iteration,
            max_correction,
        )
        return result

    def node_heads(self, reference: str) -> dict[str, float]:
        """Head at every node [m], propagated from a reference node.

        Walks the network breadth-first, subtracting each pipe's loss. Nodes
        unreachable from the reference are omitted rather than guessed.
        """
        from collections import deque

        from .hydraulics import pressure_to_head

        if reference not in self.nodes:
            raise HydraulicsError("Unknown reference node", node=reference)

        start_node = self.nodes[reference]
        heads: dict[str, float] = {
            reference: start_node.fixed_head
            if start_node.fixed_head is not None
            else start_node.elevation
        }
        queue: deque[str] = deque([reference])

        while queue:
            current = queue.popleft()
            for pipe in self.pipes.values():
                loss_head = pressure_to_head(abs(pipe.head_loss(self.fluid)), self.fluid)
                if pipe.start == current and pipe.end not in heads:
                    # Flowing start -> end loses head; the reverse gains it.
                    heads[pipe.end] = heads[current] - math.copysign(loss_head, pipe.flow)
                    queue.append(pipe.end)
                elif pipe.end == current and pipe.start not in heads:
                    heads[pipe.start] = heads[current] + math.copysign(loss_head, pipe.flow)
                    queue.append(pipe.start)
        return heads


__all__ = ["Node", "Pipe", "Loop", "NetworkResult", "PipeNetwork"]
