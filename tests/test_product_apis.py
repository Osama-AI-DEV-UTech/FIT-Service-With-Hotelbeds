import hashlib


def test_hotels_availability_passthrough(client, hb):
    body = {"stay": {"checkIn": "2026-12-10", "checkOut": "2026-12-12"},
            "occupancies": [{"rooms": 1, "adults": 2, "children": 0}],
            "destination": {"code": "DXB"}, "filter": {"maxHotels": 5}, "extraNewField": {"x": 1}}
    r = client.post("/hotels/api/v1/availability", json=body)
    assert r.status_code == 200
    assert r.json()["hotels"]["total"] == 3
    sent = hb.bodies("POST", "/hotel-api/1.0/hotels")[0]
    assert sent["language"] == "ENG" and sent["extraNewField"] == {"x": 1}  # unknown fields forwarded


def test_hotels_availability_requires_single_scope(client):
    body = {"stay": {"checkIn": "2026-12-10", "checkOut": "2026-12-12"},
            "occupancies": [{"rooms": 1, "adults": 2}], "destination": {"code": "DXB"}, "hotels": {"hotel": [1]}}
    assert client.post("/hotels/api/v1/availability", json=body).status_code == 422


def test_children_need_ages(client):
    body = {"stay": {"checkIn": "2026-12-10", "checkOut": "2026-12-12"},
            "occupancies": [{"rooms": 1, "adults": 2, "children": 1}], "destination": {"code": "DXB"}}
    r = client.post("/hotels/api/v1/availability", json=body)
    assert r.status_code == 422 and "paxes" in r.text


def test_upstream_auth_error_maps_to_502(client):
    r = client.get("/hotels/api/v1/bookings/AUTHFAIL")
    assert r.status_code == 502
    err = r.json()["error"]
    assert err["code"] == "HOTELBEDS_AUTH_OR_QUOTA" and "signature" in err["message"]
    assert r.headers.get("x-correlation-id") == err["correlationId"]


def test_idempotent_get_is_retried(client, hb):
    r = client.get("/hotels/api/v1/bookings/FLAKY")
    assert r.status_code == 200
    assert len(hb.requests("GET", "/hotel-api/1.0/bookings/FLAKY")) == 2


def test_resolve_destination_helper(client):
    r = client.get("/hotels/api/v1/resolve/destination",
                   params={"city": "Dubai", "country": "United Arab Emirates", "area": "dubai marina"})
    data = r.json()
    assert data["code"] == "DXB" and data["zone"]["code"] == 7


def test_transfers_availability_builds_path(client, hb):
    r = client.get("/transfers/api/v1/availability", params={
        "fromType": "IATA", "fromCode": "DXB", "toType": "ATLAS", "toCode": "1001",
        "outbound": "2026-12-10T14:30:00", "adults": 2, "children": 1, "infants": 0})
    assert r.status_code == 200 and r.json()["services"]
    path = hb.requests("GET", "/transfer-api/1.0/availability")[0].url.path
    assert path == "/transfer-api/1.0/availability/en/from/IATA/DXB/to/ATLAS/1001/2026-12-10T14:30:00/2/1/0"


def test_transfer_booking_validates_phone(client):
    body = {"holder": {"name": "A", "surname": "K", "email": "a@b.com", "phone": "0300123"},
            "transfers": [{"rateKey": "x", "transferDetails": [{"type": "FLIGHT", "direction": "ARRIVAL",
                                                                 "code": "EK601"}]}]}
    assert client.post("/transfers/api/v1/bookings", json=body).status_code == 422


def test_activities_availability_needs_paxes(client):
    body = {"filters": [{"searchFilterItems": [{"type": "destination", "value": "DXB"}]}],
            "from": "2026-12-10", "to": "2026-12-12"}
    r = client.post("/activities/api/v1/availability", json=body)
    assert r.status_code == 422


def test_activities_window_limit(client):
    body = {"filters": [{"searchFilterItems": [{"type": "destination", "value": "DXB"}]}],
            "from": "2026-12-10", "to": "2027-03-12", "paxes": [{"age": 30}]}
    assert client.post("/activities/api/v1/availability", json=body).status_code == 422


def test_activities_availability_ok(client, hb):
    body = {"filters": [{"searchFilterItems": [{"type": "destination", "value": "DXB"}]}],
            "from": "2026-12-10", "to": "2026-12-12", "paxes": [{"age": 30}]}
    r = client.post("/activities/api/v1/availability", json=body)
    assert r.status_code == 200 and len(r.json()["activities"]) == 4
    assert hb.bodies("POST", "/activity-api/3.0/activities/availability")[0]["from"] == "2026-12-10"


def test_signature():
    from app.hotelbeds.client import HotelbedsClient
    c = HotelbedsClient(product="hotels", base_url="https://x", api_key="k", secret="s")
    assert c.signature(1700000000) == hashlib.sha256(b"ks1700000000").hexdigest()


def test_docs_pages(client):
    for p in ("/fit/docs", "/hotels/docs", "/activities/docs", "/transfers/docs"):
        r = client.get(p)
        assert r.status_code == 200 and "swagger" in r.text.lower()
        assert f"{p[:-5]}/openapi.json" in r.text
    assert client.get("/fit/form").status_code == 200
