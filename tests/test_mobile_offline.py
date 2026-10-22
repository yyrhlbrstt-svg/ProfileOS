"""The phone page in a real browser: a measurement taken without signal
waits on the phone and reaches the office by itself when the network is back.

Runs where Playwright and a Chromium are installed (the development
container); elsewhere it is skipped, and the server half is covered by
``test_mobile.py``.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest

CHROMIUM = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
_executables = sorted(CHROMIUM.glob("chromium-*/chrome-linux/chrome"))

playwright = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(not _executables, reason="no Chromium for Playwright here")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def office(tmp_path):
    import uvicorn

    from profileos.api.server import app
    from profileos.mobile.state import configure

    state = configure(registry_path=tmp_path / "devices.json",
                      measurement_path=tmp_path / "measurements.json",
                      station="משרד", system_id="klil-7300")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield state, f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def _until(page, done, tries: int = 100) -> None:
    for _ in range(tries):
        if done():
            return
        page.wait_for_timeout(100)


def test_measuring_without_signal(office):
    state, base = office
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(_executables[-1]))
        try:
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            page.goto(base + "/m")
            code = state.registry.issue_code("המודד", scopes=("measure",)).code
            page.evaluate("""code => fetch('/m/api/pair', {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code})})
                .then(r => r.json()).then(d => {
                  localStorage.setItem('pos.token', d.token);
                  localStorage.setItem('pos.device', d.device_id);
                  localStorage.setItem('pos.scopes', 'measure');
                })""", code)
            page.reload()
            page.wait_for_selector("#s-measure.on")

            context.set_offline(True)                     # the stairwell
            for ref, width in (("W-01", "1210"), ("W-02", "905")):
                page.fill("#mref", ref)
                for i in "123":
                    page.fill(f"#w{i}", width)
                    page.fill(f"#h{i}", "1400")
                page.click("#s-measure button")
                page.wait_for_timeout(200)
            assert page.evaluate("JSON.parse(localStorage.getItem('pos.outbox')).length") == 2
            assert state.measurements.records == []
            assert "⁦2⁩" in page.inner_text("#m-outbox")
            assert page.input_value("#w1") == ""          # ready for the next opening

            # Back in the van with the page still open: sent by themselves.
            context.set_offline(False)
            page.evaluate("window.dispatchEvent(new Event('online'))")
            _until(page, lambda: len(state.measurements.records) == 2)
            assert sorted(r.reference for r in state.measurements.records) == ["W-01", "W-02"]
            assert page.evaluate("JSON.parse(localStorage.getItem('pos.outbox') || '[]').length") == 0
            assert "\u20662\u2069" in page.inner_text("#m-msg")

            # Sent again (an answer lost on the way): still one each.
            page.evaluate("window.dispatchEvent(new Event('online'))")
            page.wait_for_timeout(300)
            assert len(state.measurements.records) == 2

            # Measured offline, the page closed, opened again at the office:
            # what waited on the phone goes as the page opens.
            context.set_offline(True)
            page.fill("#mref", "W-03")
            for i in "123":
                page.fill(f"#w{i}", "800")
                page.fill(f"#h{i}", "1200")
            page.click("#s-measure button")
            page.wait_for_timeout(200)
            page.close()
            context.set_offline(False)
            again = context.new_page()
            again.goto(base + "/m")
            _until(again, lambda: len(state.measurements.records) == 3)
            assert state.measurements.latest("W-03") is not None
        finally:
            browser.close()


def test_what_the_office_would_refuse_is_said_on_the_spot(office):
    state, base = office
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(_executables[-1]))
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(base + "/m")
            code = state.registry.issue_code("המודד", scopes=("measure",)).code
            page.evaluate("""code => fetch('/m/api/pair', {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code})})
                .then(r => r.json()).then(d => {
                  localStorage.setItem('pos.token', d.token);
                  localStorage.setItem('pos.device', d.device_id);
                  localStorage.setItem('pos.scopes', 'measure');
                })""", code)
            page.reload()
            page.wait_for_selector("#s-measure.on")
            context.set_offline(True)
            page.fill("#mref", "W-09")                     # no sizes
            page.click("#s-measure button")
            page.wait_for_timeout(200)
            assert page.evaluate("localStorage.getItem('pos.outbox')") in (None, "[]")
            assert "רוחב" in page.inner_text("#m-msg") or "width" in page.inner_text("#m-msg").lower()
        finally:
            browser.close()
