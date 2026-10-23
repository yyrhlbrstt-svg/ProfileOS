"""On site with the phone: a photo goes into the job's files, and the customer
signs the handover — with what is left to fix written down at that moment."""

from __future__ import annotations

import base64
import io
import os
import socket
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")


@pytest.fixture
def office(tmp_path, monkeypatch):
    from profileos.core.config import reload_settings
    from profileos.mobile.state import configure
    from profileos.projects import JobFile, JobStatus, default_store

    monkeypatch.setenv("PROFILEOS_DATA_DIR", str(tmp_path / "data"))
    reload_settings()
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    state = configure(registry_path=tmp_path / "data" / "devices.json",
                      measurement_path=tmp_path / "data" / "measurements.json")
    default_store().save(JobFile(job_id="J-2026-0005", name="בית כהן", customer_name="כהן",
                                 status=JobStatus.IN_PRODUCTION))
    yield state
    monkeypatch.delenv("PROFILEOS_DATA_DIR", raising=False)
    reload_settings()


def _png(width: int = 300, height: int = 120) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (width, height), "white")
    ImageDraw.Draw(image).line((10, 60, 290, 40), fill="black", width=4)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _headers(client, state):
    code = state.registry.issue_code("הטלפון של יוסי", scopes=("measure",))
    paired = client.post("/m/api/pair", json={"code": code.code}).json()
    return {"X-Device-Id": paired["device_id"], "X-Device-Token": paired["token"]}


def test_a_photo_from_site_lands_in_the_jobs_files(office):
    from fastapi.testclient import TestClient

    from profileos.api.server import app
    from profileos.projects.attachments import AttachmentKind, attachments_for

    client = TestClient(app)
    headers = _headers(client, office)
    answer = client.post("/m/api/photos", headers=headers, data={
        "job_id": "J-2026-0005", "kind": "defect_photo", "element": "w3",
        "caption": "שריטה במסגרת"}, files={"photo": ("site.jpg", _png(), "image/jpeg")})
    assert answer.status_code == 200, answer.text
    [photo] = attachments_for("J-2026-0005").photos()
    assert photo.kind is AttachmentKind.DEFECT_PHOTO and photo.element == "W3"
    assert photo.added_by == "הטלפון של יוסי" and photo.caption == "שריטה במסגרת"

    refused = client.post("/m/api/photos", headers=headers, data={"job_id": "J-NOPE"},
                          files={"photo": ("a.jpg", _png(), "image/jpeg")})
    assert refused.status_code == 404
    without = client.post("/m/api/photos", data={"job_id": "J-2026-0005"},
                          files={"photo": ("a.jpg", _png(), "image/jpeg")})
    assert without.status_code == 401


def test_the_customer_signs_the_handover_on_the_phone(office):
    from fastapi.testclient import TestClient

    from profileos.api.server import app
    from profileos.projects import JobStatus, default_store
    from profileos.projects.attachments import AttachmentKind, attachments_for

    client = TestClient(app)
    headers = _headers(client, office)
    signature = "data:image/png;base64," + base64.b64encode(_png()).decode()
    unsigned = client.post("/m/api/handover", headers=headers, json={
        "job_id": "J-2026-0005", "signed_by": "משה כהן", "signature": "data:image/png;base64,"})
    assert unsigned.status_code == 422
    answer = client.post("/m/api/handover", headers=headers, json={
        "job_id": "J-2026-0005", "signed_by": "משה כהן", "signature": signature,
        "snags": ["ידית בחדר שינה נתקעת", "", "שריטה ב־W3"]})
    assert answer.status_code == 200, answer.text
    assert answer.json()["installed"] is True
    job = default_store().load("J-2026-0005")
    assert job.status is JobStatus.INSTALLED
    assert [s.description for s in job.open_snags] == ["ידית בחדר שינה נתקעת", "שריטה ב־W3"]
    [signed] = attachments_for("J-2026-0005").of_kind(AttachmentKind.SIGNED_DELIVERY)
    assert "משה כהן" in signed.caption and "⁦2⁩ תיקונים" in signed.caption


