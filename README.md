# Ubidtours FIT Service — Hotelbeds only

Fully Independent Travel (FIT) package engine built entirely on **Hotelbeds APItude**
(Hotels + Activities + Transfers). Amadeus has been removed.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env            # fill in Hotelbeds key/secret (shared or per product)
uvicorn app.main:app --host 0.0.0.0 --port 8000
pytest                          # 29 offline tests (Hotelbeds faked with respx)
```

| URL | What |
|---|---|
| `/` | Landing page with links |
| `/fit/docs` | FIT package API (pre-filled FIT request examples) |
| `/fit/form` | Interactive FIT form → generates and renders the package |
| `/hotels/docs` | Hotelbeds Hotels: availability, checkrates, bookings (list/detail/modify/cancel), content, destination resolver |
| `/activities/docs` | Hotelbeds Activities: search, availability, details, confirm / preconfirm / reconfirm, list, detail, cancel, content, cache portfolio |
| `/transfers/docs` | Hotelbeds Transfers: availability (one-way / round trip / multi-route), bookings, cancel (full / per service), amend, cache |
| `/health`, `/health/ready` | Liveness / which Hotelbeds products are configured |

## FIT flow

`POST /fit/api/v1/packages` (alias `/fit/api/v1/generate`):

1. **Hotels** — city → Hotelbeds destination code (+ zone from `accommodation.location`), one availability
   search per destination, rooms fitted to the party, scored on stars / room / bed / area / brand /
   refundability / budget. Shortlist enriched with descriptions + images (Content API, cached 7 days).
2. **Activities** — each requested excursion matched on text, type, date, day part, guide language, crowd
   comfort and mobility; one targeted `text` search if nothing matches; optional suggestions for free days.
3. **Transfers** — arrival (airport/station → hotel) and departure (hotel → airport/station) using the selected
   hotel's code as `ATLAS` location. Same terminal both ways → one round-trip call.
4. **Package** — itinerary, pricing (net / selling / markup, `hotelMandatory` respected), budget check,
   cancellation summary, admin advisories, narrative. Stored in SQLite.

Booking: `POST /packages/{id}/prebook` (checkrates + fresh activity rateKeys + questions) →
`POST /packages/{id}/confirm` (hotels → activities → transfers, ALL_OR_NOTHING rollback, timeout reconciliation,
no blind retries on money-moving calls) → `GET /packages/{id}/bookings` → `POST /packages/{id}/cancel`
(simulate first).

## Fields added to the FIT request (needed by Hotelbeds, all optional)

| Field | Why |
|---|---|
| `travelerInfo.nationality` | Hotelbeds `sourceMarket` — prices are only valid for that market (default `DEFAULT_SOURCE_MARKET`) |
| `accommodation.rooms` | Number of rooms (auto: max 2 adults / 4 persons per room) |
| `accommodation.boardBasis` | Meal plan filter RO/BB/HB/FB/AI |
| `accommodation.refundableOnly` | Exclude NRF rates |
| `travelSegment.arrivalTerminalCode` | Exact arrival airport/port/station (`DXB`, `PORT:xxx`, `STATION:xxx`) |
| `budget.currency` | Currency of `budget.range` |

Collected at **booking** time (`ConfirmPackageRequest`): holder (name, email, E.164 phone), traveller names/ages,
flight/train/cruise numbers per transfer (mandatory for Hotelbeds), answers to activity questions.

Every package lists the defaults it used in `requestFieldsAdded`.

## Not available from Hotelbeds (reported as advisories)

Within-city transport (rental car / driver), hotel→hotel intercity transfers, luggage storage, insurance,
eSIM, e-Visa, dietary filters.

## Verify in the Hotelbeds sandbox before go-live

Some details are not consistent across Hotelbeds doc pages; they are configurable or isolated:
transfer booking path `/bookings` vs `/booking` (`HOTELBEDS_TRANSFER_BOOKING_PATH`), transfer amend path,
activity `preconfirm`/`reconfirm` paths, transfer cancel `?simulation=true`, and the hotel booking-list
response wrapper used for timeout reconciliation. Evaluation keys allow **50 calls/day per product** —
every package reports its call count in `generationMetadata.hotelbedsCalls`.
