"""The job's tax invoice issued in Morning — against a stand-in Morning that
records exactly what it was sent."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from profileos.erp.morning import (
    TAX_INVOICE, TAX_INVOICE_RECEIPT, InvoiceDraft, MorningAccount, MorningClient,
    load_account, save_account,
)


class FakeMorning:
    """Answers the token and the document request the way Morning does."""

    def __init__(self, *, refuse: str = "") -> None:
        self.seen: list[tuple[str, dict, dict]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.seen.append((self.path, body, dict(self.headers)))
                if self.path.endswith("/token"):
                    if body.get("client_secret") != "s3cret":
                        return self._send(401, {"error": "invalid_client",
                                                "error_description": "Bad credentials"})
                    return self._send(200, {"accessToken": "TOKEN-1", "expiresAt": 9999999999})
                if self.headers.get("Authorization") != "Bearer TOKEN-1":
                    return self._send(401, {"errorMessage": "Unauthorized"})
                if refuse:
                    return self._send(400, {"errorCode": 1003, "errorMessage": refuse})
                return self._send(200, {"id": "doc-77", "number": 40012,
                                        "url": {"he": "https://example.test/doc-77"}})

            def _send(self, code, payload):
                data = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def morning(monkeypatch):
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.delenv(name, raising=False)
    fake = FakeMorning()
    from profileos.erp import morning as module

    monkeypatch.setitem(module.API, "live", fake.url + "/api")
    monkeypatch.setitem(module.TOKEN, "live", fake.url + "/idp/v1/oauth/token")
    yield fake
    fake.close()


@pytest.fixture
def shop(tmp_path, monkeypatch):
    from profileos.core.config import reload_settings

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
    reload_settings()
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    yield tmp_path / "data"
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    reload_settings()


def _draft(**changes) -> InvoiceDraft:
    values = dict(kind=TAX_INVOICE, description="עבודות אלומיניום — וילה", net=10_000,
                  client_name="משה כהן", client_tax_id="514000000", client_email="m@example.test")
    values.update(changes)
    return InvoiceDraft(**values)


def test_a_tax_invoice_is_sent_as_morning_takes_it(morning):
    issued = MorningClient(MorningAccount("key", "s3cret")).issue(_draft())
    assert (issued.number, issued.document_id, issued.url) == ("40012", "doc-77",
                                                               "https://example.test/doc-77")
    token_path, token_body, _ = morning.seen[0]
    assert token_path == "/idp/v1/oauth/token"
    assert token_body == {"grant_type": "client_credentials", "client_id": "key",
                          "client_secret": "s3cret"}
    path, document, headers = morning.seen[1]
    assert path == "/api/v1/documents" and headers["Authorization"] == "Bearer TOKEN-1"
    assert document["type"] == 305 and document["lang"] == "he" and document["vatType"] == 0
    assert document["client"] == {"name": "משה כהן", "add": False, "taxId": "514000000",
                                  "emails": ["m@example.test"]}
    assert document["income"] == [{"description": "עבודות אלומיניום — וילה", "quantity": 1.0,
                                   "price": 10_000, "currency": "ILS", "vatType": 0}]
    assert "payment" not in document


def test_an_invoice_receipt_carries_the_payment_with_vat(morning):
    MorningClient(MorningAccount("key", "s3cret")).issue(
        _draft(kind=TAX_INVOICE_RECEIPT, payment_method="cheque", cheque_number="4501",
               bank="12 הפועלים", vat_rate=0.18))
    payment = morning.seen[-1][1]["payment"][0]
    assert payment["type"] == 2 and payment["price"] == 11_800 and payment["chequeNum"] == "4501"


def test_morning_refusing_is_said_in_its_words_and_nothing_is_recorded(morning):
    with pytest.raises(Exception, match="Bad credentials"):
        MorningClient(MorningAccount("key", "wrong")).issue(_draft())
    with pytest.raises(Exception, match="חסר שם הלקוח"):
        MorningClient(MorningAccount("key", "s3cret")).issue(_draft(client_name=" "))


def test_the_key_is_kept_for_this_user_only(shop):
    save_account(MorningAccount("key", "s3cret", sandbox=True))
    again = load_account()
    assert again.ready and again.sandbox and again.secret == "s3cret"
    from profileos.erp.morning import account_path

    if os.name == "posix":
        assert (account_path().stat().st_mode & 0o777) == 0o600


def test_issued_from_the_jobs_account_and_recorded_on_the_job(shop, morning, monkeypatch):
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

    from profileos.projects import JobFile, JobStatus, default_store
    from profileos.projects.account import job_account
    from profileos.ui import money_dialogs

    QApplication.instance() or QApplication([])
    save_account(MorningAccount("key", "s3cret"))
    default_store().save(JobFile(job_id="J-2026-0009", name="וילה", customer_name="משה כהן",
                                 status=JobStatus.INSTALLED, quote_total=20_000))

    suggested = []

    def fill(dialog):
        suggested.append(dialog.net.value())                      # what is not yet invoiced
        dialog.kind.setCurrentIndex(dialog.kind.findData(TAX_INVOICE_RECEIPT))
        dialog.net.setValue(5_000)
        dialog.method.setCurrentIndex(dialog.method.findData("transfer"))
        assert not dialog.keep_payment.isChecked()                # never doubled by default
        dialog.keep_payment.setChecked(True)
        return QDialog.DialogCode.Accepted

    asked = []
    monkeypatch.setattr(money_dialogs.MorningInvoiceDialog, "exec", fill)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: 0)
    dialog = money_dialogs.JobAccountDialog("J-2026-0009", default_store())
    issued = dialog.issue_in_morning()
    assert issued.number == "40012" and "⁦5,900.00 ₪⁩" in asked[0]
    assert suggested == [pytest.approx(20_000)]
    job = default_store().load("J-2026-0009")
    [invoice] = job.invoices
    assert invoice.number == "40012" and invoice.net == 5_000 and invoice.issuer == "Morning"
    assert invoice.kind == "חשבונית מס־קבלה" and invoice.url.endswith("doc-77")
    [payment] = job.payments
    assert payment.amount == pytest.approx(5_900) and "40012" in payment.reference
    account = job_account(job, [])
    assert account.invoiced_net == 5_000 and account.paid == pytest.approx(5_900)

    # Said no at the confirmation: nothing goes to Morning.
    sent = len(morning.seen)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    assert dialog.issue_in_morning() is None and len(morning.seen) == sent
    assert suggested[-1] == pytest.approx(15_000)               # the rest, next time
    dialog.deleteLater()


def test_no_answer_after_sending_is_unclear_not_nothing_and_the_job_remembers(shop, monkeypatch):
    """A timeout after the document went out: the person is told it may exist,
    and the job keeps saying so until somebody checked."""
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from datetime import date

    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

    from profileos.erp import morning as module
    from profileos.insights import scan
    from profileos.projects import JobFile, JobStatus, default_store
    from profileos.ui import money_dialogs

    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.delenv(name, raising=False)
    fake = FakeMorning()
    monkeypatch.setitem(module.API, "live", fake.url + "/api")
    monkeypatch.setitem(module.TOKEN, "live", fake.url + "/idp/v1/oauth/token")
    real_post = module.MorningClient._post

    def slow(self, url, body, *, auth, issuing=False):
        if issuing:
            real_post(self, url, body, auth=auth, issuing=issuing)   # Morning got it…
            raise module.MaybeIssued("Morning לא ענה בזמן — לא ברור אם המסמך הופק")
        return real_post(self, url, body, auth=auth, issuing=issuing)

    monkeypatch.setattr(module.MorningClient, "_post", slow)
    QApplication.instance() or QApplication([])
    save_account(MorningAccount("key", "s3cret"))
    default_store().save(JobFile(job_id="J-2026-0010", name="וילה", customer_name="כהן",
                                 status=JobStatus.INSTALLED, quote_total=10_000))
    warned = []
    monkeypatch.setattr(money_dialogs, "_warn", lambda parent, title, exc: warned.append(title))
    monkeypatch.setattr(money_dialogs.MorningInvoiceDialog, "exec",
                        lambda dialog: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog = money_dialogs.JobAccountDialog("J-2026-0010", default_store())
    assert dialog.issue_in_morning() is None
    assert warned == ["לא ברור אם המסמך הופק"]
    job = default_store().load("J-2026-0010")
    assert job.document_pending and not job.invoices
    found = [i for i in scan(date.today()) if i.key.startswith("pending-doc:")]
    assert found and found[0].severity == "danger"

    # Asked again: first "did you check in Morning?" — "no" stops there.
    sent = len(fake.seen)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    assert dialog.issue_in_morning() is None and len(fake.seen) == sent
    dialog.deleteLater()
    fake.close()


def test_a_connection_refused_is_certainly_nothing_issued():
    import socket
    import urllib.error

    from profileos.erp.morning import _never_sent

    assert _never_sent(urllib.error.URLError(ConnectionRefusedError()))
    assert _never_sent(urllib.error.URLError(socket.gaierror()))
    assert not _never_sent(TimeoutError())
    assert not _never_sent(urllib.error.URLError(TimeoutError()))
