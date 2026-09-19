from __future__ import annotations

import os
from pathlib import Path
import tempfile


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="recognition-trend-test-"))
os.environ["RECOGNITION_V2_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["RECOGNITION_ENABLE_TEST_ACCOUNTS"] = "1"
os.environ["RECOGNITION_TEST_DEFAULT_PASSWORD"] = "1234"
os.environ["RECOGNITION_TEST_ADMIN_PASSWORD"] = "HR123"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.routers.statistics import TREND_MAX_MONTHS, TREND_SCORE_FIELDS, trend_month_keys  # noqa: E402


ANCHOR = "2026-09"


def login(client: TestClient, employee_no: str, password: str = "1234") -> None:
    response = client.post("/api/login", json={"employee_no": employee_no, "password": password})
    assert response.status_code == 200, response.text


def test_trend_month_keys_walk_back_across_the_year_boundary() -> None:
    assert trend_month_keys("2026-02", 4) == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert trend_month_keys("2026-09", 1) == ["2026-09"]
    assert trend_month_keys("2026-01", 2) == ["2025-12", "2026-01"]


def test_trend_totals_equal_single_month_statistics() -> None:
    """The trend must reuse statistics_payload, never re-derive the scoring rules.

    Every month in the series has to match what /api/statistics reports for that
    same month, otherwise the two views would disagree on the same data.
    """
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        response = client.get("/api/statistics/trend", params={"month": ANCHOR, "months": 3})
        assert response.status_code == 200, response.text
        data = response.json()

        assert data["months"] == trend_month_keys(ANCHOR, 3)
        assert [row["month"] for row in data["overall"]] == data["months"]

        for row in data["overall"]:
            single = client.get("/api/statistics", params={"month": row["month"]})
            assert single.status_code == 200, single.text
            summary = single.json()["summary"]
            assert row["employee_count"] == summary["employee_count"], row["month"]
            for field in TREND_SCORE_FIELDS:
                assert row[field] == summary[field], f'{row["month"]} {field}'


def test_trend_pads_every_circle_to_the_full_month_range() -> None:
    """A circle with no data in some month still needs an aligned point."""
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        data = client.get("/api/statistics/trend", params={"month": ANCHOR, "months": 4}).json()
        for circle in data["by_attraction"]:
            assert [point["month"] for point in circle["series"]] == data["months"]
            assert all(field in point for point in circle["series"] for field in TREND_SCORE_FIELDS)


def test_trend_span_is_capped_and_month_format_is_validated() -> None:
    with TestClient(app) as client:
        login(client, "GSMTEST01")
        capped = client.get("/api/statistics/trend", params={"month": ANCHOR, "months": 99})
        assert capped.status_code == 200, capped.text
        assert len(capped.json()["months"]) == TREND_MAX_MONTHS

        single = client.get("/api/statistics/trend", params={"month": ANCHOR, "months": 0})
        assert single.status_code == 200
        assert len(single.json()["months"]) == 1

        assert client.get("/api/statistics/trend", params={"month": "2026-9"}).status_code == 400


def test_trend_requires_the_same_permission_as_statistics() -> None:
    with TestClient(app) as client:
        login(client, "CMTEST01")
        assert client.get("/api/statistics/trend", params={"month": ANCHOR}).status_code == 403
