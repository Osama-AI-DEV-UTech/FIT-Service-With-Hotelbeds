"""
A stateful fake of the Hotelbeds APItude endpoints the service uses,
built on respx. Response shapes follow the Hotelbeds documentation /
recorded fixtures so the whole FIT pipeline runs end to end offline.
"""
import json
import re
from urllib.parse import unquote

import httpx
import respx

BASE = "https://api.test.hotelbeds.com"


def _hotel(code, name, cat, cat_name, dest, zone, zone_name, lat, lng, rooms):
    return {"code": code, "name": name, "categoryCode": cat, "categoryName": cat_name, "destinationCode": dest,
            "destinationName": dest, "zoneCode": zone, "zoneName": zone_name, "latitude": str(lat),
            "longitude": str(lng), "currency": "EUR", "minRate": "100.00", "maxRate": "900.00", "rooms": rooms}


def _rate(key, net, *, rclass="NOR", rtype="BOOKABLE", board="BB", adults=2, children=1, ages="8", rooms=1,
          mandatory=False, selling=None):
    r = {"rateKey": key, "rateClass": rclass, "rateType": rtype, "net": f"{net:.2f}", "allotment": 5,
         "paymentType": "AT_WEB", "packaging": False, "boardCode": board,
         "boardName": {"BB": "BED AND BREAKFAST", "RO": "ROOM ONLY", "HB": "HALF BOARD"}[board],
         "cancellationPolicies": [{"amount": f"{net * 0.3:.2f}", "from": "2026-12-01T23:59:00+04:00"}],
         "rooms": rooms, "adults": adults, "children": children, "hotelMandatory": mandatory}
    if children:
        r["childrenAges"] = ages
    if selling:
        r["sellingRate"] = f"{selling:.2f}"
    return r


