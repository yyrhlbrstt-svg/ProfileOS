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
            _until(page, lambda: page.evaluate(
                "JSON.parse(localStorage.getItem('pos.outbox') || '[]').length") == 0)
            assert page.evaluate("JSON.parse(localStorage.getItem('pos.outbox') || '[]').length") == 0
            assert "\u20662\u2069" in page.inner_text("#m-msg")

            # Sent again (the office's answer was lost on the way, so the phone
            # still holds it): kept once.
            sent = state.measurements.latest("W-01")
            page.evaluate("""id => localStorage.setItem('pos.outbox', JSON.stringify([{body: {
                reference: 'W-01', widths: [1210, 1210, 1210], heights: [1400, 1400, 1400],
                diagonals: null, note: '', client_id: id, measured_at: new Date().toISOString()}}]))""",
                          sent.client_id)
            page.evaluate("flushOutbox()")
            _until(page, lambda: page.evaluate("localStorage.getItem('pos.outbox')") == "[]")
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


def _paired_page(context, state, base):
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
    return page


def test_a_measurement_queued_while_another_is_sending_is_not_lost(office):
    """Weak signal: the waiting W-A is on its way (slowly) when W-B is taken
    and cannot be sent. W-A arriving must not take W-B off the phone."""
    state, base = office
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(_executables[-1]))
        try:
            page = _paired_page(browser.new_context(), state, base)
            page.evaluate("""() => {
              const real = window.fetch;
              window.fetch = (url, opts) => {
                const body = (opts && opts.body) || "";
                if(body.includes('"W-A"')) return new Promise(ok => setTimeout(() => ok(real(url, opts)), 1500));
                if(body.includes('"W-B"')) return Promise.reject(new TypeError("no signal"));
                return real(url, opts);
              };
              localStorage.setItem('pos.outbox', JSON.stringify([{body: {reference: 'W-A',
                widths: [900, 900, 900], heights: [1200, 1200, 1200], diagonals: null, note: '',
                client_id: 'id-a', measured_at: new Date().toISOString()}}]));
            }""")
            page.evaluate("flushOutbox()")                 # W-A on its way, slowly
            page.fill("#mref", "W-B")
            for i in "123":
                page.fill(f"#w{i}", "800")
                page.fill(f"#h{i}", "1100")
            page.click("#s-measure button")               # no signal for W-B
            _until(page, lambda: state.measurements.latest("W-A") is not None, tries=50)
            page.wait_for_timeout(500)
            left = page.evaluate("JSON.parse(localStorage.getItem('pos.outbox')).map(i => i.body.reference)")
            assert left == ["W-B"]
            assert [r.reference for r in state.measurements.records] == ["W-A"]
        finally:
            browser.close()


def test_a_refused_measurement_can_be_sent_again_or_deleted(office):
    state, base = office
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(_executables[-1]))
        try:
            page = _paired_page(browser.new_context(), state, base)
            page.on("dialog", lambda dialog: dialog.accept())
            page.evaluate("""() => localStorage.setItem('pos.outbox', JSON.stringify([{body: {
                reference: 'W-X', widths: [0, 0, 0], heights: [0, 0, 0], diagonals: null,
                note: '', client_id: 'id-x', measured_at: new Date().toISOString()}}]))""")
            page.evaluate("flushOutbox()")
            _until(page, lambda: "refused" in page.evaluate("localStorage.getItem('pos.outbox')"))
            text = page.inner_text("#m-list")
            assert "W-X" in text and "[object" not in text
            page.click("#m-list button:has-text('מחיקה'), #m-list button:has-text('Delete')")
            assert page.evaluate("localStorage.getItem('pos.outbox')") == "[]"
            assert state.measurements.records == []
        finally:
            browser.close()


def test_an_unreadable_outbox_is_set_aside_not_overwritten(office):
    state, base = office
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(_executables[-1]))
        try:
            page = _paired_page(browser.new_context(), state, base)
            page.evaluate("localStorage.setItem('pos.outbox', '{broken')")
            assert page.evaluate("outbox()") == []
            kept = page.evaluate("""Object.keys(localStorage).filter(k => k.startsWith('pos.outbox.unreadable'))
                .map(k => localStorage.getItem(k))""")
            assert kept == ["{broken"]
        finally:
            browser.close()
