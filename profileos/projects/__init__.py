"""Job files: the shop's own record of the work it has taken on.

The engines in this suite compute; this module remembers. A job file names the
customer, holds the schedule of openings, and carries the commercial status
from the first phone call to the finished installation.
"""

from .model import (
    Customer,
    CustomerNeed,
    InvoiceRecord,
    JobError,
    JobFile,
    JobLine,
    JobStatus,
    LineKind,
    Payment,
    PaymentMethod,
    PlumbingSpec,
    MaterialOrder,
    MaterialOrderLine,
    MaterialOrderState,
    ProgressBill,
    Snag,
    StatusEvent,
    Trade,
    TRANSITIONS,
)
from .dossier import render_dossier, write_dossier
from .store import CustomerBook, JobStore, default_customers, default_store

__all__ = [
    "Customer",
    "CustomerBook",
    "CustomerNeed",
    "InvoiceRecord",
    "JobLine",
    "LineKind",
    "Payment",
    "PaymentMethod",
    "PlumbingSpec",
    "MaterialOrder",
    "MaterialOrderLine",
    "MaterialOrderState",
    "ProgressBill",
    "Snag",
    "Trade",
    "JobError",
    "JobFile",
    "JobStatus",
    "JobStore",
    "StatusEvent",
    "TRANSITIONS",
    "default_customers",
    "default_store",
    "render_dossier",
    "write_dossier",
]
