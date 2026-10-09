"""Swagger examples for the FIT API (pre-filled in /fit/docs)."""

FIT_MULTI_CITY = {
    "tripInfo": {
        "tripRegions": ["Middle East", "Europe"],
        "tripCountries": ["United Arab Emirates", "Turkey"],
        "startDestinationCity": "Dubai",
        "startDestinationLocation": "Dubai International Airport (DXB)",
        "startDestinationDate": "2026-12-10T14:30:00",
        "endDestinationCity": "Istanbul",
        "endDestinationLocation": "Istanbul Airport (IST)",
        "endDestinationDate": "2026-12-17T18:00:00",
    },
    "travelerInfo": {
        "adults": 2, "children": 1, "childAges": ["8"], "travelerType": "FAMILY", "isImmobile": False,
        "comment": "First family trip abroad", "nationality": "Pakistan",
    },
    "destinations": [
        {
            "destinationIndex": 0,
            "destinationName": "Dubai",
            "travelSegment": {
                "fromDestination": "Karachi", "toDestination": "Dubai", "travelDate": "2026-12-10T14:30:00",
                "modeOfTravel": "FLIGHT", "classOfTravel": "ECONOMY", "needStorage": False, "storageComment": "",
                "arrivalTerminalCode": "DXB",
            },
            "transportDetails": {
                "arprtOrStatnTrsfrReqd": True, "arprtOrStatnTrsfrDt": "2026-12-10T14:30:00", "trsfrType": "PRIVATE",
                "hotelTrsfrReqd": True, "hotelTrsfrDt": "2026-12-14T10:00:00", "hotelTrsfrType": "PRIVATE",
                "luggageComment": "3 large suitcases", "withinCityTransportReqd": False, "withinCityTransports": [],
            },
            "accommodation": {
                "hotelCategory": "4_STAR", "location": "Downtown Dubai", "roomType": "DELUXE", "bedPref": "KING",
                "numberNights": 4, "preferedBrand": "", "comment": "High floor if possible",
                "rooms": 1, "boardBasis": "BB", "refundableOnly": False,
            },
            "excursions": [
                {"excursionDate": "2026-12-11", "excursionType": "ADVENTURE", "excursion": "Desert safari",
                 "dayPart": "EVENING", "adultCount": 2, "childCount": 1, "comment": "With BBQ dinner"},
                {"excursionDate": "2026-12-12", "excursionType": "SIGHTSEEING", "excursion": "Burj Khalifa",
                 "dayPart": "MORNING", "adultCount": 2, "childCount": 1, "comment": ""},
            ],
        },
        {
            "destinationIndex": 1,
            "destinationName": "Istanbul",
            "travelSegment": {
                "fromDestination": "Dubai", "toDestination": "Istanbul", "travelDate": "2026-12-14T14:00:00",
                "modeOfTravel": "FLIGHT", "classOfTravel": "ECONOMY", "needStorage": False, "storageComment": "",
                "arrivalTerminalCode": "IST",
            },
            "transportDetails": {
                "arprtOrStatnTrsfrReqd": True, "arprtOrStatnTrsfrDt": "2026-12-14T14:00:00", "trsfrType": "PRIVATE",
                "hotelTrsfrReqd": True, "hotelTrsfrDt": "2026-12-17T18:00:00", "hotelTrsfrType": "SHARED",
                "luggageComment": "", "withinCityTransportReqd": False, "withinCityTransports": [],
            },
            "accommodation": {
                "hotelCategory": "4_STAR", "location": "Sultanahmet", "roomType": "STANDARD", "bedPref": "DOUBLE",
                "numberNights": 3, "preferedBrand": "", "comment": "", "boardBasis": "BB",
            },
            "excursions": [
                {"excursionDate": "2026-12-15", "excursionType": "NATURE", "excursion": "Bosphorus cruise",
                 "dayPart": "AFTERNOON", "adultCount": 2, "childCount": 1, "comment": ""},
                {"excursionDate": "2026-12-16", "excursionType": "CULTURAL", "excursion": "Hagia Sophia Blue Mosque",
                 "dayPart": "MORNING", "adultCount": 2, "childCount": 1, "comment": "English guide"},
            ],
        },
    ],
    "budget": {
        "range": 6000, "splurgeOn": ["HOTEL"], "optamizeCostOn": ["TRANSFERS"], "planningStyle": "MID_RANGE",
        "decisionTimeline": "WITHIN_WEEK", "currency": "EUR",
    },
    "foodGuideEtc": {
        "mealPref": "HALAL", "mealComment": "", "guideLanguage": "English", "guideComment": "",
        "travelInsuranceReqd": True, "esimReqd": True, "evisaReqd": False,
    },
    "comfortSafety": {"comfortWtCrowd": "MEDIUM", "comfortWtRemote": "LOW", "comfortWtLocalTranspt": "MEDIUM",
                      "comment": ""},
    "refTripComment": "",
    "addnlComment": "Kid-friendly activities preferred",
    "status": "DRAFT",
}

