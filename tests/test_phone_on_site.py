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
