"""
FIT request / response / booking models.

The request mirrors the platform's FIT form JSON exactly (same field
names, including the existing spellings `optamizeCostOn`,
`preferedBrand`, `arprtOrStatnTrsfr*`). Fields that Hotelbeds needs but
the original form did not carry were ADDED as optional fields and are
tagged "[Hotelbeds]" in their descriptions.

The Swagger "string" placeholders are tolerated: any field holding the
literal "string" is treated as empty, so the default Swagger body still
validates structurally.
"""
from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.utils.text import clean

# ─────────────────────────────────────────────────────────────────────
# Request
# ─────────────────────────────────────────────────────────────────────


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def _placeholder_to_none(cls, v):
        if isinstance(v, str) and clean(v) is None:
            return None
        if isinstance(v, list):
            return [x for x in v if not (isinstance(x, str) and clean(x) is None)]
        return v


class TripInfo(_Lenient):
    tripRegions: List[str] = Field(default_factory=list)
    tripCountries: List[str] = Field(default_factory=list,
                                     description="Country names or ISO codes. Used to resolve Hotelbeds destinations.")
    startDestinationCity: Optional[str] = Field(default=None, description="First city of the trip")
    startDestinationLocation: Optional[str] = Field(
        default=None, description="Arrival point of the trip — airport name/IATA (e.g. 'DXB'), port or station")
    startDestinationDate: datetime = Field(description="Trip arrival date-time (local)")
    endDestinationCity: Optional[str] = Field(default=None, description="Last city of the trip")
    endDestinationLocation: Optional[str] = Field(default=None, description="Final departure point (airport/IATA ...)")
    endDestinationDate: datetime = Field(description="Final departure date-time (local)")

    @model_validator(mode="after")
    def _order(self):
        if self.endDestinationDate < self.startDestinationDate:
            raise ValueError("tripInfo.endDestinationDate must be after startDestinationDate")
        return self


class TravelerInfo(_Lenient):
    adults: int = Field(ge=1, le=30)
    children: int = Field(default=0, ge=0, le=20)
    childAges: List[str] = Field(default_factory=list, description="One age per child (0-17)")
    travelerType: Optional[str] = Field(default=None, description="FAMILY, COUPLE, SOLO, GROUP, SENIOR, FRIENDS")
    isImmobile: bool = False
    comment: Optional[str] = None
    nationality: Optional[str] = Field(
        default=None,
        description="[Hotelbeds] Lead traveller nationality/residence (name or ISO-2). Sent as `sourceMarket` "
                    "— Hotelbeds prices are only valid for the market they were quoted for.")

    @field_validator("childAges", mode="before")
    @classmethod
    def _ages(cls, v):
        if v is None:
            return []
        out = []
        for a in v:
            if a is None or (isinstance(a, str) and clean(a) is None):
                continue
            out.append(str(a).strip())
        return out

    @model_validator(mode="after")
    def _child_ages(self):
        if self.children and len(self.childAges) != self.children:
            raise ValueError(f"travelerInfo.childAges must contain exactly {self.children} age(s) "
                             "(Hotelbeds prices children by age)")
        for a in self.childAges:
            try:
                age = int(float(a))
            except ValueError:
                raise ValueError(f"childAges value '{a}' is not a number")
            if not 0 <= age <= 17:
                raise ValueError(f"child age {age} must be between 0 and 17")
        return self

    @property
    def child_age_ints(self) -> list[int]:
        return [int(float(a)) for a in self.childAges]


class TravelSegment(_Lenient):
    fromDestination: Optional[str] = None
    toDestination: Optional[str] = None
    travelDate: Optional[datetime] = Field(default=None, description="Arrival date-time into this destination")
    modeOfTravel: Optional[str] = Field(default=None, description="FLIGHT, TRAIN, CAR, BUS, FERRY, CRUISE")
    classOfTravel: Optional[str] = None
    needStorage: bool = False
    storageComment: Optional[str] = None
    arrivalTerminalCode: Optional[str] = Field(
        default=None,
        description="[Hotelbeds] Optional exact arrival terminal for this destination: IATA code ('DXB') or "
                    "'PORT:<code>' / 'STATION:<code>'. Auto-resolved from the city when omitted.")


class WithinCityTransport(_Lenient):
    startDate: Optional[datetime] = None
    endDate: Optional[datetime] = None
    withinCityTransportType: Optional[str] = None


