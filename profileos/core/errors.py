"""Exception hierarchy for ProfileOS.

Every engine raises a subclass of :class:`ProfileOSError`, so an application
embedding ProfileOS can catch one base class and still discriminate on the
concrete type when it needs to.
"""

from __future__ import annotations

from typing import Any


class ProfileOSError(Exception):
    """Base class for every error raised by ProfileOS.

    Parameters
    ----------
    message:
        Human readable description of the failure.
    context:
        Optional structured detail (entity handles, file names, solver status
        codes...) attached to the error for logging and UI display.
    """

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = dict(context)

    def __str__(self) -> str:  # pragma: no cover - trivial
        if not self.context:
            return self.message
        detail = ", ".join(f"{k}={v!r}" for k, v in sorted(self.context.items()))
        return f"{self.message} ({detail})"


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #
class GeometryError(ProfileOSError):
    """Raised for any failure while reading or reconstructing geometry."""


class DxfReadError(GeometryError):
    """The DXF document could not be opened, or contains no usable entities."""


class ContourError(GeometryError):
    """Loose segments could not be chained into closed contours."""


class TopologyError(GeometryError):
    """Contours could not be resolved into an outer boundary / hole hierarchy."""


# --------------------------------------------------------------------------- #
# Structural analysis
# --------------------------------------------------------------------------- #
class StructuralError(ProfileOSError):
    """Raised when section properties cannot be computed."""


class DegenerateSectionError(StructuralError):
    """The section has zero (or negative) area and cannot be analysed."""


class WarpingAnalysisError(StructuralError):
    """The finite-element torsion/warping solution failed to converge."""


# --------------------------------------------------------------------------- #
# Nesting
# --------------------------------------------------------------------------- #
class NestingError(ProfileOSError):
    """Raised when the cutting-stock problem cannot be solved."""


class InfeasibleNestingError(NestingError):
    """A required part is longer than every available stock bar."""


# --------------------------------------------------------------------------- #
# CNC
# --------------------------------------------------------------------------- #
class CncError(ProfileOSError):
    """Raised while generating machine code."""


class PostProcessorError(CncError):
    """The requested post-processor is unknown or rejected the job."""


class CollisionError(CncError):
    """A tool/clamp collision could not be resolved automatically."""


class ToolingError(CncError):
    """A referenced tool is missing from the tool database, or is unsuitable."""


# --------------------------------------------------------------------------- #
# Plumbing / quoting
# --------------------------------------------------------------------------- #
class HydraulicsError(ProfileOSError):
    """Raised when a pipework calculation cannot converge or is out of range."""


class QuotingError(ProfileOSError):
    """Raised when a quotation cannot be priced (missing rates, bad currency...)."""


# --------------------------------------------------------------------------- #
# Security / licensing
# --------------------------------------------------------------------------- #
class SecurityError(ProfileOSError):
    """Base class for authentication and licensing failures."""


class LicenseError(SecurityError):
    """The license is missing, expired, malformed or bound to other hardware."""


class AuthenticatorError(SecurityError):
    """The hardware authenticator response failed verification."""


# --------------------------------------------------------------------------- #
# Plugins / configuration
# --------------------------------------------------------------------------- #
class PluginError(ProfileOSError):
    """A plugin failed to load, validate or reload."""


class PluginValidationError(PluginError):
    """A plugin was rejected by static (AST) or schema validation."""


class ConfigError(ProfileOSError):
    """Configuration is missing or invalid."""


__all__ = [
    "ProfileOSError",
    "GeometryError",
    "DxfReadError",
    "ContourError",
    "TopologyError",
    "StructuralError",
    "DegenerateSectionError",
    "WarpingAnalysisError",
    "NestingError",
    "InfeasibleNestingError",
    "CncError",
    "PostProcessorError",
    "CollisionError",
    "ToolingError",
    "HydraulicsError",
    "QuotingError",
    "SecurityError",
    "LicenseError",
    "AuthenticatorError",
    "PluginError",
    "PluginValidationError",
    "ConfigError",
]
