"""Issuing the job's tax invoice in Morning (חשבונית ירוקה), from the job.

The shop issues its invoices in Morning. Until now that meant typing the
customer, the description and the amount a second time, then copying the
invoice number back into the job by hand — and an invoice nobody copied back
was an invoice the job's account did not know about.

Here the invoice is made from the job's own figures and issued through
Morning's public API, and the number, the link and the amount are recorded on
the job in the same step. Nothing goes to Morning unless the person presses
"issue" and confirms: a tax invoice is a legal document that cannot be
deleted, only cancelled with a credit note.

The API, as published by Morning and its maintained client library
(``green-invoice`` 2.0.0): an OAuth 2.0 client-credentials token from
``api.morning.co``; documents at ``/v1/documents`` on
``api.greeninvoice.co.il``; type 305 is a tax invoice, 320 a tax
invoice-receipt; payment types 1 cash, 2 cheque, 3 card, 4 transfer, 10 a
payment app. A sandbox account works against Morning's sandbox hosts.

The key and secret are the shop's own, kept beside the data in a file only
this user can read, and sent nowhere but Morning.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ..core.errors import ProfileOSError
from ..core.logging_setup import get_logger

_log = get_logger("erp.morning")

API = {"live": "https://api.greeninvoice.co.il/api",
       "sandbox": "https://sandbox.d.greeninvoice.co.il/api"}
TOKEN = {"live": "https://api.morning.co/idp/v1/oauth/token",
         "sandbox": "https://api.sandbox.morning.dev/idp/v1/oauth/token"}

TAX_INVOICE = 305
TAX_INVOICE_RECEIPT = 320
KIND_HEBREW = {TAX_INVOICE: "חשבונית מס", TAX_INVOICE_RECEIPT: "חשבונית מס־קבלה"}

#: The shop's payment methods, as Morning numbers them.
PAYMENT_TYPES = {"cash": 1, "cheque": 2, "credit": 3, "transfer": 4, "bit": 10, "other": 11}

TIMEOUT_S = 30


# --------------------------------------------------------------------------- #
# The shop's key
# --------------------------------------------------------------------------- #
@dataclass
class MorningAccount:
    key_id: str = ""
    secret: str = ""
    sandbox: bool = False

    @property
    def ready(self) -> bool:
        return bool(self.key_id.strip() and self.secret.strip())

    @property
    def env(self) -> str:
        return "sandbox" if self.sandbox else "live"


def account_path() -> Path:
    from ..core.config import get_settings

    return get_settings().data_dir / "integrations" / "morning.json"


def load_account() -> MorningAccount:
    """The key as saved; an empty account when none was. A damaged file is
    refused (read_json), never read as "no key"."""
    from ..core.storage import read_json

    raw = read_json(account_path(), default={}) or {}
    return MorningAccount(key_id=str(raw.get("key_id", "")), secret=str(raw.get("secret", "")),
                          sandbox=bool(raw.get("sandbox", False)))


def save_account(account: MorningAccount) -> None:
    from ..core.storage import write_json

    path = account_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, {"key_id": account.key_id.strip(), "secret": account.secret.strip(),
                      "sandbox": account.sandbox})
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - Windows keeps it in the user's profile
        pass


# --------------------------------------------------------------------------- #
# The document
# --------------------------------------------------------------------------- #
@dataclass
class InvoiceDraft:
    """What the invoice will say, before anything is sent."""

    kind: int
    description: str
    #: Before VAT; Morning adds VAT for a licensed business ("vatType": 0).
    net: float
    client_name: str
    client_tax_id: str = ""
    client_email: str = ""
    client_phone: str = ""
    client_address: str = ""
    client_city: str = ""
    #: For an invoice-receipt: how the money came, and when.
    payment_method: str = "transfer"
    paid_on: str = ""
    cheque_number: str = ""
    bank: str = ""
    vat_rate: float = 0.18
    remarks: str = ""
    lines: list[tuple[str, float, float]] = field(default_factory=list)

    @property
    def gross(self) -> float:
        return round(self.net * (1 + self.vat_rate), 2)

    def problems(self) -> list[str]:
        found = []
        if self.kind not in KIND_HEBREW:
            found.append("סוג מסמך לא מוכר")
        if not self.client_name.strip():
            found.append("חסר שם הלקוח")
        if self.net <= 0:
            found.append("הסכום חייב להיות חיובי")
        if not self.description.strip():
            found.append("חסר תיאור")
        if self.kind == TAX_INVOICE_RECEIPT and self.payment_method not in PAYMENT_TYPES:
            found.append("לא נבחר אמצעי תשלום")
        if self.kind == TAX_INVOICE_RECEIPT and self.payment_method == "cheque" \
                and not self.cheque_number.strip():
            found.append("לצ׳ק צריך מספר צ׳ק")
        return found

    def payload(self) -> dict[str, Any]:
        """The document as Morning's API takes it."""
        client: dict[str, Any] = {"name": self.client_name.strip(), "add": True}
        for key, value in (("taxId", self.client_tax_id), ("phone", self.client_phone),
                           ("address", self.client_address), ("city", self.client_city)):
            if value.strip():
                client[key] = value.strip()
        if self.client_email.strip():
            client["emails"] = [self.client_email.strip()]
        rows = self.lines or [(self.description, 1.0, self.net)]
        document: dict[str, Any] = {
            "type": self.kind,
            "description": self.description.strip(),
            "date": date.today().isoformat(),
            "lang": "he",
            "currency": "ILS",
            "vatType": 0,
            "rounding": False,
            "signed": True,
            "client": client,
            "income": [
                {"description": text, "quantity": quantity, "price": round(price, 2),
                 "currency": "ILS", "vatType": 0}
                for text, quantity, price in rows
            ],
        }
        if self.remarks.strip():
            document["remarks"] = self.remarks.strip()
        if self.kind == TAX_INVOICE_RECEIPT:
            payment: dict[str, Any] = {
                "type": PAYMENT_TYPES[self.payment_method],
                "date": self.paid_on or date.today().isoformat(),
                "price": self.gross, "currency": "ILS",
            }
            if self.payment_method == "cheque":
                payment["chequeNum"] = self.cheque_number.strip()
                if self.bank.strip():
                    payment["bankName"] = self.bank.strip()
            if self.payment_method == "credit":
                payment["dealType"] = 1
            document["payment"] = [payment]
        return document


