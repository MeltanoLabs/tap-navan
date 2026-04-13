"""Stream type classes for tap-navan."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from singer_sdk import typing as th
from singer_sdk.exceptions import FatalAPIError

from tap_navan.client import NavanPageNumberPaginator, NavanStream

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override

if TYPE_CHECKING:
    from singer_sdk.helpers.types import Context


# ---------------------------------------------------------------------------
# UsersStream
# ---------------------------------------------------------------------------


class UsersStream(NavanStream):
    """Stream for Navan users, sourced from GET /v1/users.

    Returns all company users including their department, cost centre, and
    policy level — useful for enriching expense records with employee metadata.
    """

    name = "users"
    path = "/v1/users"
    primary_keys = ("id",)
    replication_key = None  # Full sync; Navan has no user-level change filter.

    schema = th.PropertiesList(
        th.Property("id", th.StringType, description="Navan UUID for this user"),
        th.Property("userName", th.StringType, description="Primary login email"),
        th.Property(
            "name",
            th.ObjectType(
                th.Property("familyName", th.StringType),
                th.Property("givenName", th.StringType),
            ),
        ),
        th.Property("employeeId", th.StringType, description="HR / HRIS employee ID"),
        th.Property("phoneNumber", th.StringType),
        th.Property("department", th.StringType),
        th.Property("costCentre", th.StringType),
        th.Property("region", th.StringType),
        th.Property("subsidiary", th.StringType),
        th.Property("approver", th.StringType, description="Approver email"),
        th.Property("approverName", th.StringType),
        th.Property("managerName", th.StringType),
        th.Property("managerEmail", th.StringType),
        th.Property("active", th.BooleanType),
        th.Property(
            "emails",
            th.ArrayType(
                th.ObjectType(
                    th.Property("value", th.StringType),
                    th.Property("primary", th.BooleanType),
                    th.Property("emailType", th.StringType),
                )
            ),
        ),
        th.Property("legalEntityName", th.StringType),
        th.Property("companyOffice", th.StringType),
        th.Property(
            "policyLevel",
            th.StringType,
            description="CUSTOM, DEFAULT, DIRECTOR, or EXECUTIVE",
        ),
        th.Property("policyName", th.StringType),
    ).to_dict()

    @override
    def get_records(self, context: Context | None) -> Any:
        """Yield records, skipping gracefully on 403 Forbidden.

        TMC credentials may only have ``bookings:read`` scope. If the users
        endpoint responds with 403, log a warning and yield nothing rather
        than raising a fatal error.

        Args:
            context: The stream context.

        Yields:
            Each user record from the source.
        """
        try:
            yield from super().get_records(context)
        except FatalAPIError as exc:
            if "403" in str(exc):
                self.logger.warning(
                    "GET /v1/users returned 403 Forbidden. "
                    "Ensure the client credentials include the 'users:read' "
                    "scope. Skipping stream."
                )
                return
            raise


# ---------------------------------------------------------------------------
# BookingsStream
# ---------------------------------------------------------------------------


class BookingsStream(NavanStream):
    """Stream for Navan travel bookings, sourced from GET /v1/bookings.

    Returns all booking segments (flights, hotels, rail, cars, black cars)
    associated with the configured company. Supports incremental extraction
    using ``start_date`` / ``created`` as the replication key.

    The ``createdFrom`` / ``createdTo`` query parameters (epoch seconds) bound
    the sync window. ``createdTo`` defaults to the current time and is always
    required by the API when ``createdFrom`` is provided.

    The ``includeTransactions`` beta parameter is enabled so that payment
    transaction details appear in each booking record.
    """

    name = "bookings"
    path = "/v1/bookings"
    primary_keys = ("uuid",)
    replication_key = "created"

    schema = th.PropertiesList(
        # ---------------------------------------------------------------
        # Core identity
        # ---------------------------------------------------------------
        th.Property("uuid", th.StringType, description="Navan UUID for this booking"),
        th.Property("bookingId", th.StringType, description="TMC-assigned booking ID"),
        th.Property(
            "bookingStatus",
            th.StringType,
            description=(
                "UNKNOWN, CANCELED, VOIDED, REJECTED_BY_PROVIDER, "
                "CONFIRMED, TICKETED, or ACCEPTED"
            ),
        ),
        th.Property(
            "bookingType",
            th.StringType,
            description="FLIGHT, HOTEL, RAIL, CAR, or BLACK_CAR",
        ),
        th.Property("tripName", th.StringType),
        th.Property("confirmationNumber", th.StringType),
        th.Property(
            "created",
            th.DateTimeType,
            description="Booking creation timestamp — replication key",
        ),
        th.Property("lastModified", th.DateTimeType),
        th.Property("pcc", th.StringType, description="Pseudo City Code"),
        th.Property(
            "bookingMethod",
            th.StringType,
            description="ONLINE, OFFLINE, AGENT, etc.",
        ),
        th.Property("inventorySource", th.StringType),
        th.Property("inventory", th.StringType),
        th.Property("invoiceNumber", th.StringType),
        # ---------------------------------------------------------------
        # Trip / segment dates
        # ---------------------------------------------------------------
        th.Property("startDate", th.DateTimeType),
        th.Property("endDate", th.DateTimeType),
        th.Property("bookingDuration", th.IntegerType, description="Duration in nights or days"),
        th.Property(
            "tripLength",
            th.StringType,
            description="e.g. 'Long haul', 'Short haul'",
        ),
        th.Property("tripDescription", th.StringType),
        th.Property("tripUuids", th.ArrayType(th.StringType)),
        th.Property("leadTimeInDays", th.IntegerType),
        th.Property("domestic", th.BooleanType),
        # ---------------------------------------------------------------
        # Booking type / route
        # ---------------------------------------------------------------
        th.Property(
            "routeType",
            th.StringType,
            description="ONE_WAY, ROUND_TRIP, MULTI_CITY, etc.",
        ),
        th.Property("airlineRoute", th.StringType),
        th.Property("carType", th.StringType),
        th.Property("flight", th.BooleanType),
        # ---------------------------------------------------------------
        # Vendor / rate info
        # ---------------------------------------------------------------
        th.Property("vendor", th.StringType),
        th.Property("preferredVendor", th.BooleanType),
        th.Property("corporateDiscountUsed", th.BooleanType),
        th.Property("gsaRate", th.BooleanType),
        th.Property("cnr", th.ObjectType()),
        # ---------------------------------------------------------------
        # Cabin / class
        # ---------------------------------------------------------------
        th.Property("cabin", th.StringType, description="Requested cabin class"),
        th.Property("cabinPurchased", th.StringType, description="Cabin class actually ticketed"),
        th.Property("flownCabinClass", th.StringType),
        th.Property("fareClass", th.StringType),
        th.Property("fareBasisCode", th.StringType),
        # ---------------------------------------------------------------
        # Cancellation
        # ---------------------------------------------------------------
        th.Property("cancelledAt", th.DateTimeType),
        th.Property("cancellationReason", th.StringType),
        th.Property("maxCancellationLoss", th.NumberType),
        # ---------------------------------------------------------------
        # People
        # ---------------------------------------------------------------
        th.Property("numberOfPassengers", th.IntegerType),
        th.Property(
            "booker",
            th.ObjectType(
                th.Property("uuid", th.StringType),
                th.Property("email", th.StringType),
                th.Property("name", th.StringType),
                th.Property("firstName", th.StringType),
                th.Property("lastName", th.StringType),
                th.Property("employeeId", th.StringType),
                th.Property("department", th.StringType),
                th.Property("costCenter", th.StringType),
            ),
        ),
        th.Property("departments", th.ArrayType(th.StringType)),
        th.Property("costCenters", th.ArrayType(th.StringType)),
        th.Property("regions", th.ArrayType(th.StringType)),
        th.Property("subsidiaries", th.ArrayType(th.StringType)),
        th.Property("billableEntities", th.ArrayType(th.StringType)),
        th.Property("loggedAsUserName", th.StringType),
        # ---------------------------------------------------------------
        # Locations
        # ---------------------------------------------------------------
        th.Property(
            "origin",
            th.ObjectType(
                th.Property("countryCode", th.StringType),
                th.Property("state", th.StringType),
                th.Property("city", th.StringType),
                th.Property("airportCode", th.StringType),
            ),
        ),
        th.Property(
            "destination",
            th.ObjectType(
                th.Property("countryCode", th.StringType),
                th.Property("state", th.StringType),
                th.Property("city", th.StringType),
                th.Property("airportCode", th.StringType),
            ),
        ),
        th.Property("hotelCode", th.StringType),
        th.Property("hotelChain", th.StringType),
        th.Property("hotelLatitude", th.NumberType),
        th.Property("hotelLongitude", th.NumberType),
        # ---------------------------------------------------------------
        # Segments (legs of the journey)
        # ---------------------------------------------------------------
        th.Property("segments", th.ArrayType(th.ObjectType())),
        th.Property("etickets", th.ArrayType(th.StringType)),
        th.Property("invoice", th.StringType),
        th.Property("pdf", th.StringType),
        th.Property(
            "seats",
            th.ArrayType(th.StringType),
            description="Seat identifiers, e.g. '8C'",
        ),
        th.Property("credit", th.ObjectType()),
        th.Property("reshopping", th.ObjectType()),
        # ---------------------------------------------------------------
        # Financial — core amounts (for expense management)
        # ---------------------------------------------------------------
        th.Property("currency", th.StringType, description="ISO 4217 currency code"),
        th.Property("saving", th.NumberType),
        th.Property("grandTotal", th.NumberType, description="Total charged to the traveler"),
        th.Property("usdGrandTotal", th.NumberType, description="Grand total converted to USD"),
        th.Property("travelSpend", th.NumberType),
        th.Property("basePrice", th.NumberType),
        th.Property("unitaryPrice", th.NumberType, description="Per-unit price"),
        th.Property("optimalPrice", th.NumberType),
        th.Property("priceBenchmark", th.NumberType),
        th.Property("savingMissed", th.NumberType),
        th.Property(
            "currencyExhangeRateFromUsd",  # Note: API typo — "exhange" not "exchange"
            th.NumberType,
        ),
        # ---------------------------------------------------------------
        # Financial — fees and taxes
        # ---------------------------------------------------------------
        th.Property("tax", th.NumberType),
        th.Property("vat", th.NumberType),
        th.Property("gst", th.NumberType),
        th.Property("hst", th.NumberType),
        th.Property("qst", th.NumberType),
        th.Property("resortFee", th.NumberType),
        th.Property("tripFee", th.NumberType),
        th.Property("handlingFees", th.NumberType),
        th.Property("transactionFees", th.NumberType),
        th.Property("invoiceCollectionFees", th.NumberType),
        th.Property("bookingFee", th.NumberType),
        th.Property("vipFee", th.NumberType),
        th.Property("navanPro", th.BooleanType),
        th.Property("seatsFee", th.NumberType),
        th.Property("extrasFees", th.NumberType),
        th.Property("airlineCreditCardSurcharge", th.NumberType),
        th.Property("travelAgentRequestFee", th.NumberType),
        th.Property("exchangeAmount", th.NumberType),
        th.Property("exchangeFee", th.NumberType),
        th.Property("netCharge", th.NumberType),
        th.Property("paymentSchedule", th.StringType, description="e.g. 'NOW', 'LATER'"),
        # ---------------------------------------------------------------
        # Financial — payment method
        # ---------------------------------------------------------------
        th.Property(
            "paymentMethod",
            th.StringType,
            description="e.g. 'VISA 1235'",
        ),
        th.Property("nameOnCreditCard", th.StringType),
        th.Property("paymentMethodUsed", th.StringType),
        th.Property("paymentCreditCardTypeName", th.StringType),
        th.Property("companyPaymentMethod", th.StringType),
        th.Property("statementDescription", th.StringType),
        # ---------------------------------------------------------------
        # Policy / approval
        # ---------------------------------------------------------------
        th.Property("policyLevel", th.StringType),
        th.Property("outOfPolicy", th.BooleanType),
        th.Property("outOfPolicyDescription", th.StringType),
        th.Property(
            "outOfPolicyViolations",
            th.ArrayType(th.StringType),
            description="Normalized to [] when the API returns an empty string.",
        ),
        th.Property(
            "outOfPolicyViolationTypes",
            th.ArrayType(th.StringType),
            description="Normalized to [] when the API returns an empty string.",
        ),
        th.Property("maxPricePolicy", th.NumberType),
        th.Property("approvalStatus", th.StringType),
        th.Property("approverEmail", th.StringType),
        th.Property("approverReason", th.StringType),
        th.Property("approvalChangedAt", th.DateTimeType),
        # ---------------------------------------------------------------
        # Expense categorization
        # ---------------------------------------------------------------
        th.Property("expensed", th.BooleanType),
        th.Property("purpose", th.StringType),
        th.Property("companyOffice", th.StringType),
        th.Property("projects", th.StringType),
        th.Property("billToClient", th.StringType),
        th.Property("reason", th.StringType),
        th.Property(
            "passengers",
            th.ArrayType(th.ObjectType()),
            description="Passenger/traveler details",
        ),
        th.Property(
            "customFields",
            th.ArrayType(
                th.ObjectType(
                    th.Property("name", th.StringType),
                    th.Property("value", th.StringType),
                )
            ),
        ),
        # ---------------------------------------------------------------
        # Sustainability / miles
        # ---------------------------------------------------------------
        th.Property("flightMiles", th.NumberType),
        th.Property("trainMiles", th.NumberType),
        th.Property("carbonEmissions", th.NumberType),
        th.Property("carbonOffsetCost", th.NumberType),
        # ---------------------------------------------------------------
        # Rewards
        # ---------------------------------------------------------------
        th.Property("tripBucksEarned", th.NumberType),
        th.Property("tripBucksEarnedUsd", th.NumberType),
        th.Property("ppbPointsBurned", th.NumberType),
        # ---------------------------------------------------------------
        # Event / group travel
        # ---------------------------------------------------------------
        th.Property("eventName", th.StringType),
        th.Property("inviteConnectionMethod", th.StringType),
        th.Property("invitationType", th.StringType),
        # ---------------------------------------------------------------
        # Transaction details (beta: includeTransactions=true)
        # ---------------------------------------------------------------
        th.Property(
            "transactions",
            th.ArrayType(
                th.ObjectType(
                    th.Property("transactionId", th.StringType),
                    th.Property("amount", th.NumberType),
                    th.Property("currency", th.StringType),
                    th.Property("transactionDate", th.DateTimeType),
                    th.Property("description", th.StringType),
                )
            ),
        ),
    ).to_dict()

    @override
    def get_new_paginator(self) -> NavanPageNumberPaginator:
        """Create a new pagination helper instance.

        Returns:
            A Navan page-number paginator starting at page 0.
        """
        return NavanPageNumberPaginator(start_value=0)

    @override
    def get_url_params(
        self,
        context: Context | None,
        next_page_token: Any | None,
    ) -> dict[str, Any]:
        """Return URL parameters for the bookings endpoint.

        Converts the Singer SDK replication-key bookmark to an epoch-second
        ``createdFrom`` value. ``createdTo`` (current time) is always included
        because the Navan API requires it whenever ``createdFrom`` is set.

        Args:
            context: The stream context.
            next_page_token: The current page number.

        Returns:
            A dictionary of URL query parameters.
        """
        now = datetime.now(tz=timezone.utc)

        params: dict[str, Any] = {
            "page": next_page_token if next_page_token is not None else 0,
            "size": 100,
            # Include payment transaction details for expense matching.
            "includeTransactions": "true",
            # createdTo is always required when createdFrom is set.
            "createdTo": int(now.timestamp()),
        }

        start = self.get_starting_timestamp(context)
        if start is not None:
            # Navan expects epoch seconds (integer).
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            params["createdFrom"] = int(start.timestamp())

        return params

    @override
    def post_process(
        self,
        row: dict,
        context: Context | None = None,
    ) -> dict | None:
        """Normalize fields with inconsistent types in the Navan API response.

        The API returns an empty string ``""`` instead of ``[]`` for list
        fields when there are no values (e.g. ``outOfPolicyViolations``).
        Normalize these to proper empty lists so the schema remains valid.

        Args:
            row: An individual record from the stream.
            context: The stream context.

        Returns:
            The normalized record dictionary.
        """
        for field in ("outOfPolicyViolations", "outOfPolicyViolationTypes"):
            if isinstance(row.get(field), str):
                row[field] = [row[field]] if row[field] else []
        return row
