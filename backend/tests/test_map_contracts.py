"""Synthetic provider contracts and failure handling. No network or real keys."""

import json
from datetime import datetime

import httpx
import pytest
from pydantic import SecretStr

from app.providers.maps.amap import AmapProvider
from app.providers.maps.base import MapError, RouteQuery
from app.providers.maps.google import GoogleProvider
from app.providers.maps.policy import get_policy
from app.providers.maps.transport import MapTransport
from app.schemas.common import RegionRef
from app.schemas.place import Coordinates, Place

SHANGHAI = RegionRef(label="上海", country_code="CN", timezone="Asia/Shanghai")
SINGAPORE = RegionRef(label="Singapore", country_code="SG", timezone="Asia/Singapore")
FAKE_KEY = "synthetic-key-not-a-real-credential"


def transport(handler, limit=12):
    return MapTransport(max_calls=limit, client=httpx.Client(transport=httpx.MockTransport(handler)))


def point(place_id, *, crs="GCJ02", region=SHANGHAI):
    return Place(place_id=place_id, name=place_id, category="attraction", region=region,
                 coordinates=Coordinates(longitude=121.49, latitude=31.23, crs=crs))


def route_query(mode="walking", *, google=False):
    return RouteQuery(origin=point("a", crs="WGS84" if google else "GCJ02", region=SINGAPORE if google else SHANGHAI),
                      destination=point("b", crs="WGS84" if google else "GCJ02", region=SINGAPORE if google else SHANGHAI),
                      mode=mode, departure_at=datetime.fromisoformat("2026-09-30T10:00:00+08:00"))


def test_search_preserves_provider_identity_and_does_not_confirm_user_choice():
    def handler(req):
        assert req.url.params["city_limit"] == "true"
        return httpx.Response(200, json={"status": "1", "pois": [
            {"id": "fake-poi", "name": "合成豫园", "typecode": "110000", "location": "121.49,31.23", "address": []}]})
    provider = AmapProvider(SecretStr(FAKE_KEY), transport(handler))
    place = provider.search_places("豫园", SHANGHAI)[0]
    assert place.provider_refs[0].provider_place_id == "fake-poi"
    assert place.coordinates.crs == "GCJ02" and place.address is None
    assert place.identity_status == "candidate"
    assert provider.search_places("豫园", SHANGHAI)[0].place_id == place.place_id


@pytest.mark.parametrize("mode", ["walking", "transit", "driving"])
def test_amap_units_geometry_and_time_application(mode):
    def handler(req):
        assert req.url.params["show_fields"] == "cost,polyline"
        option = {"distance": "1234", "cost": {"duration": "600"},
                  "steps": [{"polyline": "121.49,31.23;121.50,31.24"}]}
        if mode == "transit":
            assert req.url.params["city1"] == "021"
            assert req.url.params["date"] == "2026-09-30"
            assert req.url.params["time"] == "10-00"
            option["segments"] = [{"walking": {"steps": [{"polyline": {"polyline": "121.49,31.23;121.50,31.24"}}]}}]
        return httpx.Response(200, json={"status": "1", "route": {"transits" if mode == "transit" else "paths": [option]}})
    provider = AmapProvider(SecretStr(FAKE_KEY), transport(handler))
    leg = provider.route(route_query(mode))
    assert leg.duration_seconds == 600 and leg.distance_meters == 1234
    assert leg.geometry.crs == "GCJ02" and len(leg.geometry.points) == 2
    assert leg.departure_time_applied == (mode == "transit")


@pytest.mark.parametrize("option, expected", [
    (None, "no_route"), ({"distance": "1200"}, "unknown"),
])
def test_empty_or_missing_route_never_becomes_zero(option, expected):
    provider = AmapProvider(SecretStr(FAKE_KEY), transport(lambda req: httpx.Response(200,
        json={"status": "1", "route": {"paths": [] if option is None else [option]}})))
    leg = provider.route(route_query())
    assert leg.status == expected and leg.duration_seconds is None


@pytest.mark.parametrize("infocode, expected", [("10001", "permission_denied"), ("10002", "permission_denied"), ("10003", "rate_limited")])
def test_amap_permission_and_quota_failures_are_distinct_and_sanitized(infocode, expected):
    provider = AmapProvider(SecretStr(FAKE_KEY), transport(lambda req: httpx.Response(200,
        json={"status": "0", "infocode": infocode, "info": "raw-secret=" + FAKE_KEY})))
    with pytest.raises(MapError) as error:
        provider.search_places("豫园", SHANGHAI)
    assert error.value.code == expected
    assert FAKE_KEY not in json.dumps(error.value.summary())


