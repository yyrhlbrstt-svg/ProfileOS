"""ProfileOS — integrated CAD/CAM suite for architectural aluminium profile systems.

The package is organised into independent engines that communicate through
plain :mod:`pydantic` models, so any engine can be used on its own:

``profileos.geometry``
    DXF ingestion, contour reconstruction and topological analysis of profile
    cross-sections.
``profileos.structural``
    Exact section properties (Green's theorem) plus finite-element torsion and
    warping constants.
``profileos.nesting``
    1D cutting-stock optimisation with kerf and miter compensation.
``profileos.cnc``
    Machine post-processors (Elumatec, Schueco, Kaban, ISO G-code) and clamp
    collision avoidance.
``profileos.plumbing``
    Pipework hydraulics and sizing.
``profileos.quoting``
    Bill of materials, costing and supplier quotations.
``profileos.security``
    WebAuthn/FIDO2 hardware licensing and offline license sealing.
``profileos.core``
    Configuration, logging, plugin registry and the hot-reload framework.
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
