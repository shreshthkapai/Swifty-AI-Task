"""Complete literal Northstar fixtures used by adapter boundary tests."""


LOCATION_PAYLOAD = {
    "id": "northstar-manchester",
    "name": "Northstar Manchester",
    "town": "Manchester",
    "postcode": "M20 2YY",
    "addressLine": "101 Kingsway",
    "phone": "0161 555 0101",
    "email": "manchester@northstarmotors.example",
    "latitude": 53.424,
    "longitude": -2.231,
    "brands": ["BMW", "MINI", "Volvo"],
}


OPENING_HOURS_PAYLOAD = {
    "dealershipId": "northstar-manchester",
    "weekly": [
        {
            "department": "sales",
            "day": "Monday",
            "dayOfWeek": 0,
            "opensAt": "09:00",
            "closesAt": "18:00",
            "closed": False,
        },
        {
            "department": "service",
            "day": "Monday",
            "dayOfWeek": 0,
            "opensAt": "08:00",
            "closesAt": "17:30",
            "closed": False,
        },
    ],
    "holidayExceptions": [],
}


BUSINESS_INFORMATION_PAYLOAD = {
    "organisation": "Northstar Motors",
    "currency": "GBP",
    "market": "United Kingdom",
    "finance": {"notice": "Finance notice.", "minimumAge": 18},
    "partExchange": {"estimateNotice": "Estimate notice."},
    "privacyContact": "privacy@northstarmotors.example",
}


VEHICLE_PAYLOAD = {
    "id": "veh-003",
    "dealershipId": "northstar-manchester",
    "dealershipName": "Northstar Manchester",
    "dealershipTown": "Manchester",
    "make": "BMW",
    "model": "X3",
    "variant": "xDrive20d M Sport",
    "year": 2024,
    "pricePence": 4299500,
    "monthlyPricePence": 64900,
    "mileage": 8000,
    "fuelType": "Diesel",
    "transmission": "Automatic",
    "colour": "Tanzanite Blue",
    "bodyStyle": "SUV",
    "availability": "available",
    "registration": "MA24 XYZ",
    "description": "A complete vehicle description.",
    "updatedAt": "2026-08-12T10:30:00+00:00",
    "images": ["/assets/vehicles/veh-003.jpg"],
}


OFFER_PAYLOAD = {
    "id": "offer-001",
    "make": "BMW",
    "model": "iX1",
    "title": "BMW iX1 Personal Contract Purchase",
    "productType": "PCP",
    "monthlyPricePence": 49900,
    "upfrontPaymentPence": 299900,
    "apr": 4.9,
    "termMonths": 48,
    "annualMileage": 8000,
    "expiresOn": "2026-12-31",
    "description": "A qualifying new-car offer.",
    "image": "/assets/vehicles/veh-004.jpg",
}


CUSTOMER_PAYLOAD = {
    "firstName": "Jamie",
    "lastName": "Taylor",
    "email": "jamie@example.com",
    "phone": "07700900123",
}


SALES_ENQUIRY_PAYLOAD = {
    "id": "enq-1",
    "reference": "SALE-000001",
    "dealershipId": "northstar-manchester",
    "vehicleId": "veh-003",
    "enquiryType": "part-exchange",
    **CUSTOMER_PAYLOAD,
    "message": "Please contact me about this vehicle.",
    "status": "received",
    "createdAt": "2026-08-12T10:30:00+00:00",
}


TEST_DRIVE_SLOT_PAYLOAD = {
    "id": "td-slot-003-1",
    "dealershipId": "northstar-manchester",
    "vehicleId": "veh-003",
    "startsAt": "2026-08-13T09:00:00+00:00",
    "status": "available",
    "dealershipName": "Northstar Manchester",
    "make": "BMW",
    "model": "X3",
    "variant": "xDrive20d M Sport",
}


TEST_DRIVE_BOOKING_PAYLOAD = {
    "id": "tdb-1",
    "reference": "TEST-000001",
    "slotId": "td-slot-003-1",
    "dealershipId": "northstar-manchester",
    "vehicleId": "veh-003",
    **CUSTOMER_PAYLOAD,
    "notes": "Morning preferred.",
    "status": "confirmed",
    "createdAt": "2026-08-12T10:30:00+00:00",
}


VEHICLE_INTEREST_PAYLOAD = {
    "id": "int-1",
    "reference": "WAIT-000001",
    "dealershipId": "northstar-manchester",
    "vehicleId": "veh-020",
    **CUSTOMER_PAYLOAD,
    "notes": None,
    "status": "registered",
    "createdAt": "2026-08-12T10:30:00+00:00",
}


CALLBACK_PAYLOAD = {
    "id": "call-1",
    "reference": "CALL-000001",
    "dealershipId": "northstar-manchester",
    "department": "sales",
    "vehicleId": "veh-003",
    **CUSTOMER_PAYLOAD,
    "preferredTime": "Tomorrow afternoon",
    "reason": "Discuss vehicle finance.",
    "status": "requested",
    "createdAt": "2026-08-12T10:30:00+00:00",
}


PART_EXCHANGE_PAYLOAD = {
    "id": "px-1",
    "reference": "PX-000001",
    "dealershipId": "northstar-manchester",
    "registration": "AB12 CDE",
    "mileage": 50000,
    "condition": "good",
    **CUSTOMER_PAYLOAD,
    "estimateLowPence": 800000,
    "estimateHighPence": 900000,
    "status": "estimated",
    "createdAt": "2026-08-12T10:30:00+00:00",
    "estimateNotice": "Online estimates are indicative.",
}


WORKSHOP_SERVICE_PAYLOAD = {
    "id": "service-mot",
    "name": "MOT",
    "description": "Annual MOT inspection.",
    "durationMinutes": 60,
    "priceFromPence": 5499,
}


WORKSHOP_SLOT_PAYLOAD = {
    "id": "ws-slot-1",
    "dealershipId": "northstar-manchester",
    "serviceTypeId": "service-mot",
    "startsAt": "2026-08-14T09:00:00+00:00",
    "status": "available",
    "dealershipName": "Northstar Manchester",
    "serviceName": "MOT",
    "durationMinutes": 60,
    "priceFromPence": 5499,
}


WORKSHOP_BOOKING_PAYLOAD = {
    "id": "wsb-1",
    "reference": "WORK-000001",
    "slotId": "ws-slot-1",
    "dealershipId": "northstar-manchester",
    "serviceTypeId": "service-mot",
    "registration": "AB12 CDE",
    "mileage": 50000,
    **CUSTOMER_PAYLOAD,
    "notes": "Check the brakes.",
    "status": "confirmed",
    "createdAt": "2026-08-12T10:30:00+00:00",
    "updatedAt": "2026-08-12T10:30:00+00:00",
    "cancelledAt": None,
}


WORKSHOP_LOOKUP_PAYLOAD = {
    **WORKSHOP_BOOKING_PAYLOAD,
    "startsAt": "2026-08-14T09:00:00+00:00",
    "serviceTypeName": "MOT",
    "dealershipName": "Northstar Manchester",
    "dealershipAddressLine": "101 Kingsway",
    "dealershipTown": "Manchester",
    "dealershipPostcode": "M20 2YY",
}


def platform_error(code: str, *, retryable: bool = False) -> dict[str, object]:
    return {
        "error": {
            "code": code,
            "message": "Safe platform wording.",
            "fieldErrors": {},
            "retryable": retryable,
        }
    }
