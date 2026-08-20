"""Job files: the shop's own record of the work it has taken on.

The engines in this suite compute; this module remembers. A job file names the
customer, holds the schedule of openings, and carries the commercial status
from the first phone call to the finished installation.
"""

from .model import (
    Customer,
    JobError,
    JobFile,
    JobStatus,
    StatusEvent,
    TRANSITIONS,
)
from .store import CustomerBook, JobStore, default_customers, default_store

__all__ = [
    "Customer",
    "CustomerBook",
    "JobError",
    "JobFile",
    "JobStatus",
    "JobStore",
    "StatusEvent",
    "TRANSITIONS",
    "default_customers",
    "default_store",
]
