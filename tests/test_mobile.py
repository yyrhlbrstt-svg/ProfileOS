"""Mobile access tests.

A phone is the device most likely to be lost, so the tests are mostly about
what a phone cannot do: pair itself, keep working after being revoked, reuse a
code, or reach a part of the system it was not paired for. The rest checks that
a measurement taken on site arrives in the office exactly once and unaltered.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from profileos.mobile.measure import MeasurementStore, SiteMeasurement
from profileos.mobile.pairing import DeviceRegistry, PairingError
from profileos.mobile.state import configure


@pytest.fixture
def registry(tmp_path) -> DeviceRegistry:
    return DeviceRegistry.load(tmp_path / "devices.json")


@pytest.fixture
def state(tmp_path):
    return configure(
        registry_path=tmp_path / "devices.json",
        measurement_path=tmp_path / "measurements.json",
        station="משרד",
        system_id="klil-7300",
    )


@pytest.fixture
def client(state):
    from fastapi.testclient import TestClient

    from profileos.api.server import app

    return TestClient(app)


def paired(client, state, *, name="הטלפון של דאדי", scopes=("jobs", "measure", "drawings")):
    code = state.registry.issue_code(name, scopes=scopes)
    response = client.post("/m/api/pair", json={"code": code.code})
    assert response.status_code == 200
    data = response.json()
    return {"X-Device-Id": data["device_id"], "X-Device-Token": data["token"]}


# --------------------------------------------------------------------------- #
class TestPairing:
    def test_a_code_works_once(self, registry):
        code = registry.issue_code("phone")
        registry.redeem(code.code)
        with pytest.raises(PairingError):
            registry.redeem(code.code)

    def test_a_code_expires(self, registry, monkeypatch):
        code = registry.issue_code("phone")
        code.expires_at -= timedelta(minutes=10)
        with pytest.raises(PairingError):
            registry.redeem(code.code)

    def test_wrong_expired_and_used_all_say_the_same_thing(self, registry):
        """Telling a phone which of the three it hit teaches it something."""
        used = registry.issue_code("a")
        registry.redeem(used.code)
        expired = registry.issue_code("b")
        expired.expires_at -= timedelta(minutes=10)

        messages = set()
        for code in (used.code, expired.code, "000000"):
            with pytest.raises(PairingError) as excinfo:
                registry.redeem(code)
            messages.add(str(excinfo.value))
        assert len(messages) == 1

    def test_the_token_is_never_stored_in_the_clear(self, registry, tmp_path):
        code = registry.issue_code("phone")
        _, token = registry.redeem(code.code)
        assert token not in (tmp_path / "devices.json").read_text(encoding="utf-8")

    def test_a_device_needs_a_name_so_it_can_be_revoked_by_one(self, registry):
        with pytest.raises(PairingError, match="name"):
            registry.issue_code("   ")

    def test_a_revoked_device_stops_working(self, registry):
        code = registry.issue_code("phone")
        device, token = registry.redeem(code.code)
        assert registry.authenticate(device.device_id, token)
        registry.revoke(device.device_id)
        assert registry.authenticate(device.device_id, token) is None

    def test_revoking_one_device_leaves_the_others_alone(self, registry):
        first, first_token = registry.redeem(registry.issue_code("a").code)
        second, second_token = registry.redeem(registry.issue_code("b").code)
        registry.revoke(first.device_id)
        assert registry.authenticate(second.device_id, second_token)

    def test_a_device_unseen_for_too_long_lapses(self, registry):
        device, token = registry.redeem(registry.issue_code("phone").code)
        device.last_seen -= timedelta(days=120)
        assert registry.authenticate(device.device_id, token) is None

    def test_devices_survive_a_restart(self, registry, tmp_path):
        device, token = registry.redeem(registry.issue_code("phone").code)
        reopened = DeviceRegistry.load(tmp_path / "devices.json")
        assert reopened.authenticate(device.device_id, token)

    def test_a_pairing_code_is_not_persisted(self, registry, tmp_path):
        """Only a machine that is open right now can pair a phone."""
        code = registry.issue_code("phone")
        reopened = DeviceRegistry.load(tmp_path / "devices.json")
        assert code.code not in reopened.codes


class TestScopes:
    def test_a_measuring_phone_cannot_move_production(self, client, state):
        headers = paired(client, state, scopes=("measure",))
        assert client.post(
            "/m/api/scan", json={"payload": "X", "stage": "cut"}, headers=headers
        ).status_code == 403

    def test_a_floor_tablet_cannot_read_the_measurements(self, client, state):
        headers = paired(client, state, scopes=("jobs",))
        assert client.get("/m/api/measurements", headers=headers).status_code == 403

    def test_no_route_answers_without_a_token(self, client, state):
        for path in ("/m/api/jobs", "/m/api/measurements", "/m/api/elements"):
            assert client.get(path).status_code == 401

    def test_a_made_up_token_is_refused(self, client, state):
        headers = {"X-Device-Id": "deadbeef", "X-Device-Token": "nope"}
        assert client.get("/m/api/jobs", headers=headers).status_code == 401

    def test_the_page_itself_needs_nothing(self, client):
        """The pairing screen has to be reachable, or nothing can ever pair."""
        assert client.get("/m").status_code == 200


class TestFloor:
    def _work_order(self, state):
        from profileos.elements.builder import ElementBuilder
        from profileos.elements.model import Opening
        from profileos.mes.tracking import work_order_from_builds

        build = ElementBuilder.for_system("klil-7300").build(
            Opening(element_id="W-01", name="W-01", width=1200.0, height=1400.0)
        )
        order = work_order_from_builds([build], project_id="P1", name="בית פרטי")
        state.set_work_order(order)
        state.set_builds([build])
        return order

    def test_the_stages_a_phone_may_set_stop_short_of_shipping(self, client, state):
        from profileos.mobile.app import FLOOR_STAGES

        assert "shipped" not in FLOOR_STAGES and "scrapped" not in FLOOR_STAGES

    def test_shipping_from_a_phone_is_refused(self, client, state):
        order = self._work_order(state)
        headers = paired(client, state)
        item = next(iter(order))
        response = client.post(
            "/m/api/scan", json={"payload": item.item_id, "stage": "shipped"}, headers=headers
        )
        assert response.status_code == 403

    def test_a_scan_moves_the_item_and_records_who_did_it(self, client, state):
        order = self._work_order(state)
        headers = paired(client, state, name="דאדי")
        item = next(iter(order))
        response = client.post(
            "/m/api/scan", json={"payload": item.item_id, "stage": "cut"}, headers=headers
        )
        assert response.status_code == 200
        assert order.find(item.item_id).stage.value == "cut"
        assert order.find(item.item_id).history[-1].operator == "דאדי"

    def test_an_impossible_transition_gives_the_trackers_own_reason(self, client, state):
        order = self._work_order(state)
        headers = paired(client, state)
        item = next(iter(order))
        response = client.post(
            "/m/api/scan", json={"payload": item.item_id, "stage": "glazed"}, headers=headers
        )
        assert response.status_code == 400
        assert response.json()["detail"]

    def test_with_no_work_order_the_phone_is_told_so(self, client, state):
        headers = paired(client, state)
        assert client.get("/m/api/jobs", headers=headers).json()["total"] == 0


class TestMeasuring:
    def test_a_measurement_reaches_the_office_store(self, client, state):
        headers = paired(client, state)
        response = client.post(
            "/m/api/measurements",
            json={"reference": "w-01", "widths": [1210, 1205, 1198],
                  "heights": [1400, 1402, 1401]},
            headers=headers,
        )
        assert response.status_code == 200
        assert state.measurements.latest("W-01").width == 1198

    def test_the_narrowest_width_is_the_one_to_build_to(self):
        """The frame has to fit the tightest point, not the average."""
        record = SiteMeasurement(reference="W", widths=(1210, 1205, 1198),
                                 heights=(1400, 1400, 1400))
        assert record.width == 1198

    def test_a_tapering_opening_is_reported_rather_than_averaged(self, client, state):
        headers = paired(client, state)
        response = client.post(
            "/m/api/measurements",
            json={"reference": "W-02", "widths": [1210, 1200, 1180],
                  "heights": [1400, 1400, 1400]},
            headers=headers,
        )
        assert response.json()["problems"]

    def test_out_of_square_is_caught_by_the_diagonals(self):
        record = SiteMeasurement(
            reference="W", widths=(1200, 1200, 1200), heights=(1400, 1400, 1400),
            diagonals=(1840.0, 1866.0),
        )
        assert record.diagonal_difference == pytest.approx(26.0)
        assert any("square" in problem for problem in record.problems())

    def test_a_measurement_without_sizes_is_refused(self, client, state):
        headers = paired(client, state)
        assert client.post(
            "/m/api/measurements", json={"reference": "W-03", "widths": [], "heights": []},
            headers=headers,
        ).status_code == 422

    def test_re_measuring_keeps_the_earlier_figure(self, tmp_path):
        """Knowing a size changed is the point of writing it down."""
        store = MeasurementStore.load(tmp_path / "m.json")
        store.add(SiteMeasurement(reference="W", widths=(1200, 1200, 1200),
                                  heights=(1400, 1400, 1400)))
        store.add(SiteMeasurement(reference="W", widths=(1180, 1180, 1180),
                                  heights=(1400, 1400, 1400)))
        assert len(store.history("W")) == 2
        assert store.changed() == [("W", -20.0, 0.0)]

    def test_the_element_size_is_derived_not_stored(self):
        """So the installation clearance can change without re-measuring."""
        record = SiteMeasurement(reference="W", widths=(1200, 1200, 1200),
                                 heights=(1400, 1400, 1400))
        assert record.element_size(clearance=10.0) == (1180.0, 1380.0)
        assert record.element_size(clearance=15.0) == (1170.0, 1370.0)

    def test_the_measurement_records_which_device_took_it(self, client, state):
        headers = paired(client, state, name="הטלפון של דאדי")
        client.post(
            "/m/api/measurements",
            json={"reference": "W-09", "widths": [1000, 1000, 1000],
                  "heights": [1000, 1000, 1000]},
            headers=headers,
        )
        assert state.measurements.latest("W-09").measured_by == "הטלפון של דאדי"


class TestOnSiteCheck:
    def test_an_opening_that_cannot_be_made_says_so_in_hebrew(self, client, state):
        headers = paired(client, state)
        response = client.post(
            "/m/api/check",
            json={"width": 1600, "height": 2600, "opening_type": "casement",
                  "sill_height": 900},
            headers=headers,
        )
        data = response.json()
        assert not data["can_be_made"]
        assert data["verdict"].startswith("לא ניתן לייצור")
        assert all(finding["what"] for finding in data["findings"])

    def test_the_hebrew_line_is_hebrew_all_the_way_through(self, client, state):
        """A fitter holding a phone should not have to read English mid-sentence."""
        headers = paired(client, state)
        data = client.post(
            "/m/api/check",
            json={"width": 1600, "height": 2600, "opening_type": "fixed", "sill_height": 0},
            headers=headers,
        ).json()
        blocker = next(f for f in data["findings"] if f["tone"] == "bad")
        assert "exceeds" not in blocker["what"]

    def test_a_sensible_opening_passes(self, client, state):
        headers = paired(client, state)
        data = client.post(
            "/m/api/check",
            json={"width": 900, "height": 1100, "opening_type": "fixed", "sill_height": 1100},
            headers=headers,
        ).json()
        assert data["can_be_made"]

    def test_a_nonsense_size_is_refused_rather_than_crashed_on(self, client, state):
        headers = paired(client, state)
        assert client.post(
            "/m/api/check", json={"width": 0, "height": 1400}, headers=headers
        ).status_code == 422


class TestDrawings:
    def test_an_elevation_can_be_read_on_the_phone(self, client, state):
        from profileos.elements.builder import ElementBuilder
        from profileos.elements.model import Opening

        build = ElementBuilder.for_system("klil-7300").build(
            Opening(element_id="W-01", name="W-01", width=1200.0, height=1400.0)
        )
        state.set_builds([build])
        headers = paired(client, state)

        listing = client.get("/m/api/elements", headers=headers).json()
        assert listing["elements"]
        response = client.get("/m/api/elements/W-01/elevation.svg", headers=headers)
        assert response.status_code == 200
        assert response.text.startswith("<svg")
        assert "1200" in response.text

    def test_an_element_that_is_not_loaded_is_a_404_not_a_crash(self, client, state):
        headers = paired(client, state)
        assert client.get("/m/api/elements/NOPE/elevation.svg", headers=headers).status_code == 404