@dataclass(frozen=True)
class Issued:
    document_id: str
    number: str
    url: str


# --------------------------------------------------------------------------- #
# Talking to Morning
# --------------------------------------------------------------------------- #
class MorningClient:
    def __init__(self, account: MorningAccount, *, api_url: str | None = None,
                 token_url: str | None = None) -> None:
        if not account.ready:
            raise ProfileOSError("לא הוזן מפתח Morning — מערכת ← חיבורים")
        self.account = account
        self.api_url = (api_url or API[account.env]).rstrip("/")
        self.token_url = token_url or TOKEN[account.env]
        self._token = ""
        self._expires = 0.0

    def _post(self, url: str, body: dict[str, Any], *, auth: bool) -> dict[str, Any]:
        headers = {"Content-Type": "application/json", "User-Agent": "ProfileOS"}
        if auth:
            headers["Authorization"] = "Bearer " + self.token()
        request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                         headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
                text = response.read().decode("utf-8") or "{}"
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ProfileOSError(f"Morning סירב: {_said(detail) or exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProfileOSError(
                "אין חיבור ל־Morning — בדקו את האינטרנט ונסו שוב. שום מסמך לא הופק.") from exc
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            raise ProfileOSError("Morning ענה תשובה לא מובנת — בדקו שם אם המסמך הופק") from exc
        return parsed if isinstance(parsed, dict) else {}

    def token(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        body = self._post(self.token_url, {
            "grant_type": "client_credentials",
            "client_id": self.account.key_id.strip(),
            "client_secret": self.account.secret.strip(),
        }, auth=False)
        token = body.get("accessToken")
        if not token:
            raise ProfileOSError("Morning לא אישר את המפתח — בדקו את מזהה המפתח והסוד")
        self._token = str(token)
        try:
            self._expires = float(body.get("expiresAt"))
        except (TypeError, ValueError):
            self._expires = time.time() + 3600
        return self._token

    def issue(self, draft: InvoiceDraft) -> Issued:
        problems = draft.problems()
        if problems:
            raise ProfileOSError("המסמך לא הופק: " + "; ".join(problems))
        body = self._post(self.api_url + "/v1/documents", draft.payload(), auth=True)
        document_id = str(body.get("id") or "")
        number = body.get("number")
        if not document_id or number in (None, ""):
            raise ProfileOSError("Morning לא החזיר מספר מסמך — בדקו שם אם הוא הופק")
        url = body.get("url") or {}
        link = (url.get("he") or url.get("origin") or "") if isinstance(url, dict) else str(url)
        _log.info("Morning issued %s number %s", draft.kind, number)
        return Issued(document_id=document_id, number=str(number), url=str(link))


def _said(text: str) -> str:
    """Morning's own error message, from whichever shape it came in."""
    try:
        body = json.loads(text)
    except ValueError:
        return text.strip()[:300]
    if isinstance(body, dict):
        for key in ("error_description", "errorMessage", "message", "description", "error"):
            if isinstance(body.get(key), str) and body[key]:
                return body[key]
        messages = body.get("messages")
        if isinstance(messages, dict) and isinstance(messages.get("message"), dict):
            return str(messages["message"].get("description", ""))
    return str(body)[:300]


__all__ = [
    "Issued",
    "InvoiceDraft",
    "KIND_HEBREW",
    "MorningAccount",
    "MorningClient",
    "PAYMENT_TYPES",
    "TAX_INVOICE",
    "TAX_INVOICE_RECEIPT",
    "account_path",
    "load_account",
    "save_account",
]
