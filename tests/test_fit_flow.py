import copy
import json

from app.apis.examples import CONFIRM_EXAMPLE


def _generate(client, req):
    r = client.post("/fit/api/v1/packages", json=req)
    assert r.status_code == 200, r.text
    return r.json()


def test_generate_multi_city_package(client, hb, fit_request):
    pkg = _generate(client, fit_request)
    assert pkg["status"] == "GENERATED", json.dumps(pkg["adminComments"], indent=1)
    dests = pkg["displayPackage"]["destinations"]
    assert [d["name"] for d in dests] == ["Dubai", "Istanbul"]

    dubai = dests[0]
    hotel = dubai["hotel"]["selected"]
    # 4★ Downtown + DELUXE KING requested → Downtown Grand (deluxe king, zone 3) wins
    assert hotel["code"] == 1001 and hotel["rooms"][0]["roomCode"] == "DBL.DX-KG"
    assert hotel["refundable"] is True
    assert hotel["images"] and hotel["images"][0]["url"].startswith("https://photos.hotelbeds.com/giata/bigger/")
    assert dubai["resolvedDestination"]["zone"]["name"] == "Downtown Dubai"

    acts = {i["requested"]["excursion"]: i for i in dubai["activities"]["items"]}
    assert acts["Desert safari"]["activity"]["code"] == "E-U10-SAFARI"
    assert acts["Desert safari"]["date"] == "2026-12-11"
    assert acts["Burj Khalifa"]["activity"]["code"] == "E-U10-BURJ"
    assert dubai["activities"]["suggested"], "free days should get suggestions"

    arr, dep = dubai["transfers"]["arrival"], dubai["transfers"]["departure"]
    assert arr["selected"]["transferType"] == "PRIVATE" and arr["from"]["code"] == "DXB"
    assert arr["to"] == {"type": "ATLAS", "code": "1001", "name": "Downtown Grand Hotel"}
    assert dep["selected"]["transferType"] == "PRIVATE"

    ist = dests[1]
    assert ist["hotel"]["selected"]["code"] == 2001  # Sultanahmet zone
    assert ist["transfers"]["departure"]["selected"]["transferType"] == "SHARED"  # hotelTrsfrType SHARED
    ist_acts = {i["requested"]["excursion"]: i["activity"]["code"] for i in ist["activities"]["items"]}
    assert ist_acts == {"Bosphorus cruise": "E-E10-BOSPH", "Hagia Sophia Blue Mosque": "E-E10-OLDCITY"}

    # Hotel availability got correct occupancy + market + filters
    body = hb.bodies("POST", "/hotel-api/1.0/hotels")[0]
    assert body["occupancies"] == [{"rooms": 1, "adults": 2, "children": 1, "paxes": [{"type": "CH", "age": 8}]}]
    assert body["sourceMarket"] == "PK" and body["filter"]["minCategory"] == 4
    assert body["boards"] == {"board": ["BB"], "included": True}

    # Round-trip optimisation: Dubai arrival+departure both DXB → one call
    trf_calls = hb.requests("GET", "/transfer-api/1.0/availability")
    assert len(trf_calls) == 2  # Dubai round trip + Istanbul round trip

    pricing = pkg["displayPackage"]["pricing"]
    assert pricing["grandTotal"]["currency"] == "EUR" and pricing["grandTotal"]["amount"] > 0
    assert pricing["budgetCheck"]["withinBudget"] is True
    itinerary = pkg["displayPackage"]["itinerary"]
    assert itinerary[0]["date"] == "2026-12-10" and itinerary[-1]["date"] == "2026-12-17"
    assert any(e["type"] == "ACTIVITY" for d in itinerary for e in d["events"])
    meta = pkg["generationMetadata"]
    assert meta["hotelbedsCalls"]["total"] >= 6
    cats = {c["category"] for c in pkg["adminComments"]}
    assert "ADD_ON" in cats and "MEAL" in cats


def test_content_is_cached_between_requests(client, hb, fit_request):
    _generate(client, fit_request)
    first = len(hb.requests("GET", "/hotel-content-api/1.0/locations/destinations"))
    _generate(client, fit_request)
    assert len(hb.requests("GET", "/hotel-content-api/1.0/locations/destinations")) == first


def test_confirm_full_package(client, hb, fit_request):
    pkg = _generate(client, fit_request)
    r = client.post(f"/fit/api/v1/packages/{pkg['packageId']}/confirm", json=CONFIRM_EXAMPLE)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["status"] == "CONFIRMED", json.dumps(res, indent=1)
    types = [i["type"] for i in res["items"]]
    assert types[:2] == ["HOTEL", "HOTEL"] and types.count("TRANSFER") == 4 and types.count("ACTIVITY") == 4

    hotel_body = hb.bodies("POST", "/hotel-api/1.0/bookings")[0]
    assert hotel_body["rooms"][0]["rateKey"].endswith("-CR")  # checkrate's rateKey used
    paxes = hotel_body["rooms"][0]["paxes"]
    assert {(p["type"], p.get("age")) for p in paxes} == {("AD", None), ("CH", 8)}
    assert len(hotel_body["clientReference"]) <= 20

    act_body = hb.bodies("PUT", "/activity-api/3.0/bookings")[0]
    safari = next(b for b in hb.bodies("PUT", "/activity-api/3.0/bookings")
                  if b["activities"][0]["rateKey"] == "DK-E-U10-SAFARI")
    assert safari["activities"][0]["answers"][0]["answer"] == "Downtown Grand Hotel"  # auto-answered
    assert act_body["holder"]["telephones"] == ["+923001234567"]

    trf_bodies = hb.bodies("POST", "/transfer-api/1.0/bookings")
    dets = [b["transfers"][0]["transferDetails"][0] for b in trf_bodies]
    assert {"type": "FLIGHT", "direction": "ARRIVAL", "code": "EK601", "companyName": "Emirates"} in dets

    status = client.get(f"/fit/api/v1/packages/{pkg['packageId']}").json()
    assert status["status"] == "CONFIRMED" and status["booking"]["items"]

    sim = client.post(f"/fit/api/v1/packages/{pkg['packageId']}/cancel", json={"simulate": True}).json()
    assert sim["simulation"] is True and len(sim["items"]) == len(res["items"])
    assert hb.cancelled == []
    real = client.post(f"/fit/api/v1/packages/{pkg['packageId']}/cancel", json={"simulate": False}).json()
    assert real["status"] == "CANCELLED" and len(hb.cancelled) == len(res["items"])


