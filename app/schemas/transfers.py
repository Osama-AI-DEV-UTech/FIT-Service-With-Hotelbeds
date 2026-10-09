"""Request models for the Hotelbeds Transfers Booking API (field names = Hotelbeds JSON)."""
from typing import List, Literal, Optional

from pydantic import Field, field_validator

from app.schemas.common import HBModel

LocationType = Literal["IATA", "ATLAS", "GPS", "PORT", "STATION", "GIATA"]


class TransferRoute(HBModel):
    id: str = Field(description="fromType/fromCode/toType/toCode, e.g. IATA/PMI/ATLAS/1523")
    dateTime: str = Field(description="YYYY-MM-DDThh:mm:ss (local time)")


class TransferHolder(HBModel):
    name: str
    surname: str
    email: str
    phone: str = Field(description="E.164, e.g. +971501234567")

    @field_validator("phone")
    @classmethod
    def _e164(cls, v: str) -> str:
        digits = v.replace(" ", "").replace("-", "")
        if not digits.startswith("+") or not digits[1:].isdigit() or not 8 <= len(digits) <= 16:
            raise ValueError("phone must be E.164 (e.g. +971501234567)")
        return digits


class TransferDetail(HBModel):
    type: Literal["FLIGHT", "CRUISE", "TRAIN"]
    direction: Literal["ARRIVAL", "DEPARTURE"]
    code: str = Field(min_length=1, max_length=50)
    companyName: Optional[str] = Field(default=None, max_length=100)


class LocationInformation(HBModel):
    name: str = Field(max_length=300)
    address: str = Field(max_length=300)
    town: str = Field(max_length=300)
    country: str = Field(max_length=300)
    zip: str = Field(max_length=15)


class TransferExtra(HBModel):
    code: str
    units: str = "1"


class BookingTransfer(HBModel):
    rateKey: str
    transferDetails: List[TransferDetail] = Field(min_length=1)
    pickupInformation: Optional[LocationInformation] = None
    dropoffInformation: Optional[LocationInformation] = None
    extras: Optional[List[TransferExtra]] = None


class TransferBookingRequest(HBModel):
    language: str = "en"
    holder: TransferHolder
    transfers: List[BookingTransfer] = Field(min_length=1)
    clientReference: Optional[str] = None
    welcomeMessage: Optional[str] = None
    remark: Optional[str] = Field(default=None, max_length=2000)


class TransferAmendRequest(HBModel):
    """PUT amendment — holder / transferDetails / pickupTime / location changes, combinable."""

    reference: str
    language: str = "en"
    holder: Optional[dict] = None
    transfers: Optional[List[dict]] = None
