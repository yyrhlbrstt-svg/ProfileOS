"""Service API tests."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from profileos.api.server import app  # noqa: E402

SAMPLE = "data/samples/mullion_mb70.dxf"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


class TestService:
    def test_health(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert "components" in body

    def test_drivers_are_listed(self, client):
        keys = {d["key"] for d in client.get("/drivers").json()}
        assert "elumatec.ncx" in keys and "iso.gcode" in keys

    def test_openapi_schema_is_generated(self, client):
        assert client.get("/openapi.json").status_code == 200


class TestSectionEndpoint:
    def test_dxf_upload_is_analysed(self, client, mullion_dxf):
        with open(mullion_dxf, "rb") as handle:
            response = client.post(
                "/section/analyse?torsion=false",
                files={"file": ("mullion.dxf", handle, "application/dxf")},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["properties"]["area"] == pytest.approx(1719.2, abs=1.0)
        assert body["geometry"]["chambers"] == 4

    def test_garbage_upload_is_rejected(self, client):
        response = client.post(
            "/section/analyse", files={"file": ("bad.dxf", b"not a dxf", "application/dxf")}
        )
        assert response.status_code == 422


class TestElementEndpoint:
    def test_element_is_built(self, client):
        response = client.post(
            "/elements/build",
            json={
                "name": "W-04", "width": 2400, "height": 1800, "quantity": 2,
                "mullion_positions": [800, 1600],
                "sashes": [{"column": 1, "row": 0, "opening_type": "tilt_turn"}],
                "sill_height": 900,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["glass"]) == 3
        assert any(item["code"] == "HW-TT-KIT" for item in body["hardware"])

    def test_impossible_element_is_rejected(self, client):
        response = client.post("/elements/build", json={"name": "x", "width": 50, "height": 50})
        assert response.status_code == 422

    def test_division_outside_the_element_is_rejected(self, client):
        response = client.post(
            "/elements/build",
            json={"name": "x", "width": 1000, "height": 1000, "mullion_positions": [2000]},
        )
        assert response.status_code == 422


class TestNestingEndpoint:
    def test_optimises_a_cut_list(self, client):
        response = client.post(
            "/nesting/optimise",
            json={
                "project_name": "T",
                "items": [
                    {"profile_id": "P", "length": 2400, "quantity": 3},
                    {"profile_id": "P", "length": 1500, "quantity": 3},
                    {"profile_id": "P", "length": 1200, "quantity": 3},
                ],
                "stock_lengths": [6000], "kerf": 0.0,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["profiles"]["P"]["bars"] == 3
        assert len(body["layouts"]["P"]) == 3

    def test_oversized_piece_is_rejected(self, client):
        response = client.post(
            "/nesting/optimise",
            json={
                "project_name": "T",
                "items": [{"profile_id": "P", "length": 9000, "quantity": 1}],
                "stock_lengths": [6000],
            },
        )
        assert response.status_code == 422


class TestGlazingEndpoint:
    @pytest.mark.parametrize(
        "body,expected",
        [
            ({"panes": [6, 4], "cavities": [16], "coated_surfaces": []}, 2.60),
            ({"panes": [6, 4], "cavities": [16], "coated_surfaces": [3]}, 1.09),
            ({"panes": [4, 4, 4], "cavities": [14, 14], "coated_surfaces": [2, 5]}, 0.62),
        ],
    )
    def test_u_values(self, client, body, expected):
        response = client.post("/glazing/evaluate", json=body)
        assert response.json()["u_value"] == pytest.approx(expected, abs=0.05)

    def test_coating_on_an_exposed_face_does_not_help(self, client):
        """Surface 1 faces the weather, not a cavity, so it changes nothing."""
        plain = client.post(
            "/glazing/evaluate", json={"panes": [6, 4], "cavities": [16], "coated_surfaces": []}
        ).json()
        outer = client.post(
            "/glazing/evaluate", json={"panes": [6, 4], "cavities": [16], "coated_surfaces": [1]}
        ).json()
        assert outer["u_value"] == pytest.approx(plain["u_value"])

    def test_mismatched_cavity_count_is_rejected(self, client):
        response = client.post("/glazing/evaluate", json={"panes": [6, 4], "cavities": []})
        assert response.status_code == 422

    def test_nonexistent_surface_is_rejected(self, client):
        response = client.post(
            "/glazing/evaluate", json={"panes": [6, 4], "cavities": [16], "coated_surfaces": [9]}
        )
        assert response.status_code == 422


class TestPlumbingEndpoint:
    def test_sizes_a_run(self, client):
        response = client.post(
            "/plumbing/size",
            json={
                "flow_lps": 1.2, "length_m": 45, "height_gain_m": 12,
                "available_pressure_kpa": 250, "fittings": {"elbow_90_long": 8},
            },
        )
        body = response.json()
        assert body["ok"]
        assert body["velocity"] < 2.0

    def test_unknown_fitting_is_rejected(self, client):
        response = client.post(
            "/plumbing/size",
            json={"flow_lps": 1.0, "length_m": 10, "fittings": {"warp_drive": 1}},
        )
        assert response.status_code == 422

    def test_impossible_run_reports_reasons(self, client):
        response = client.post(
            "/plumbing/size",
            json={"flow_lps": 40.0, "length_m": 500, "available_pressure_kpa": 1.0},
        )
        body = response.json()
        assert not body["ok"] and body["reasons"]


class TestMesEndpoints:
    @pytest.fixture
    def work_order(self, client):
        response = client.post(
            "/mes/work-orders", json=[{"name": "W", "width": 2000, "height": 1500}]
        )
        return response.json()["work_order_id"]

    def test_work_order_is_created(self, client, work_order):
        body = client.get(f"/mes/work-orders/{work_order}").json()
        assert body["summary"]["items"] > 0
        assert all(item["stage"] == "planned" for item in body["items"])

    def test_valid_scan_advances(self, client, work_order):
        items = client.get(f"/mes/work-orders/{work_order}").json()["items"]
        response = client.post(
            f"/mes/work-orders/{work_order}/scan",
            json={"payload": items[0]["barcode"], "stage": "cut", "operator": "Dana"},
        )
        assert response.status_code == 200
        assert response.json()["ok"]

    def test_invalid_scan_returns_a_message_not_an_error(self, client, work_order):
        """A bad scan is a normal floor event; the tablet must show the reason."""
        items = client.get(f"/mes/work-orders/{work_order}").json()["items"]
        response = client.post(
            f"/mes/work-orders/{work_order}/scan",
            json={"payload": items[0]["barcode"], "stage": "shipped"},
        )
        assert response.status_code == 200
        assert not response.json()["ok"]
        assert "אי אפשר לעבור" in response.json()["message"]

    def test_unknown_work_order_is_404(self, client):
        assert client.get("/mes/work-orders/NOPE").status_code == 404

    def test_job_card_is_html(self, client, work_order):
        response = client.get(f"/mes/work-orders/{work_order}/job-card")
        assert response.status_code == 200
        assert response.text.startswith("<!doctype html>")

    def test_label_renders_svg(self, client):
        response = client.get("/mes/label/PC-101")
        assert response.status_code == 200 and response.text.startswith("<svg")

    def test_qr_label(self, client):
        response = client.get("/mes/label/PC-101?kind=qr")
        assert response.status_code in (200, 503)