def test_a_handover_sent_again_is_kept_once_and_an_unwon_job_is_not_handed_over(office):
    from fastapi.testclient import TestClient

    from profileos.api.server import app
    from profileos.projects import JobFile, JobStatus, default_store
    from profileos.projects.attachments import AttachmentKind, attachments_for

    client = TestClient(app)
    headers = _headers(client, office)
    signature = "data:image/png;base64," + base64.b64encode(_png()).decode()
    body = {"job_id": "J-2026-0005", "signed_by": "משה כהן", "signature": signature,
            "snags": ["ידית נתקעת"], "client_id": "h-1"}
    assert client.post("/m/api/handover", headers=headers, json=body).status_code == 200
    again = client.post("/m/api/handover", headers=headers, json=body)
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert len(attachments_for("J-2026-0005").of_kind(AttachmentKind.SIGNED_DELIVERY)) == 1
    assert len(default_store().load("J-2026-0005").snags) == 1

    default_store().save(JobFile(job_id="J-2026-0009", name="פנייה", status=JobStatus.ENQUIRY))
    early = client.post("/m/api/handover", headers=headers, json={
        **body, "job_id": "J-2026-0009", "client_id": "h-2"})
    assert early.status_code == 409 and "לא אושר" in early.json()["detail"]
    assert default_store().load("J-2026-0009").status is JobStatus.ENQUIRY


def test_a_measurement_for_a_project_the_office_does_not_have_is_refused(office):
    from fastapi.testclient import TestClient

    from profileos.api.server import app

    client = TestClient(app)
    headers = _headers(client, office)
    answer = client.post("/m/api/measurements", headers=headers, json={
        "reference": "W1", "widths": [1200, 1200, 1200], "heights": [1400, 1400, 1400],
        "project_id": "J-NOPE"})
    assert answer.status_code == 422 and "לא נמצא" in answer.json()["detail"]


def test_only_a_photo_is_taken_as_a_photo(office):
    from fastapi.testclient import TestClient

    from profileos.api.server import app

    client = TestClient(app)
    headers = _headers(client, office)
    answer = client.post("/m/api/photos", headers=headers, data={"job_id": "J-2026-0005"},
                         files={"photo": ("notes.txt", b"hello" * 100, "text/plain")})
    assert answer.status_code == 422


CHROMIUM = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
_executables = sorted(CHROMIUM.glob("chromium-*/chrome-linux/chrome"))