class FakeHotelbeds:
    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.fail_transfer_booking = False
        self.checkrate_price_factor = 1.0
        self.hotel_booking_timeout = False
        self.cancelled: list[str] = []
        self.router = respx.mock(base_url=BASE, assert_all_called=False, assert_all_mocked=True)
        self._routes()

    def __enter__(self):
        self.router.__enter__()
        return self

    def __exit__(self, *exc):
        self.router.__exit__(*exc)

    def requests(self, method: str, path_part: str) -> list[httpx.Request]:
        return [r for r in self.calls if r.method == method and path_part in r.url.path]

    def bodies(self, method: str, path_part: str) -> list:
        return [json.loads(r.content) for r in self.requests(method, path_part) if r.content]

    def _rec(self, request: httpx.Request):
        assert request.headers.get("Api-key"), "Api-key header missing"
        assert re.fullmatch(r"[0-9a-f]{64}", request.headers.get("X-Signature", "")), "bad X-Signature"
        assert request.headers.get("Accept-Encoding") == "gzip"
        self.calls.append(request)

    # ─────────────────────────────────────────────────────────────────
    def _routes(self):
        r = self.router

        @r.get("/hotel-api/1.0/status")
        def status(request):
            self._rec(request)
            return httpx.Response(200, json={"auditData": {}, "status": "OK"})

        # ── Hotels content ───────────────────────────────────────────
        @r.get("/hotel-content-api/1.0/locations/destinations")
        def destinations(request):
            self._rec(request)
            cc = request.url.params.get("countryCodes")
            data = {
                "AE": [{"code": "DXB", "name": {"content": "Dubai"}, "countryCode": "AE",
                        "zones": [{"zoneCode": 3, "name": "Downtown Dubai"}, {"zoneCode": 7, "name": "Dubai Marina"}]},
                       {"code": "AUH", "name": {"content": "Abu Dhabi"}, "countryCode": "AE", "zones": []}],
                "TR": [{"code": "IST", "name": {"content": "Istanbul"}, "countryCode": "TR",
                        "zones": [{"zoneCode": 1, "name": "Sultanahmet"}, {"zoneCode": 2, "name": "Taksim"}]}],
            }.get(cc, [])
            return httpx.Response(200, json={"from": 1, "to": len(data), "total": len(data), "destinations": data})

        @r.get("/hotel-content-api/1.0/hotels")
        def content_hotels(request):
            self._rec(request)
            codes = [int(c) for c in request.url.params.get("codes", "").split(",") if c]
            hotels = [{"code": c, "name": {"content": f"Hotel {c}"},
                       "description": {"content": f"Lovely hotel {c} close to everything."},
                       "address": {"content": "Downtown Boulevard 1" if c in (1001, 1002) else "Street 5"},
                       "city": {"content": "City"}, "chainCode": "HILTO" if c == 1003 else "IND",
                       "images": [{"path": f"00/{c}/{c}_a_001.jpg", "imageTypeCode": "GEN", "visualOrder": 0,
                                   "order": 1}],
                       "facilities": [{"facilityCode": 10, "facilityGroupCode": 70}],
                       "phones": [{"phoneNumber": "+97140000000"}]} for c in codes]
            return httpx.Response(200, json={"from": 1, "to": len(hotels), "total": len(hotels), "hotels": hotels})

        # ── Hotels booking ───────────────────────────────────────────
        @r.post("/hotel-api/1.0/hotels")
        def availability(request):
            self._rec(request)
            body = json.loads(request.content)
            dest = (body.get("destination") or {}).get("code")
            occ = body["occupancies"][0]
            a, ch = occ["adults"], occ["children"]
            ages = ",".join(str(p["age"]) for p in occ.get("paxes") or [])
            if dest == "DXB":
                hotels = [
                    _hotel(1001, "Downtown Grand Hotel", "4EST", "4 STARS", "DXB", 3, "Downtown Dubai", 25.19, 55.27, [
                        {"code": "DBL.DX-KG", "name": "DOUBLE DELUXE KING", "rates": [
                            _rate("HK1001-DX", 820.0, adults=a, children=ch, ages=ages),
                            _rate("HK1001-DX-NRF", 700.0, rclass="NRF", adults=a, children=ch, ages=ages)]},
                        {"code": "DBL.ST", "name": "DOUBLE STANDARD", "rates": [
                            _rate("HK1001-ST", 600.0, adults=a, children=ch, ages=ages)]}]),
                    _hotel(1002, "Budget Inn Downtown", "3EST", "3 STARS", "DXB", 3, "Downtown Dubai", 25.18, 55.26, [
                        {"code": "DBL.ST", "name": "DOUBLE STANDARD", "rates": [
                            _rate("HK1002-ST", 300.0, adults=a, children=ch, ages=ages, rtype="RECHECK")]}]),
                    _hotel(1003, "Marina Palace", "4EST", "4 STARS", "DXB", 7, "Dubai Marina", 25.08, 55.14, [
                        {"code": "SUI.ST", "name": "SUITE", "rates": [
                            _rate("HK1003-SU", 1500.0, adults=a, children=ch, ages=ages, mandatory=True,
                                  selling=1700.0)]}]),
                ]
            elif dest == "IST":
                hotels = [
                    _hotel(2001, "Sultanahmet Boutique", "4EST", "4 STARS", "IST", 1, "Sultanahmet", 41.006, 28.976, [
                        {"code": "DBL.ST", "name": "DOUBLE STANDARD", "rates": [
                            _rate("HK2001-ST", 450.0, adults=a, children=ch, ages=ages)]}]),
                    _hotel(2002, "Taksim Tower", "4EST", "4 STARS", "IST", 2, "Taksim", 41.03, 28.98, [
                        {"code": "TWN.ST", "name": "TWIN STANDARD", "rates": [
                            _rate("HK2002-ST", 380.0, adults=a, children=ch, ages=ages)]}]),
                ]
            else:
                hotels = []
            return httpx.Response(200, json={"auditData": {}, "hotels": {"checkIn": body["stay"]["checkIn"],
                                                                         "checkOut": body["stay"]["checkOut"],
                                                                         "total": len(hotels), "hotels": hotels}})

        @r.post("/hotel-api/1.0/checkrates")
        def checkrates(request):
            self._rec(request)
            body = json.loads(request.content)
            rates = []
            total = 0.0
            for room in body["rooms"]:
                base = {"HK1001-DX": 820.0, "HK2001-ST": 450.0, "HK1003-SU": 1500.0, "HK1002-ST": 300.0,
                        "HK1001-ST": 600.0, "HK2002-ST": 380.0}.get(room["rateKey"], 500.0)
                net = round(base * self.checkrate_price_factor, 2)
                total += net
                rates.append({"rateKey": room["rateKey"] + "-CR", "net": f"{net:.2f}", "rateClass": "NOR",
                              "rateComments": "Tourism Dirham fee payable at hotel.",
                              "cancellationPolicies": [{"amount": "100.00", "from": "2026-12-01T23:59:00+04:00"}]})
            return httpx.Response(200, json={"auditData": {}, "hotel": {
                "code": 1001, "name": "x", "currency": "EUR", "totalNet": f"{total:.2f}",
                "paymentDataRequired": False, "rooms": [{"code": "R", "rates": rates}]}})

        @r.post("/hotel-api/1.0/bookings")
        def hotel_booking(request):
            self._rec(request)
            if self.hotel_booking_timeout:
                raise httpx.ReadTimeout("timeout", request=request)
            body = json.loads(request.content)
            ref = f"102-{len(self.requests('POST', '/hotel-api/1.0/bookings')):06d}"
            return httpx.Response(200, json={"auditData": {}, "booking": {
                "reference": ref, "clientReference": body["clientReference"], "status": "CONFIRMED",
                "totalNet": "820.00", "currency": "EUR", "pendingAmount": "820.00",
                "hotel": {"supplier": {"name": "HOTELBEDS", "vatNumber": "ESB"}},
                "modificationPolicies": {"cancellation": True, "modification": True}}})

        @r.get("/hotel-api/1.0/bookings")
        def hotel_booking_list(request):
            self._rec(request)
            ref = request.url.params.get("clientReference")
            return httpx.Response(200, json={"auditData": {}, "bookings": {"from": 1, "to": 1, "total": 1, "bookings": [
                {"reference": "102-RECON1", "clientReference": ref, "status": "CONFIRMED", "totalNet": "820.00",
                 "currency": "EUR"}]}})

        @r.route(method="DELETE", path__regex=r"^/hotel-api/1\.0/bookings/(?P<ref>[^/]+)$")
        def hotel_cancel(request, ref):
            self._rec(request)
            flag = request.url.params.get("cancellationFlag")
            if flag == "CANCELLATION":
                self.cancelled.append(ref)
            return httpx.Response(200, json={"auditData": {}, "booking": {
                "reference": ref, "status": "CANCELLED" if flag == "CANCELLATION" else "CONFIRMED",
                "cancellationReference": "CXL-1", "currency": "EUR", "hotel": {"cancellationAmount": "0.00"}}})

        @r.route(method="GET", path__regex=r"^/hotel-api/1\.0/bookings/(?P<ref>[^/]+)$")
        def hotel_detail(request, ref):
            self._rec(request)
            if ref == "AUTHFAIL":
                return httpx.Response(401, json={"error": "Request signature verification failed"})
            if ref == "FLAKY":
                self.flaky_count = getattr(self, "flaky_count", 0) + 1
                if self.flaky_count == 1:
                    return httpx.Response(503, text="busy")
            return httpx.Response(200, json={"booking": {"reference": ref, "status": "CONFIRMED",
                                                         "totalNet": "820.00", "currency": "EUR"}})

        # ── Activities ───────────────────────────────────────────────
        @r.route(method="GET", path__regex=r"^/activity-content-api/3\.0/destinations/(?P<lang>\w+)/(?P<cc>\w+)$")
        def act_destinations(request, lang, cc):
            self._rec(request)
            dests = {"AE": [{"code": "DXB", "name": "Dubai"}], "TR": [{"code": "IST", "name": "Istanbul"}]}.get(cc, [])
            return httpx.Response(200, json={"country": {"code": cc, "destinations": dests}})

        @r.post("/activity-api/3.0/activities/availability")
        def act_availability(request):
            self._rec(request)
            body = json.loads(request.content)
            items = body["filters"][0]["searchFilterItems"]
            dest = next((i.get("value") for i in items if i["type"] == "destination"), None)
            frm, to = body["from"], body["to"]
            catalogue = {
                "DXB": [("E-U10-SAFARI", "Desert Safari with BBQ Dinner", "Evening dune bashing and BBQ dinner",
                         85.0, 6, "HOURS", [{"code": "EVE", "name": "Evening"}]),
                        ("E-U10-BURJ", "Burj Khalifa At the Top", "Observation deck tickets level 124",
                         60.0, 2, "HOURS", [{"code": "0900", "name": "Morning 09:00"}]),
                        ("E-U10-CITY", "Dubai City Tour", "Panoramic sightseeing city tour", 40.0, 4, "HOURS", []),
                        ("E-U10-AQUA", "Dubai Aquarium", "Family aquarium and underwater zoo ticket", 35.0, 3, "HOURS",
                         [])],
                "IST": [("E-E10-BOSPH", "Bosphorus Cruise", "Afternoon boat cruise on the Bosphorus", 30.0, 2, "HOURS",
                         [{"code": "PM", "name": "Afternoon"}]),
                        ("E-E10-OLDCITY", "Hagia Sophia and Blue Mosque Guided Tour",
                         "Historic old city walking tour with guide", 55.0, 4, "HOURS",
                         [{"code": "AM", "name": "Morning"}]),
                        ("E-E10-TOPKAPI", "Topkapi Palace Ticket", "Palace and harem museum", 45.0, 3, "HOURS", [])],
            }.get(dest, [])
            acts = []
            for code, name, desc, price, dur, metric, sessions in catalogue:
                acts.append({
                    "code": code, "name": name, "type": "EXCURSION", "currency": "EUR",
                    "content": {"name": name, "description": f"<p>{desc}</p>",
                                "media": {"images": [{"urls": [{"resource": f"https://img/{code}.jpg", "width": 1024,
                                                                "sizeType": "LARGE"}]}]},
                                "segmentationGroups": [{"segments": [{"name": "Sightseeing"}]}]},
                    "modalities": [{"code": "STD", "name": f"{name} - Standard", "duration": {"value": dur,
                                                                                             "metric": metric},
                                    "questions": [{"code": "HOTEL", "text": "Hotel name for pickup", "required": True}]
                                    if code == "E-U10-SAFARI" else [],
                                    "rates": [{"rateCode": "GENERIC", "rateClass": "NOR", "freeCancellation": True,
                                               "rateDetails": [{"rateKey": f"AK-{code}",
                                                                "operationDates": [{"from": frm, "to": to,
                                                                                    "cancellationPolicies": [
                                                                                        {"dateFrom": "2026-12-08T00:00:00",
                                                                                         "amount": price}]}],
                                                                "languages": [{"code": "en"}, {"code": "ar"}],
                                                                "sessions": sessions,
                                                                "totalAmount": {"amount": price * len(body["paxes"])}}]}]}],
                })
            return httpx.Response(200, json={"operationId": "x", "pagination": {"itemsPerPage": 100, "page": 1,
                                                                                 "totalItems": len(acts)},
                                             "activities": acts})

        @r.post("/activity-api/3.0/activities/details")
        def act_details(request):
            self._rec(request)
            body = json.loads(request.content)
            code = body["code"]
            questions = [{"code": "HOTEL", "text": "Hotel name for pickup", "required": True}] \
                if code == "E-U10-SAFARI" else []
            return httpx.Response(200, json={"activity": {"code": code, "name": code, "currency": "EUR", "modalities": [
                {"code": "STD", "questions": questions, "rates": [{"rateCode": "GENERIC", "rateDetails": [
                    {"rateKey": f"DK-{code}", "sessions": [{"code": "EVE", "name": "Evening"}],
                     "operationDates": [{"from": body["from"], "to": body["to"], "cancellationPolicies": []}],
                     "totalAmount": {"amount": 85.0 * len(body["paxes"]) if code == "E-U10-SAFARI" else
                                     {"E-U10-BURJ": 60.0, "E-E10-BOSPH": 30.0, "E-E10-OLDCITY": 55.0}.get(code, 40.0)
                                     * len(body["paxes"])}}]}]}]}})

        @r.put("/activity-api/3.0/bookings")
        def act_confirm(request):
            self._rec(request)
            body = json.loads(request.content)
            n = len(self.requests("PUT", "/activity-api/3.0/bookings"))
            return httpx.Response(200, json={"booking": {
                "reference": f"270-{n:06d}", "clientReference": body["clientReference"], "status": "CONFIRMED",
                "currency": "EUR", "totalNet": 100.0,
                "activities": [{"vouchers": [{"url": f"https://vouchers/270-{n}.pdf"}],
                                "comments": [{"type": "CONTRACT_REMARKS", "text": "Bring passport"}],
                                "supplier": {"name": "Local DMC"}}]}})

        @r.route(method="DELETE", path__regex=r"^/activity-api/3\.0/bookings/(?P<lang>\w+)/(?P<ref>[^/]+)$")
        def act_cancel(request, lang, ref):
            self._rec(request)
            if request.url.params.get("cancellationFlag") == "CANCELLATION":
                self.cancelled.append(ref)
            return httpx.Response(200, json={"booking": {"reference": ref, "status": "CANCELLED", "total": 0,
                                                         "currency": "EUR"}})

        # ── Transfers ────────────────────────────────────────────────
        @r.route(method="GET", path__regex=r"^/transfer-api/1\.0/availability/")
        def trf_availability(request):
            self._rec(request)
            parts = unquote(request.url.path).split("/")
            # /transfer-api/1.0/availability/{lang}/from/{ft}/{fc}/to/{tt}/{tc}/{out}[/{in}]/{a}/{c}/{i}
            ft, fc, tt, tc = parts[6], parts[7], parts[9], parts[10]
            round_trip = len(parts) == 16
            services = []

            def svc(sid, direction, ttype, cat, veh, price, maxpax=4):
                when = parts[12] if (round_trip and direction == "DEPARTURE") else parts[11]
                return {"id": sid, "direction": direction, "transferType": ttype,
                        "vehicle": {"code": veh, "name": {"CR": "Car", "MNBS": "Minibus", "SH": "Shuttle"}[veh]},
                        "category": {"code": cat, "name": cat},
                        "pickupInformation": {"from": {"code": fc}, "to": {"code": tc}, "date": when[:10],
                                              "time": "11:15:00" if direction == "DEPARTURE" else when[11:19],
                                              "pickup": {"address": "Terminal 3", "checkPickup": {
                                                  "mustCheckPickupTime": False}}},
                        "minPaxCapacity": 1, "maxPaxCapacity": maxpax,
                        "content": {"images": [{"url": "https://img/car.png", "type": "LARGE"}],
                                    "transferRemarks": [{"type": "CONTRACT", "description": "Meet at exit",
                                                         "mandatory": True}],
                                    "transferDetailInfo": [{"id": "TRFTIME", "name": "Journey", "description": "30m"}]},
                        "price": {"totalAmount": price, "netAmount": None, "currencyId": "EUR"},
                        "rateKey": f"TK-{direction}-{ttype}-{fc}-{tc}",
                        "cancellationPolicies": [{"amount": price, "from": "2026-12-09T14:30:00",
                                                  "currencyId": "EUR"}]}

            dirs = ["ARRIVAL", "DEPARTURE"] if round_trip else (["ARRIVAL"] if ft == "IATA" else ["DEPARTURE"])
            for d in dirs:
                services += [svc(1, d, "PRIVATE", "STND", "CR", 45.0), svc(2, d, "SHARED", "STND", "SH", 20.0, 16),
                             svc(3, d, "PRIVATE", "PRM", "MNBS", 80.0, 7)]
            return httpx.Response(200, json={"search": {}, "services": services})

        @r.post("/transfer-api/1.0/bookings")
        def trf_book(request):
            self._rec(request)
            if self.fail_transfer_booking:
                return httpx.Response(500, json={"message": "E_CONFIRMATION_NOTRESPONDING",
                                                 "code": "E_CONFIRMATION_NOTRESPONDING"})
            body = json.loads(request.content)
            n = len(self.requests("POST", "/transfer-api/1.0/bookings"))
            return httpx.Response(200, json={"bookings": [{
                "reference": f"1-{n:07d}", "status": "CONFIRMED", "currency": "EUR", "totalAmount": 45.0,
                "clientReference": body.get("clientReference"),
                "transfers": [{"id": 1, "pickupInformation": {"date": "2026-12-10", "time": "14:30:00"},
                               "vouchers": [{"url": f"https://vouchers/t{n}.pdf"}]}]}]})

        @r.route(method="DELETE", path__regex=r"^/transfer-api/1\.0/bookings/(?P<lang>\w+)/reference/(?P<ref>[^/]+)$")
        def trf_cancel(request, lang, ref):
            self._rec(request)
            if request.url.params.get("simulation") != "true":
                self.cancelled.append(ref)
            return httpx.Response(200, json={"bookings": [{"reference": ref, "status": "CANCELLED",
                                                           "totalAmount": 0, "currency": "EUR"}]})