class TransportDetails(_Lenient):
    arprtOrStatnTrsfrReqd: bool = Field(default=False, description="Arrival transfer airport/station → hotel")
    arprtOrStatnTrsfrDt: Optional[datetime] = Field(default=None, description="Flight/train ARRIVAL time")
    trsfrType: Optional[str] = Field(default=None, description="PRIVATE, SHARED, LUXURY")
    hotelTrsfrReqd: bool = Field(default=False, description="Departure transfer hotel → airport/station")
    hotelTrsfrDt: Optional[datetime] = Field(default=None, description="Flight/train DEPARTURE time")
    hotelTrsfrType: Optional[str] = Field(default=None, description="PRIVATE, SHARED, LUXURY")
    luggageComment: Optional[str] = None
    withinCityTransportReqd: bool = False
    withinCityTransports: List[WithinCityTransport] = Field(default_factory=list)


class Accommodation(_Lenient):
    hotelCategory: Optional[str] = Field(default=None, description="3_STAR, 4_STAR, 5_STAR, BOUTIQUE, LUXURY ...")
    location: Optional[str] = Field(default=None, description="Preferred area, e.g. 'Downtown', 'Dubai Marina'")
    roomType: Optional[str] = Field(default=None, description="STANDARD, SUPERIOR, DELUXE, SUITE, FAMILY")
    bedPref: Optional[str] = Field(default=None, description="DOUBLE, TWIN, KING")
    numberNights: Optional[int] = Field(default=None, ge=1, le=60)
    preferedBrand: Optional[str] = None
    comment: Optional[str] = None
    rooms: Optional[int] = Field(default=None, ge=1, le=9,
                                 description="[Hotelbeds] Number of rooms. Auto-calculated when omitted.")
    boardBasis: Optional[str] = Field(
        default=None, description="[Hotelbeds] Board/meal plan: RO (room only), BB, HB, FB, AI")
    refundableOnly: Optional[bool] = Field(default=None,
                                           description="[Hotelbeds] Exclude non-refundable (NRF) rates")


class ExcursionRequest(_Lenient):
    excursionDate: Optional[date] = None
    excursionType: Optional[str] = Field(default=None, description="CULTURAL, ADVENTURE, NATURE, CULINARY, ...")
    excursion: Optional[str] = Field(default=None, description="What the traveller wants, e.g. 'Desert safari'")
    dayPart: Optional[str] = Field(default=None, description="MORNING, AFTERNOON, EVENING, FULL_DAY")
    adultCount: Optional[int] = Field(default=None, ge=0)
    childCount: Optional[int] = Field(default=None, ge=0)
    comment: Optional[str] = None


class Destination(_Lenient):
    destinationIndex: int = 0
    destinationName: str = Field(min_length=1, description="City, e.g. 'Dubai'")
    travelSegment: Optional[TravelSegment] = None
    transportDetails: Optional[TransportDetails] = None
    accommodation: Optional[Accommodation] = None
    excursions: List[ExcursionRequest] = Field(default_factory=list)


class Budget(_Lenient):
    range: Optional[float] = Field(default=None, ge=0, description="Total trip budget")
    splurgeOn: List[str] = Field(default_factory=list, description="HOTEL, ACTIVITIES, TRANSFERS, ...")
    optamizeCostOn: List[str] = Field(default_factory=list)
    planningStyle: Optional[str] = Field(default=None, description="BUDGET, MID_RANGE, LUXURY, ULTRA_LUXURY")
    decisionTimeline: Optional[str] = Field(default=None, description="IMMEDIATE, WITHIN_WEEK, FLEXIBLE")
    currency: Optional[str] = Field(default=None, description="[Hotelbeds] ISO currency of `range` (default EUR)")


class FoodGuideEtc(_Lenient):
    mealPref: Optional[str] = None
    mealComment: Optional[str] = None
    guideLanguage: Optional[str] = Field(default=None, description="e.g. English, Arabic, Urdu — or ISO code")
    guideComment: Optional[str] = None
    travelInsuranceReqd: bool = False
    esimReqd: bool = False
    evisaReqd: bool = False


class ComfortSafety(_Lenient):
    comfortWtCrowd: Optional[str] = None
    comfortWtRemote: Optional[str] = None
    comfortWtLocalTranspt: Optional[str] = None
    comment: Optional[str] = None


class FITRequest(_Lenient):
    tripInfo: TripInfo
    travelerInfo: TravelerInfo
    destinations: List[Destination] = Field(min_length=1, max_length=12)
    budget: Budget = Field(default_factory=Budget)
    foodGuideEtc: FoodGuideEtc = Field(default_factory=FoodGuideEtc)
    comfortSafety: ComfortSafety = Field(default_factory=ComfortSafety)
    refTripComment: Optional[str] = None
    addnlComment: Optional[str] = None
    status: Optional[str] = "DRAFT"

    @model_validator(mode="after")
    def _sort(self):
        self.destinations.sort(key=lambda d: d.destinationIndex)
        return self


