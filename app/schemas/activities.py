"""Request models for the Hotelbeds Activities Booking API (field names = Hotelbeds JSON)."""
from datetime import date
from typing import List, Literal, Optional

from pydantic import Field, model_validator

from app.schemas.common import HBModel

FilterType = Literal["destination", "country", "hotel", "giata", "tti", "gps", "factsheet", "service",
                     "service_modality", "segment", "priceFrom", "priceTo", "text"]


class SearchFilterItem(HBModel):
    type: FilterType
    value: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @model_validator(mode="after")
    def _shape(self):
        if self.type == "gps":
            if self.latitude is None or self.longitude is None:
                raise ValueError("gps filter needs latitude and longitude")
        elif not self.value:
            raise ValueError(f"filter '{self.type}' needs a value")
        return self


class FilterGroup(HBModel):
    """Items inside one group are AND-ed; separate groups are OR-ed."""

    searchFilterItems: List[SearchFilterItem] = Field(min_length=1)


class ActivityPax(HBModel):
    age: int = Field(ge=0, le=120)


class Pagination(HBModel):
    itemsPerPage: int = Field(default=100, ge=1, le=100)
    page: int = Field(default=1, ge=1)


class _DateRange(HBModel):
    from_: date = Field(alias="from")
    to: date

    @model_validator(mode="after")
    def _range(self):
        if self.to < self.from_:
            raise ValueError("'to' must be on/after 'from'")
        if (self.to - self.from_).days > 60:
            raise ValueError("Hotelbeds allows at most a 60-day search window")
        return self


class ActivitySearchRequest(_DateRange):
    """POST /activity-api/3.0/activities (search) and /activities/availability (paxes required there)."""

    filters: List[FilterGroup] = Field(min_length=1)
    language: Optional[str] = "en"
    paxes: Optional[List[ActivityPax]] = None
    pagination: Optional[Pagination] = Field(default_factory=Pagination)
    order: Optional[Literal["DEFAULT", "PRICE", "NAME"]] = "DEFAULT"

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "filters": [{"searchFilterItems": [{"type": "destination", "value": "DXB"}]}],
                "from": "2026-12-10", "to": "2026-12-14", "language": "en",
                "paxes": [{"age": 30}, {"age": 30}, {"age": 8}],
                "pagination": {"itemsPerPage": 100, "page": 1}, "order": "DEFAULT",
            }]
        }
    }


class ActivityDetailsRequest(_DateRange):
    """POST /activity-api/3.0/activities/details[/full] — returns the bookable rateKey."""

    code: str
    language: Optional[str] = "en"
    paxes: List[ActivityPax] = Field(min_length=1)
    modalityCode: Optional[str] = None


class ActivityHolder(HBModel):
    name: str
    surname: str
    title: Optional[str] = None
    email: str
    address: Optional[str] = None
    zipCode: Optional[str] = None
    mailing: Optional[bool] = False
    mailUpdDate: Optional[int] = None
    country: Optional[str] = None
    telephones: List[str] = Field(min_length=1)


class BookingActivityPax(HBModel):
    age: int
    name: str
    surname: str
    type: Literal["ADULT", "CHILD"]


class QuestionRef(HBModel):
    code: str
    text: Optional[str] = None
    required: Optional[bool] = None


class Answer(HBModel):
    question: QuestionRef
    answer: str


class BookingActivity(HBModel):
    rateKey: str
    from_: date = Field(alias="from")
    to: date
    session: Optional[str] = None
    preferedLanguage: Optional[str] = None
    serviceLanguage: Optional[str] = None
    paxes: Optional[List[BookingActivityPax]] = None
    answers: Optional[List[Answer]] = None
    comments: Optional[List[dict]] = None


class ActivityBookingRequest(HBModel):
    """PUT /activity-api/3.0/bookings (also used for /preconfirm)."""

    language: str = "en"
    clientReference: str = Field(min_length=1, max_length=20)
    holder: ActivityHolder
    activities: List[BookingActivity] = Field(min_length=1)


class ActivityReconfirmRequest(HBModel):
    language: str = "en"
    reference: str
    clientReference: Optional[str] = None


class ContentMultiCode(HBModel):
    activityCode: str
    modalityCodes: Optional[List[str]] = None


class ActivityContentMultiRequest(HBModel):
    language: str = "en"
    codes: List[ContentMultiCode] = Field(min_length=1, max_length=100)