@pytest.mark.skipif(not _executables, reason="no Chromium for Playwright here")
def test_photo_and_signature_from_the_phone_page(office, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    import uvicorn

    from profileos.api.server import app
    from profileos.projects import JobStatus, default_store
    from profileos.projects.attachments import attachments_for

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    photo = tmp_path / "frame.png"
    photo.write_bytes(_png(1600, 1200))
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(_executables[-1]))
            page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
            page.goto(f"http://127.0.0.1:{port}/m?lang=he")
            code = office.registry.issue_code("המתקין", scopes=("measure",)).code
            page.evaluate("""code => fetch('/m/api/pair', {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code})})
                .then(r => r.json()).then(d => {
                  localStorage.setItem('pos.token', d.token);
                  localStorage.setItem('pos.device', d.device_id);
                  localStorage.setItem('pos.scopes', 'measure');
                })""", code)
            page.reload()
            page.click("nav button[data-tab=site]")
            page.wait_for_selector("#sjob option[value='J-2026-0005']", state="attached")
            page.select_option("#sjob", "J-2026-0005")
            page.set_input_files("#sfile", str(photo))
            page.wait_for_selector("#s-msg.ok", timeout=10_000)

            page.fill("#hby", "משה כהן")
            page.fill("#hsnags", "ידית נתקעת")
            page.locator("#hsign").scroll_into_view_if_needed()
            box = page.locator("#hsign").bounding_box()
            page.mouse.move(box["x"] + 20, box["y"] + 90)
            page.mouse.down()
            for step in range(1, 12):
                page.mouse.move(box["x"] + 20 + step * 25, box["y"] + 90 - (step % 3) * 20)
            page.mouse.up()
            page.click("button[onclick='handOver()']")
            page.wait_for_selector("#h-msg.ok", timeout=10_000)
            page.screenshot(path=os.environ.get("PHONE_SHOT", str(tmp_path / "site.png")), full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    files = attachments_for("J-2026-0005")
    assert len(files.photos()) == 2           # the photo and the signature
    from PIL import Image

    taken = next(f for f in files.photos() if f.kind.value == "progress_photo")
    with Image.open(files.path_of(taken)) as image:
        assert max(image.size) <= 2000        # shrunk on the phone
    assert default_store().load("J-2026-0005").status is JobStatus.INSTALLED


def test_the_same_photo_sent_twice_is_kept_once(office):
    from fastapi.testclient import TestClient

    from profileos.api.server import app
    from profileos.projects.attachments import attachments_for

    client = TestClient(app)
    headers = _headers(client, office)
    for _ in range(2):
        answer = client.post("/m/api/photos", headers=headers, data={
            "job_id": "J-2026-0005", "client_id": "ph-123"},
            files={"photo": ("site.jpg", _png(), "image/jpeg")})
        assert answer.status_code == 200
    assert answer.json().get("duplicate") is True
    assert len(attachments_for("J-2026-0005").photos()) == 1


@pytest.mark.skipif(not _executables, reason="no Chromium for Playwright here")
def test_a_photo_taken_without_signal_goes_by_itself_later(office, tmp_path):
    playwright = pytest.importorskip("playwright.sync_api")
    import uvicorn

    from profileos.api.server import app
    from profileos.projects.attachments import attachments_for

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    photo = tmp_path / "frame.png"
    photo.write_bytes(_png(800, 600))
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(_executables[-1]))
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{port}/m?lang=he")
            code = office.registry.issue_code("המתקין", scopes=("measure",)).code
            page.evaluate("""code => fetch('/m/api/pair', {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code})})
                .then(r => r.json()).then(d => {
                  localStorage.setItem('pos.token', d.token);
                  localStorage.setItem('pos.device', d.device_id);
                  localStorage.setItem('pos.scopes', 'measure');
                })""", code)
            page.reload()
            page.click("nav button[data-tab=site]")
            page.wait_for_selector("#sjob option[value='J-2026-0005']", state="attached")
            page.select_option("#sjob", "J-2026-0005")

            context.set_offline(True)
            page.set_input_files("#sfile", str(photo))
            page.wait_for_function(
                "document.getElementById('s-wait').textContent.includes('1')", timeout=10_000)
            assert "נשמר בטלפון" in page.inner_text("#s-msg")
            assert not attachments_for("J-2026-0005").photos()

            context.set_offline(False)
            page.evaluate("window.dispatchEvent(new Event('online'))")
            page.wait_for_function(
                "document.getElementById('s-wait').textContent === ''", timeout=10_000)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert len(attachments_for("J-2026-0005").photos()) == 1


def _quoted_job(job_id="J-2026-0020"):
    from profileos.projects import JobFile, JobStatus, default_store
    from profileos.quoting.revisions import Line, RevisionBook

    default_store().save(JobFile(job_id=job_id, name="מטבח לוי", customer_name="לוי",
                                 status=JobStatus.QUOTED, quote_total=8_000))
    book = RevisionBook().load()
    book.for_job(job_id).record_issued(
        [Line("W1", "W1 — חלון הזזה", 2, "pc", 4_000.0, "element")], 8_000.0)
    book.save()
    return job_id


def test_the_customer_approves_the_sent_version_and_the_job_is_won(office):
    from fastapi.testclient import TestClient

    from profileos.api.server import app
    from profileos.projects import JobStatus, default_store
    from profileos.projects.attachments import AttachmentKind, attachments_for

    job_id = _quoted_job()
    client = TestClient(app)
    headers = _headers(client, office)
    shown = client.get(f"/m/api/quote?job_id={job_id}", headers=headers).json()
    assert shown["revision"] == 1 and shown["net"] == 8_000 and not shown["won"]
    assert shown["lines"][0]["description"] == "W1 — חלון הזזה"

    signature = "data:image/png;base64," + base64.b64encode(_png()).decode()
    stale = client.post("/m/api/accept", headers=headers, json={
        "job_id": job_id, "revision": 7, "signed_by": "לוי", "signature": signature})
    assert stale.status_code == 409 and "גרסה חדשה" in stale.json()["detail"]
    answer = client.post("/m/api/accept", headers=headers, json={
        "job_id": job_id, "revision": 1, "signed_by": "דוד לוי", "signature": signature})
    assert answer.status_code == 200, answer.text
    job = default_store().load(job_id)
    assert job.status is JobStatus.WON and job.accepted_revision == 1
    assert [need.need for need in job.waiting_for] == ["measurement", "colour", "glass_choice"]
    signed = attachments_for(job_id).of_kind(AttachmentKind.SIGNED_QUOTE)
    assert len(signed) == 2
    record = next(f for f in signed if f.name.endswith(".txt"))
    text = attachments_for(job_id).path_of(record).read_text(encoding="utf-8")
    assert "דוד לוי" in text and "8,000.00" in text
    again = client.post("/m/api/accept", headers=headers, json={
        "job_id": job_id, "revision": 1, "signed_by": "דוד לוי", "signature": signature})
    assert again.status_code == 409                              # once is enough


