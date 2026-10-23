"""Handing the work to somebody else's software.

Everything else in this suite is about making the window. This is about the
architect, the engineer and the main contractor who need what was made in a
form their own software reads.
"""

from .ifc import IfcOptions, write_ifc

__all__ = ["IfcOptions", "write_ifc"]
