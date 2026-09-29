"""Probe reports must not claim coverage from missing or ambiguous evidence."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from app.providers.maps.base import route_result
from app.schemas.place import Coordinates, Place, ProviderRef

spec = importlib.util.spec_from_file_location("probe_maps", Path(__file__).parents[1] / "scripts/probe_maps.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def fake_settings(**kwargs):
    return SimpleNamespace(map_probe_max_calls=12, map_request_timeout_seconds=1,
                           amap_web_service_key=None, google_maps_api_key=None)


def test_missing_credential_report_is_blocked_without_requests(monkeypatch):
    monkeypatch.setattr(probe, "Settings", fake_settings)
    report = probe.run_probe("google", "singapore")
    assert report["status"] == "blocked" and report["calls_used"] == 0
    assert report["checks"][0]["error"]["code"] == "missing_credentials"


def test_all_no_route_results_do_not_claim_mode_coverage(monkeypatch):
    class NoRoutesProvider:
        def __init__(self, *args):
            self.by_id = {}

        def search_places(self, query, region):
            name = {"豫园": "上海豫园"}.get(query, query)
            place = Place(place_id=query, name=name, category="attraction", region=region,
                          coordinates=Coordinates(longitude=121, latitude=31, crs="GCJ02"),
                          provider_refs=[ProviderRef(provider="amap", provider_place_id=query)])
            self.by_id[query] = place
            return [place]

        def get_place(self, provider_id, region):
            return self.by_id[provider_id]

        def nearby_places(self, *args):
            return []

        def route(self, query):
            return route_result(query, "amap", status="no_route", unknown_reason="synthetic_no_route")

    monkeypatch.setattr(probe, "Settings", fake_settings)
    monkeypatch.setattr(probe, "AmapProvider", NoRoutesProvider)
    report = probe.run_probe("amap", "shanghai")
    assert report["status"] == "partial"
    assert not any(report["mode_coverage"].values())


def test_same_name_multiple_candidates_stops_route_guessing(monkeypatch):
    class AmbiguousProvider:
        def __init__(self, *args):
            pass

        def search_places(self, query, region):
            name = {"豫园": "上海豫园"}.get(query, query)
            return [Place(place_id=f"{query}-{index}", name=name, category="attraction", region=region,
                          coordinates=Coordinates(longitude=121, latitude=31, crs="GCJ02")) for index in (1, 2)]

        def route(self, query):
            raise AssertionError("ambiguous places must not be used for routes")

    monkeypatch.setattr(probe, "Settings", fake_settings)
    monkeypatch.setattr(probe, "AmapProvider", AmbiguousProvider)
    report = probe.run_probe("amap", "shanghai")
    assert report["status"] == "partial"
    assert len([row for row in report["checks"] if row["kind"] == "identity"]) == 3
    assert not any(row["kind"] == "route" for row in report["checks"])