def test_confirm_requires_transport_codes(client, hb, fit_request):
    pkg = _generate(client, fit_request)
    body = copy.deepcopy(CONFIRM_EXAMPLE)
    body["transportCodes"] = body["transportCodes"][:1]
    r = client.post(f"/fit/api/v1/packages/{pkg['packageId']}/confirm", json=body)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "TRANSPORT_CODE_REQUIRED"
    assert not hb.requests("POST", "/hotel-api/1.0/bookings")  # nothing booked


def test_confirm_price_change_blocks_booking(client, hb, fit_request):
    pkg = _generate(client, fit_request)
    hb.checkrate_price_factor = 1.10
    r = client.post(f"/fit/api/v1/packages/{pkg['packageId']}/confirm", json=CONFIRM_EXAMPLE)
    assert r.status_code == 409 and r.json()["error"]["code"] == "PRICE_CHANGED"
    assert not hb.requests("POST", "/hotel-api/1.0/bookings")
    body = {**CONFIRM_EXAMPLE, "acceptPriceChanges": True}
    r = client.post(f"/fit/api/v1/packages/{pkg['packageId']}/confirm", json=body)
    assert r.status_code == 200 and r.json()["status"] == "CONFIRMED"


def test_all_or_nothing_rolls_back(client, hb, fit_request):
    pkg = _generate(client, fit_request)
    hb.fail_transfer_booking = True
    r = client.post(f"/fit/api/v1/packages/{pkg['packageId']}/confirm", json=CONFIRM_EXAMPLE)
    assert r.status_code == 200
    res = r.json()
    assert res["status"] == "BOOKING_FAILED_ROLLED_BACK"
    booked = [i for i in res["items"] if i.get("reference")]
    assert booked and all(i.get("rolledBack") for i in booked)
    assert sorted(hb.cancelled) == sorted(i["reference"] for i in booked)
    # the 500 on a booking call must NOT be retried (money-moving)
    assert len(hb.requests("POST", "/transfer-api/1.0/bookings")) == 1


def test_hotel_booking_timeout_is_reconciled(client, hb, fit_request):
    pkg = _generate(client, fit_request)
    hb.hotel_booking_timeout = True
    r = client.post(f"/fit/api/v1/packages/{pkg['packageId']}/confirm",
                    json={**CONFIRM_EXAMPLE, "bookingMode": "BEST_EFFORT"})
    res = r.json()
    hotels = [i for i in res["items"] if i["type"] == "HOTEL"]
    assert hotels[0]["reconciled"] is True and hotels[0]["reference"] == "102-RECON1"
    assert len(hb.requests("POST", "/hotel-api/1.0/bookings")) == 2  # one attempt per hotel, no blind retry


def test_swap_hotel_alternative_researches_transfers(client, hb, fit_request):
    pkg = _generate(client, fit_request)
    alt = pkg["displayPackage"]["destinations"][0]["hotel"]["alternatives"][0]
    before = len(hb.requests("GET", "/transfer-api/1.0/availability"))
    body = copy.deepcopy(CONFIRM_EXAMPLE)
    body["selection"]["hotelOptionIds"] = {"0": alt["optionId"]}
    r = client.post(f"/fit/api/v1/packages/{pkg['packageId']}/confirm", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CONFIRMED"
    after = hb.requests("GET", "/transfer-api/1.0/availability")[before:]
    assert len(after) == 2 and all(f"/ATLAS/{alt['code']}" in r.url.path for r in after)


def test_prebook_returns_questions_and_prices(client, hb, fit_request):
    pkg = _generate(client, fit_request)
    r = client.post(f"/fit/api/v1/packages/{pkg['packageId']}/prebook", json={})
    assert r.status_code == 200
    pre = r.json()
    assert pre["allAvailable"] and not pre["anyPriceChanged"]
    assert any(q[0]["code"] == "HOTEL" for q in pre["activityQuestions"].values())


def test_swagger_placeholder_request_is_rejected_cleanly(client):
    raw = json.load(open("tests/data/user_fit_template.json"))
    r = client.post("/fit/api/v1/packages", json=raw)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"


def test_child_age_validation(client, fit_request):
    fit_request["travelerInfo"]["childAges"] = []
    r = client.post("/fit/api/v1/packages", json=fit_request)
    assert r.status_code == 422 and "childAges" in r.text


def test_missing_nationality_is_reported(client, hb, fit_request):
    fit_request["travelerInfo"].pop("nationality")
    pkg = _generate(client, fit_request)
    assert any(f["field"] == "travelerInfo.nationality" for f in pkg["requestFieldsAdded"])


def test_get_and_list_packages(client, hb, fit_request):
    pkg = _generate(client, fit_request)
    got = client.get(f"/fit/api/v1/packages/{pkg['packageId']}").json()
    assert got["packageId"] == pkg["packageId"]
    lst = client.get("/fit/api/v1/packages").json()
    assert lst["items"][0]["packageId"] == pkg["packageId"]
    assert client.get("/fit/api/v1/packages/FIT-NOPE").status_code == 404