FIT_SINGLE_CITY = {
    "tripInfo": {
        "tripRegions": ["Middle East"], "tripCountries": ["UAE"],
        "startDestinationCity": "Dubai", "startDestinationLocation": "DXB",
        "startDestinationDate": "2026-12-05T09:00:00",
        "endDestinationCity": "Dubai", "endDestinationLocation": "DXB",
        "endDestinationDate": "2026-12-09T21:00:00",
    },
    "travelerInfo": {"adults": 2, "children": 0, "childAges": [], "travelerType": "COUPLE", "isImmobile": False,
                     "comment": "Honeymoon", "nationality": "PK"},
    "destinations": [{
        "destinationIndex": 0, "destinationName": "Dubai",
        "travelSegment": {"fromDestination": "Lahore", "toDestination": "Dubai",
                          "travelDate": "2026-12-05T09:00:00", "modeOfTravel": "FLIGHT", "classOfTravel": "BUSINESS"},
        "transportDetails": {"arprtOrStatnTrsfrReqd": True, "arprtOrStatnTrsfrDt": "2026-12-05T09:00:00",
                             "trsfrType": "LUXURY", "hotelTrsfrReqd": True, "hotelTrsfrDt": "2026-12-09T21:00:00",
                             "hotelTrsfrType": "LUXURY"},
        "accommodation": {"hotelCategory": "5_STAR", "location": "Dubai Marina", "roomType": "SUITE",
                          "bedPref": "KING", "numberNights": 4, "boardBasis": "HB"},
        "excursions": [{"excursionDate": "2026-12-07", "excursionType": "CULINARY", "excursion": "Dhow cruise dinner",
                        "dayPart": "EVENING", "adultCount": 2, "childCount": 0}],
    }],
    "budget": {"range": 5000, "splurgeOn": ["HOTEL", "ACTIVITIES"], "optamizeCostOn": [],
               "planningStyle": "LUXURY", "decisionTimeline": "IMMEDIATE", "currency": "EUR"},
    "foodGuideEtc": {"mealPref": "HALAL", "guideLanguage": "English", "travelInsuranceReqd": False,
                     "esimReqd": False, "evisaReqd": False},
    "comfortSafety": {"comfortWtCrowd": "LOW", "comfortWtRemote": "MEDIUM", "comfortWtLocalTranspt": "LOW"},
    "addnlComment": "Romantic experiences",
    "status": "SUBMITTED",
}

CONFIRM_EXAMPLE = {
    "holder": {"title": "MR", "firstName": "Ahmed", "lastName": "Khan", "email": "ahmed.khan@example.com",
               "phone": "+923001234567", "country": "PK", "address": "Clifton Block 5", "zipCode": "75600"},
    "travellers": [
        {"type": "ADULT", "firstName": "Ahmed", "lastName": "Khan", "age": 38},
        {"type": "ADULT", "firstName": "Sara", "lastName": "Khan", "age": 35},
        {"type": "CHILD", "firstName": "Ali", "lastName": "Khan", "age": 8},
    ],
    "transportCodes": [
        {"destinationIndex": 0, "direction": "ARRIVAL", "code": "EK601", "companyName": "Emirates"},
        {"destinationIndex": 0, "direction": "DEPARTURE", "code": "TK763", "companyName": "Turkish Airlines"},
        {"destinationIndex": 1, "direction": "ARRIVAL", "code": "TK763", "companyName": "Turkish Airlines"},
        {"destinationIndex": 1, "direction": "DEPARTURE", "code": "TK708", "companyName": "Turkish Airlines"},
    ],
    "activityAnswers": [],
    "selection": {"hotelOptionIds": {}, "excludeItemIds": [], "includeSuggestedItemIds": []},
    "remark": "Family with one child",
    "acceptPriceChanges": False,
    "bookingMode": "ALL_OR_NOTHING",
}