@pytest.mark.skipif(not _executables, reason="no Chromium for Playwright here")
def test_approved_and_signed_at_the_customers_table(office):
    playwright = pytest.importorskip("playwright.sync_api")
    import uvicorn

    from profileos.api.server import app
    from profileos.projects import JobStatus, default_store

    job_id = _quoted_job()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(_executables[-1]))
            page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
            page.goto(f"http://127.0.0.1:{port}/m?lang=he")
            code = office.registry.issue_code("המוכר", scopes=("measure",)).code
            page.evaluate("""code => fetch('/m/api/pair', {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code})})
                .then(r => r.json()).then(d => {
                  localStorage.setItem('pos.token', d.token);
                  localStorage.setItem('pos.device', d.device_id);
                  localStorage.setItem('pos.scopes', 'measure');
                })""", code)
            page.reload()
            page.click("nav button[data-tab=site]")
            page.wait_for_selector(f"#sjob option[value='{job_id}']", state="attached")
            page.select_option("#sjob", job_id)
            page.click("button[onclick='showQuote()']")
            page.wait_for_selector("#q-sign", state="visible")
            assert "חלון הזזה" in page.inner_text("#q-view")
            page.fill("#qby", "דוד לוי")
            page.locator("#qsign").scroll_into_view_if_needed()
            box = page.locator("#qsign").bounding_box()
            page.mouse.move(box["x"] + 20, box["y"] + 90)
            page.mouse.down()
            for step in range(1, 12):
                page.mouse.move(box["x"] + 20 + step * 25, box["y"] + 90 - (step % 3) * 20)
            page.mouse.up()
            page.click("#q-accept")
            try:
                page.wait_for_selector("#q-msg.ok", timeout=10_000)
            except Exception:
                raise AssertionError(page.inner_text("#q-msg"))
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert default_store().load(job_id).status is JobStatus.WON


def test_a_piece_number_in_two_jobs_is_not_moved_on_a_guess(office):
    from fastapi.testclient import TestClient

    from profileos.api.server import app
    from profileos.mes import ItemKind, ProductionItem, WorkOrder
    from profileos.mes.store import load_work_order, save_work_order
    from profileos.projects import JobFile, JobStatus, default_store

    default_store().save(JobFile(job_id="J-2026-0006", name="בית לוי",
                                 status=JobStatus.IN_PRODUCTION))
    for job_id in ("J-2026-0005", "J-2026-0006"):
        order = WorkOrder(work_order_id=f"WO-{job_id}", project_id=job_id, name=job_id)
        order.add(ProductionItem(item_id="W1-P011", kind=ItemKind.PROFILE_PIECE,
                                 description="קורה", project_id=job_id,
                                 barcode=f"POS|{job_id}|W1|W1-P011|CUT"))
        save_work_order(order, job_id)
    client = TestClient(app)
    code = office.registry.issue_code("מסור", scopes=("jobs",))
    paired = client.post("/m/api/pair", json={"code": code.code}).json()
    headers = {"X-Device-Id": paired["device_id"], "X-Device-Token": paired["token"]}

    bare = client.post("/m/api/scan", headers=headers, json={"payload": "W1-P011", "stage": "cut"})
    assert bare.status_code == 409 and "המדבקה" in bare.json()["detail"]
    assert load_work_order("J-2026-0005").find("W1-P011").stage.value == "planned"

    label = client.post("/m/api/scan", headers=headers,
                        json={"payload": "POS|J-2026-0006|W1|W1-P011|CUT", "stage": "cut"})
    assert label.status_code == 200, label.text
    assert load_work_order("J-2026-0006").find("W1-P011").stage.value == "cut"
    assert load_work_order("J-2026-0005").find("W1-P011").stage.value == "planned"