# ─────────────────────────────────────────────────────────────────────
# Response
# ─────────────────────────────────────────────────────────────────────


class Advisory(BaseModel):
    severity: Literal["INFO", "WARNING", "CRITICAL"]
    category: str
    message: str
    destinationIndex: Optional[int] = None
    actionRequired: bool = False


class FITPackageResponse(BaseModel):
    packageId: str
    status: Literal["GENERATED", "PARTIAL", "FAILED"]
    provider: str = "HOTELBEDS"
    createdAt: datetime
    expiresAt: datetime
    displayPackage: Dict[str, Any]
    bookingPayload: Dict[str, Any]
    adminComments: List[Advisory]
    requestFieldsAdded: List[Dict[str, str]] = Field(
        default_factory=list, description="Fields Hotelbeds needs that the request did not provide (defaults used)")
    generationMetadata: Dict[str, Any]


# ─────────────────────────────────────────────────────────────────────
# Booking
# ─────────────────────────────────────────────────────────────────────


class BookingHolder(BaseModel):
    title: Optional[str] = Field(default="MR", description="MR, MRS, MS, MISS, DR")
    firstName: str = Field(min_length=1, max_length=50)
    lastName: str = Field(min_length=1, max_length=50)
    email: EmailStr
    phone: str = Field(description="E.164, e.g. +923001234567 (mandatory for transfers & activities)")
    country: Optional[str] = Field(default=None, description="ISO-2, used for activity holder")
    address: Optional[str] = None
    zipCode: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def _e164(cls, v: str) -> str:
        digits = v.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        if digits.startswith("00"):
            digits = "+" + digits[2:]
        if not digits.startswith("+") or not digits[1:].isdigit() or not 8 <= len(digits) <= 16:
            raise ValueError("phone must be in E.164 format, e.g. +923001234567")
        return digits


class BookingTraveller(BaseModel):
    type: Literal["ADULT", "CHILD"]
    firstName: str = Field(min_length=1, max_length=50)
    lastName: str = Field(min_length=1, max_length=50)
    age: Optional[int] = Field(default=None, ge=0, le=120, description="Mandatory for children")

    @model_validator(mode="after")
    def _age(self):
        if self.type == "CHILD" and self.age is None:
            raise ValueError("age is required for CHILD travellers")
        return self


class TransportCode(BaseModel):
    destinationIndex: int
    direction: Literal["ARRIVAL", "DEPARTURE"]
    code: str = Field(min_length=1, max_length=50, description="Flight / train / cruise number, e.g. EK601")
    companyName: Optional[str] = Field(default=None, max_length=100)


class ActivityAnswer(BaseModel):
    itemId: str = Field(description="Activity item id from the package (bookingPayload.activities[].itemId)")
    questionCode: str
    answer: str


class ComponentSelection(BaseModel):
    """Swap the default pick for an alternative before booking."""

    hotelOptionIds: Dict[str, str] = Field(default_factory=dict,
                                           description="destinationIndex → hotel optionId (from hotel.alternatives)")
    excludeItemIds: List[str] = Field(default_factory=list, description="Item ids NOT to book")
    includeSuggestedItemIds: List[str] = Field(default_factory=list,
                                               description="Suggested (optional) activity item ids TO book")


class ConfirmPackageRequest(BaseModel):
    holder: BookingHolder
    travellers: List[BookingTraveller] = Field(min_length=1)
    transportCodes: List[TransportCode] = Field(default_factory=list,
                                                description="Mandatory for every airport/station transfer")
    activityAnswers: List[ActivityAnswer] = Field(default_factory=list)
    selection: ComponentSelection = Field(default_factory=ComponentSelection)
    remark: Optional[str] = Field(default=None, max_length=1000)
    acceptPriceChanges: bool = Field(default=False,
                                     description="Book even if the re-checked price moved beyond tolerance")
    bookingMode: Literal["ALL_OR_NOTHING", "BEST_EFFORT"] = Field(
        default="ALL_OR_NOTHING",
        description="ALL_OR_NOTHING cancels already-confirmed components if a later one fails")


class PrebookRequest(BaseModel):
    travellers: Optional[List[BookingTraveller]] = None
    selection: ComponentSelection = Field(default_factory=ComponentSelection)


class CancelPackageRequest(BaseModel):
    simulate: bool = Field(default=True, description="true = only return the cancellation fees")
    itemIds: Optional[List[str]] = Field(default=None, description="Cancel only these items (default: all)")
    reason: Optional[str] = None
