"""Request models for the Hotelbeds Hotels Booking API (field names = Hotelbeds JSON)."""
from datetime import date
from typing import List, Literal, Optional

from pydantic import Field, model_validator

from app.schemas.common import HBModel


class Stay(HBModel):
    checkIn: date
    checkOut: date
    shiftDays: Optional[int] = Field(default=None, ge=1, le=5)
    allowOnlyShift: Optional[bool] = None

    @model_validator(mode="after")
    def _order(self):
        if self.checkOut <= self.checkIn:
            raise ValueError("stay.checkOut must be after stay.checkIn")
        return self


class Pax(HBModel):
    type: Literal["AD", "CH"]
    age: Optional[int] = Field(default=None, ge=0, le=120)
    name: Optional[str] = None
    surname: Optional[str] = None


class Occupancy(HBModel):
    rooms: int = Field(ge=1, le=9)
    adults: int = Field(ge=1, le=9, description="Adults PER ROOM")
    children: int = Field(default=0, ge=0, le=9, description="Children PER ROOM")
    paxes: Optional[List[Pax]] = Field(default=None, description="Mandatory child ages when children > 0")

    @model_validator(mode="after")
    def _children_ages(self):
        if self.children:
            ages = [p for p in (self.paxes or []) if p.type == "CH" and p.age is not None]
            if len(ages) < self.children:
                raise ValueError("paxes with type CH and age are required for every child")
        return self


class DestinationFilter(HBModel):
    code: str = Field(min_length=1, max_length=8)
    zone: Optional[int] = None


class Geolocation(HBModel):
    latitude: float
    longitude: float
    radius: float = Field(gt=0, le=200)
    unit: Literal["km", "mi"] = "km"
    secondaryLatitude: Optional[float] = None
    secondaryLongitude: Optional[float] = None


class HotelCodes(HBModel):
    hotel: List[int] = Field(min_length=1, max_length=2000)


class AvailabilityFilter(HBModel):
    maxHotels: Optional[int] = Field(default=None, ge=1, le=2000)
    maxRooms: Optional[int] = Field(default=None, ge=1, le=50)
    minRate: Optional[float] = None
    maxRate: Optional[float] = None
    maxRatesPerRoom: Optional[int] = None
    packaging: Optional[bool] = None
    paymentType: Optional[Literal["AT_HOTEL", "AT_WEB", "BOTH"]] = None
    hotelPackage: Optional[Literal["YES", "NO", "BOTH"]] = None
    minCategory: Optional[int] = Field(default=None, ge=1, le=5)
    maxCategory: Optional[int] = Field(default=None, ge=1, le=5)
    contract: Optional[str] = None


class BoardsFilter(HBModel):
    board: List[str]
    included: bool = True


class RoomsFilter(HBModel):
    room: List[str]
    included: bool = True


class KeywordsFilter(HBModel):
    keyword: List[int]
    allIncluded: bool = False


class ReviewFilter(HBModel):
    type: Literal["TRIPADVISOR", "HOTELBEDS"] = "TRIPADVISOR"
    minRate: Optional[float] = Field(default=None, ge=0, le=5)
    maxRate: Optional[float] = Field(default=None, ge=0, le=5)
    minReviewCount: Optional[int] = None


class HotelAvailabilityRequest(HBModel):
    """POST /hotel-api/1.0/hotels — provide exactly ONE of hotels / destination / geolocation."""

    stay: Stay
    occupancies: List[Occupancy] = Field(min_length=1)
    hotels: Optional[HotelCodes] = None
    destination: Optional[DestinationFilter] = None
    geolocation: Optional[Geolocation] = None
    filter: Optional[AvailabilityFilter] = None
    boards: Optional[BoardsFilter] = None
    rooms: Optional[RoomsFilter] = None
    keywords: Optional[KeywordsFilter] = None
    reviews: Optional[List[ReviewFilter]] = None
    accommodations: Optional[List[str]] = None
    dailyRate: Optional[bool] = None
    language: Optional[str] = None
    sourceMarket: Optional[str] = Field(default=None, description="ISO-2 market the price is valid for")
    platform: Optional[int] = None
    aifUse: Optional[bool] = None

    @model_validator(mode="after")
    def _one_scope(self):
        scopes = [s for s in (self.hotels, self.destination, self.geolocation) if s is not None]
        if len(scopes) != 1:
            raise ValueError("Provide exactly one of: hotels, destination, geolocation")
        return self

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "stay": {"checkIn": "2026-12-10", "checkOut": "2026-12-14"},
                "occupancies": [{"rooms": 1, "adults": 2, "children": 1, "paxes": [{"type": "CH", "age": 8}]}],
                "destination": {"code": "DXB"},
                "filter": {"maxHotels": 20, "minCategory": 4, "maxCategory": 5},
                "sourceMarket": "PK",
                "language": "ENG",
            }]
        }
    }


class CheckRateRoom(HBModel):
    rateKey: str
    paxes: Optional[List[Pax]] = None


class CheckRatesRequest(HBModel):
    rooms: List[CheckRateRoom] = Field(min_length=1, max_length=10)
    upselling: Optional[bool] = None
    expandCXL: Optional[bool] = None
    language: Optional[str] = None


class Holder(HBModel):
    name: str = Field(min_length=1, max_length=50)
    surname: str = Field(min_length=1, max_length=50)


class BookingPax(HBModel):
    roomId: int = Field(ge=1)
    type: Literal["AD", "CH"]
    age: Optional[int] = None
    name: Optional[str] = None
    surname: Optional[str] = None


class BookingRoom(HBModel):
    rateKey: str
    paxes: List[BookingPax] = Field(min_length=1)


class HotelBookingRequest(HBModel):
    """POST /hotel-api/1.0/bookings. `paymentData` only when checkrates said paymentDataRequired."""

    holder: Holder
    rooms: List[BookingRoom] = Field(min_length=1)
    clientReference: str = Field(min_length=1, max_length=20)
    remark: Optional[str] = Field(default=None, max_length=2000)
    tolerance: Optional[float] = Field(default=None, ge=0, le=100)
    language: Optional[str] = None
    creationUser: Optional[str] = None
    voucher: Optional[dict] = None
    paymentData: Optional[dict] = None


class HotelBookingModifyRequest(HBModel):
    mode: Literal["SIMULATION", "UPDATE"]
    booking: dict
    language: Optional[str] = None
    paymentData: Optional[dict] = None