def test_budget_stops_before_next_http_request():
    tx = transport(lambda req: httpx.Response(200, json={"status": "1", "pois": []}), limit=1)
    provider = AmapProvider(SecretStr(FAKE_KEY), tx)
    provider.search_places("豫园", SHANGHAI)
    with pytest.raises(MapError, match="count limit") as error:
        provider.search_places("外滩", SHANGHAI)
    assert error.value.code == "budget_exhausted" and tx.calls_used == 1


def test_missing_google_key_and_unsupported_amap_overseas_make_no_request():
    tx = transport(lambda req: pytest.fail("must not call a provider"))
    with pytest.raises(MapError) as error:
        GoogleProvider(None, tx).search_places("Merlion Park", SINGAPORE)
    assert error.value.code == "missing_credentials"
    with pytest.raises(MapError) as error:
        AmapProvider(SecretStr(FAKE_KEY), tx).search_places("Merlion Park", SINGAPORE)
    assert error.value.code == "unsupported" and tx.calls_used == 0


def test_mixed_crs_rejected_before_request():
    tx = transport(lambda req: pytest.fail("must not call a provider"))
    with pytest.raises(MapError) as error:
        AmapProvider(SecretStr(FAKE_KEY), tx).route(route_query(google=True))
    assert error.value.code == "invalid_request" and tx.calls_used == 0


def test_timeout_does_not_leak_request_url_and_does_not_retry():
    def handler(req):
        raise httpx.ReadTimeout("secret " + FAKE_KEY, request=req)
    tx = transport(handler)
    with pytest.raises(MapError) as error:
        AmapProvider(SecretStr(FAKE_KEY), tx).search_places("豫园", SHANGHAI)
    assert error.value.code == "unavailable" and tx.calls_used == 1
    assert FAKE_KEY not in str(error.value)


@pytest.mark.parametrize("status, expected", [(403, "permission_denied"), (429, "rate_limited"), (503, "unavailable")])
def test_http_failures(status, expected):
    provider = GoogleProvider(SecretStr(FAKE_KEY), transport(lambda req: httpx.Response(status, text=FAKE_KEY)))
    with pytest.raises(MapError) as error:
        provider.search_places("Merlion Park", SINGAPORE)
    assert error.value.code == expected and FAKE_KEY not in str(error.value)


def test_google_field_mask_and_fractional_seconds():
    def handler(req):
        body = json.loads(req.content)
        if "places" in req.url.host:
            assert "*" not in req.headers["X-Goog-FieldMask"]
            return httpx.Response(200, json={"places": [{"id": "fake-google-poi", "displayName": {"text": "合成鱼尾狮"},
                "location": {"longitude": 103.85, "latitude": 1.29}, "primaryType": "tourist_attraction"}]})
        assert body["travelMode"] == "TRANSIT" and "intermediates" not in body
        assert body["departureTime"] == "2026-09-30T10:00:00+08:00"
        return httpx.Response(200, json={"routes": [{"duration": "123.5s", "distanceMeters": 1000,
                                                  "polyline": {"encodedPolyline": "synthetic-line"}}]})
    provider = GoogleProvider(SecretStr(FAKE_KEY), transport(handler))
    place = provider.search_places("Merlion Park", SINGAPORE)[0]
    assert place.coordinates.crs == "WGS84"
    leg = provider.route(route_query("transit", google=True))
    assert leg.duration_seconds == 123.5 and leg.geometry.encoding == "google_polyline"


def test_google_missing_route_and_bad_units():
    tx = transport(lambda req: httpx.Response(200, json={"routes": []}))
    assert GoogleProvider(SecretStr(FAKE_KEY), tx).route(route_query(google=True)).status == "no_route"
    provider = GoogleProvider(SecretStr(FAKE_KEY), transport(lambda req: httpx.Response(200,
        json={"routes": [{"duration": "5 minutes", "distanceMeters": 1}]})))
    with pytest.raises(MapError) as error:
        provider.route(route_query(google=True))
    assert error.value.code == "invalid_response"


def test_policy_keeps_geometry_ephemeral_and_google_display_paired():
    for name in ("amap", "google"):
        policy = get_policy(name)
        assert not policy.persist_raw_response and not policy.persist_geometry and not policy.cache_enabled
    assert get_policy("google").render_on == "google"


def test_non_json_response_is_sanitized():
    tx = transport(lambda req: httpx.Response(200, text="bad " + FAKE_KEY))
    with pytest.raises(MapError) as error:
        AmapProvider(SecretStr(FAKE_KEY), tx).search_places("豫园", SHANGHAI)
    assert error.value.code == "invalid_response" and FAKE_KEY not in str(error.value)


def test_malformed_route_option_is_not_an_unhandled_exception():
    provider = AmapProvider(SecretStr(FAKE_KEY), transport(lambda req: httpx.Response(200,
        json={"status": "1", "route": {"paths": [FAKE_KEY]}})))
    with pytest.raises(MapError) as error:
        provider.route(route_query())
    assert error.value.code == "invalid_response" and FAKE_KEY not in str(error.value)
