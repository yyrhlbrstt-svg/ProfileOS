"""Plumbing engine: hydraulics, pipe sizing and network analysis."""

from __future__ import annotations

from .hydraulics import (
    FITTING_K,
    ROUGHNESS_MM,
    FlowRegime,
    Fluid,
    colebrook_white,
    darcy_weisbach_loss,
    fitting_loss,
    friction_factor,
    hazen_williams_loss,
    pressure_to_head,
    reynolds_number,
    static_head,
    swamee_jain,
    total_k,
    velocity,
    water_at,
)
from .network import Loop, NetworkResult, Node, Pipe, PipeNetwork
from .pipes import (
    BUILTIN_CATALOGUES,
    COPPER_EN1057,
    PIPE_CATALOGUE_SCHEMA,
    PPR_PN20,
    STEEL_EN10255,
    DesignLimits,
    PipeCatalogue,
    PipeSize,
    ServiceType,
    SizingResult,
    get_catalogue,
    register_catalogue,
    size_pipe,
)

__all__ = [
    "Fluid", "water_at", "FlowRegime", "ROUGHNESS_MM", "FITTING_K",
    "velocity", "reynolds_number", "swamee_jain", "colebrook_white",
    "friction_factor", "darcy_weisbach_loss", "hazen_williams_loss",
    "fitting_loss", "total_k", "static_head", "pressure_to_head",
    "ServiceType", "DesignLimits", "PipeSize", "PipeCatalogue", "SizingResult",
    "size_pipe", "COPPER_EN1057", "PPR_PN20", "STEEL_EN10255",
    "BUILTIN_CATALOGUES", "get_catalogue", "register_catalogue",
    "PIPE_CATALOGUE_SCHEMA",
    "Node", "Pipe", "Loop", "NetworkResult", "PipeNetwork",
]
