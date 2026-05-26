"""Navan Expense Partner API stream classes.

Targets the production Expense Partner API rooted at
``{api_url}/v1/expense/`` (see "Navan Expense Partner API Quick Start
Guide", v1.0). One stream per transaction type, plus a receipts stream:

- ``card_transactions``       — ``/v1/expense/card-transactions``
- ``connect_transactions``    — ``/v1/expense/connect-transactions``
- ``manual_transactions``     — ``/v1/expense/manual-transactions``
- ``repayments``              — ``/v1/expense/repayments``
- ``fees``                    — ``/v1/expense/fees``
- ``adjustments``             — ``/v1/expense/adjustments``
- ``daily_rebates``           — ``/v1/expense/daily-rebates``
- ``disputes``                — ``/v1/expense/disputes``
- ``receipts``                — ``/v1/expense/transactions/receipts``

All transaction streams share one base class and one schema. The schema
asks the API for every documented field group via
``include_field_group=POLICY,ERP,TAX,BOOKING,MILEAGE,TRAVELER_HR`` so
warehouse columns are stable regardless of customer field-group
preferences.

Key API behaviors handled here:

- **Cursor pagination.** Response envelope is
  ``{"content": [...], "next_cursor": str | None, "has_next": bool,
  "has_previous": bool, "page_size": int, "content_size": int}``.
- **Date ranges, not single days.** Use ``date_modified.from`` /
  ``date_modified.to`` (max 93-day window, must be on or after the
  first day of the month two months prior). ``request_records`` walks
  93-day windows if the bookmark-to-today span is larger.
- **Custom fields are first-class.** ``custom_field_values`` is an
  array of ``{name, label, value, displayValue}`` — no post_process
  packing required.
- **Money is a string.** ``posted_amount``, ``original_amount``, etc.
  arrive as strings (``"3.30"``) to preserve precision. Typed as
  ``th.StringType``; dbt casts downstream.
- **EU customers** need ``X-ta-region: EU`` on every request or the
  API returns 500.
- **403 graceful skip** for tenants without Expense API access.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, ClassVar

from singer_sdk import typing as th
from singer_sdk.exceptions import FatalAPIError
from singer_sdk.pagination import BaseAPIPaginator

from tap_navan.client import NavanStream

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date

    import requests
    from singer_sdk.helpers.types import Context


# Page size — API caps at 500, default 100. Larger reduces round trips.
PAGE_SIZE = 500

# Hard limit on a single API call's date span.
MAX_WINDOW_DAYS = 93

# All field groups — included by default for stable warehouse columns.
ALL_FIELD_GROUPS = "POLICY,ERP,TAX,BOOKING,MILEAGE,TRAVELER_HR"

# Lookback applied to the bookmark to recapture late edits. The API docs
# warn that updates may take up to 4 hours to surface; one day is ample.
LOOKBACK_DAYS = 1


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class NavanExpenseCursorPaginator(BaseAPIPaginator[str | None]):
    """Cursor paginator for the Navan Expense API.

    Reads ``next_cursor`` from the JSON envelope and stops when
    ``has_next`` is False (or the cursor is missing). Returning ``None``
    from ``get_next`` is what the Singer SDK actually uses to halt;
    ``has_more`` is overridden for defensive double-checking and
    cleaner stop semantics.
    """

    def __init__(self) -> None:
        """Start with a null cursor — the first request omits the param."""
        super().__init__(start_value=None)

    @override
    def get_next(self, response: requests.Response) -> str | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        if not payload.get("has_next", False):
            return None
        return payload.get("next_cursor")

    @override
    def has_more(self, response: requests.Response) -> bool:
        try:
            payload = response.json()
        except ValueError:
            return False
        return bool(payload.get("has_next", False))


# ---------------------------------------------------------------------------
# Shared transaction schema
# ---------------------------------------------------------------------------
#
# Sourced from live API responses against a real tenant plus the
# Quick Start Guide's "Default Transaction Fields" appendix. Money
# fields are StringType because the API returns them as strings to
# preserve decimal precision (e.g. "3.30").
#
# Nested objects (``booking_details``, ``tax_details``, ``line_items``)
# are typed as ``ObjectType(additional_properties=AnyType())`` /
# ``ArrayType(ObjectType(additional_properties=AnyType()))`` so the
# Singer SDK passes them through without stripping sub-fields. This
# escape hatch is documented in tap-navan's stream-authoring notes:
# it works because the parent path is at the top level of properties.

_BOOKING_DETAILS = th.ObjectType(
    th.Property("hotel_name", th.StringType),
    th.Property("booking_start_date", th.DateType),
    th.Property("booking_end_date", th.DateType),
    th.Property("flight_origin_airport", th.StringType),
    th.Property("flight_destination_airport", th.StringType),
    th.Property("flight_cabin", th.StringType),
    # Distances come back as strings ("1267.00") for precision.
    th.Property("flight_miles", th.StringType),
    th.Property("train_miles", th.StringType),
    th.Property("booking_status", th.StringType),
    th.Property("bookers", th.StringType),
    th.Property("related_travelers", th.StringType),
    th.Property("traveler_employee_ids", th.StringType),
    th.Property("trip_uuid", th.StringType),
    th.Property("trip_name", th.StringType),
    th.Property("trip_purpose", th.StringType),
    th.Property("navan_booking_ids", th.StringType),
    th.Property("navan_booking_uuids", th.StringType),
    th.Property("e_tickets", th.StringType),
)

_CUSTOM_FIELD_VALUE = th.ObjectType(
    th.Property("name", th.StringType),
    th.Property("label", th.StringType),
    th.Property("value", th.StringType),
    # API returns camelCase here (`displayValue`), not snake_case.
    # Matching API exactly so Singer SDK does not strip the field.
    th.Property("displayValue", th.StringType),
)


EXPENSE_TRANSACTION_SCHEMA = th.PropertiesList(
    # Core identity
    th.Property("id", th.StringType, required=True),
    th.Property("source_id", th.StringType),
    th.Property("_type", th.StringType, description="Type discriminator"),
    th.Property("transaction_type", th.StringType),
    th.Property("activity_type", th.StringType),
    th.Property("activity_description", th.StringType),
    # Dates
    th.Property("authorization_date", th.DateType),
    th.Property("posted_date", th.DateType),
    th.Property("posted_date_time", th.DateTimeType),
    th.Property("transaction_date", th.DateType),
    th.Property("invoice_date", th.DateType),
    th.Property("last_approver_action_date", th.DateType),
    th.Property("erp_effective_date", th.DateType),
    th.Property("manually_added_date", th.DateType),
    th.Property("reimbursement_date", th.DateType),
    th.Property("modified_timestamp", th.DateTimeType, description="Replication key"),
    # Amounts — strings to preserve precision
    th.Property("posted_amount", th.StringType),
    th.Property("posted_currency", th.StringType),
    th.Property("original_amount", th.StringType),
    th.Property("original_currency", th.StringType),
    th.Property("personal_amount", th.StringType),
    th.Property("rejected_amount", th.StringType),
    th.Property("accrued_rebate_amount", th.NumberType),
    th.Property("billable_entity_amount", th.StringType),
    th.Property("billable_entity_currency", th.StringType),
    th.Property("reimbursement_amount", th.StringType),
    th.Property("reimbursement_currency", th.StringType),
    th.Property("reimbursement_method", th.StringType),
    th.Property("reimbursement_exchange_rate", th.NumberType),
    th.Property("exchange_rate", th.NumberType),
    th.Property("fee", th.StringType),
    th.Property("fx_fee_amount", th.StringType),
    th.Property("direct_reimbursement_fee_amount", th.StringType),
    th.Property("rebate_type", th.StringType),
    # Cardholder / card
    th.Property("cardholder", th.StringType),
    th.Property("cardholder_email", th.StringType),
    th.Property("employee_id", th.StringType),
    th.Property("card_description", th.StringType),
    th.Property("card_program_name", th.StringType, description="Connect transactions"),
    # Vendor / merchant
    th.Property("vendor_name", th.StringType),
    th.Property("vendor_address", th.StringType),
    th.Property("vendor_category", th.StringType),
    # Approval
    th.Property("approver_type", th.StringType),
    th.Property("approved_by_email", th.StringType),
    th.Property("approval_status", th.StringType),
    # Policy
    th.Property("policy", th.StringType),
    th.Property("policy_category", th.StringType),
    # GL / ERP
    th.Property("gl_code_number", th.StringType),
    th.Property("gl_code_name", th.StringType),
    th.Property("erp_sync_status", th.StringType),
    th.Property("statement_id", th.StringType),
    th.Property("invoice_number", th.StringType),
    # Reporting / HR
    th.Property("department", th.StringType),
    th.Property("cost_center", th.StringType),
    th.Property("region", th.StringType),
    th.Property("subsidiary", th.StringType),
    th.Property("legal_entity", th.StringType),
    # Traveler HR (TRAVELER_HR field group; excluded by default API-side)
    th.Property("traveler_department", th.StringType),
    th.Property("traveler_cost_center", th.StringType),
    th.Property("traveler_region", th.StringType),
    th.Property("traveler_subsidiary", th.StringType),
    # Flags
    th.Property("flagged", th.BooleanType),
    th.Property("flag_status", th.StringType),
    th.Property("flag_reasons", th.StringType),
    th.Property("personal", th.BooleanType),
    # Receipts (top-level URLs)
    th.Property("receipt", th.StringType),
    th.Property("e_receipt", th.StringType),
    th.Property("receipt_navan", th.StringType),
    th.Property("e_receipt_navan", th.StringType),
    # Participants — comma-separated string in practice
    th.Property("participants", th.StringType),
    th.Property("transaction_description", th.StringType),
    # Mileage (MILEAGE field group)
    th.Property("distance_unit", th.StringType),
    th.Property("distance_amount", th.StringType),
    # Itemization (LINE_ITEM field group)
    th.Property("itemized", th.BooleanType),
    th.Property("line_items", th.ArrayType(th.ObjectType(additional_properties=th.AnyType()))),
    # Tax (TAX field group)
    th.Property("tax_details", th.ObjectType(additional_properties=th.AnyType())),
    # Booking detail (BOOKING field group)
    th.Property("booking_details", _BOOKING_DETAILS),
    # Custom fields (first-class array)
    th.Property("custom_field_values", th.ArrayType(_CUSTOM_FIELD_VALUE)),
    # Connect-specific
    th.Property(
        "reporting_transaction_id",
        th.StringType,
        description="Connect — Visa/Mastercard txn ref",
    ),
    th.Property(
        "vcf_transaction_reference_number",
        th.StringType,
        description="Connect — DEPRECATED, use reporting_transaction_id",
    ),
).to_dict()


# ---------------------------------------------------------------------------
# Base class for the 8 transaction streams
# ---------------------------------------------------------------------------


class NavanExpenseStream(NavanStream):
    """Base class for /v1/expense/<type> streams.

    Subclasses set ``name`` and ``path``. All pagination, incremental
    windowing, and schema concerns live here.
    """

    # API envelope wraps records in `content`, not `data`.
    records_jsonpath = "$.content[*]"

    primary_keys: ClassVar[tuple[str, ...]] = ("id",)
    # Annotated as Optional so ReceiptsStream can override to None
    # (FULL_TABLE — presigned URL signatures change every call).
    replication_key: ClassVar[str | None] = "modified_timestamp"

    schema = EXPENSE_TRANSACTION_SCHEMA

    # --- HTTP headers (EU customers need X-ta-region) --------------------

    @property
    @override
    def http_headers(self) -> dict:
        headers = dict(super().http_headers)
        api_url = (self.config.get("api_url") or "").lower()
        if "app-fra" in api_url or "fra.navan" in api_url:
            headers["X-ta-region"] = "EU"
        return headers

    # --- Pagination ------------------------------------------------------

    @override
    def get_new_paginator(self) -> NavanExpenseCursorPaginator:
        return NavanExpenseCursorPaginator()

    # --- URL params ------------------------------------------------------

    @override
    def get_url_params(
        self,
        context: Context | None,
        next_page_token: Any | None,
    ) -> dict[str, Any]:
        """Build query params for one page of one date window.

        ``date_modified.from`` / ``date_modified.to`` come from the
        per-window context injected by :py:meth:`request_records`.
        ``cursor`` advances within the window. ``include_field_group``
        opts into every documented field group so the schema is stable.
        """
        params: dict[str, Any] = {
            "page_size": PAGE_SIZE,
            "include_field_group": ALL_FIELD_GROUPS,
        }
        if context is not None:
            if "date_modified_from" in context:
                params["date_modified.from"] = context["date_modified_from"]
            if "date_modified_to" in context:
                params["date_modified.to"] = context["date_modified_to"]
        if next_page_token:
            params["cursor"] = next_page_token
        return params

    # --- Incremental windowing -------------------------------------------

    @override
    def request_records(self, context: Context | None) -> Iterable[dict]:
        """Walk 93-day date_modified windows from bookmark through today.

        The API caps a single call's date span at 93 days and requires
        the start to be on or after the first day of the month two
        months prior to today. ``_compute_start_day`` clamps to that
        floor.
        """
        start_day = self._compute_start_day(context)
        end_day = datetime.now(tz=timezone.utc).date()

        if start_day > end_day:
            self.logger.info(
                "Stream %s: bookmark %s is past today (%s); nothing to fetch.",
                self.name,
                start_day.isoformat(),
                end_day.isoformat(),
            )
            return

        self.logger.info(
            "Stream %s: walking date_modified %s through %s in <=%d-day windows.",
            self.name,
            start_day.isoformat(),
            end_day.isoformat(),
            MAX_WINDOW_DAYS,
        )

        window_start = start_day
        while window_start <= end_day:
            window_end = min(
                window_start + timedelta(days=MAX_WINDOW_DAYS - 1),
                end_day,
            )
            window_context: dict[str, Any] = dict(context or {})
            window_context["date_modified_from"] = window_start.isoformat()
            window_context["date_modified_to"] = window_end.isoformat()
            try:
                yield from super().request_records(window_context)
            except FatalAPIError as exc:
                if "403" in str(exc):
                    self.logger.warning(
                        "Stream %s: 403 Forbidden on window %s..%s. "
                        "Verify the tenant is enabled for the Expense API. "
                        "Skipping remaining windows.",
                        self.name,
                        window_start.isoformat(),
                        window_end.isoformat(),
                    )
                    return
                raise
            window_start = window_end + timedelta(days=1)

    # --- Helpers ---------------------------------------------------------

    def _compute_start_day(self, context: Context | None) -> date:
        """Resolve the first UTC date to fetch.

        Precedence:
        1. Singer state bookmark minus ``LOOKBACK_DAYS``.
        2. The tap's ``start_date`` config.
        3. Seven days ago, as a defensive fallback.

        Clamped to the API floor: first day of the month two months
        prior to today UTC. Dates older than that are silently
        non-retrievable.
        """
        bookmark = self.get_starting_timestamp(context)
        candidate: date
        if bookmark is not None:
            if bookmark.tzinfo is None:
                bookmark = bookmark.replace(tzinfo=timezone.utc)
            candidate = bookmark.date() - timedelta(days=LOOKBACK_DAYS)
        else:
            configured = self.config.get("start_date")
            if configured:
                parsed = (
                    datetime.fromisoformat(configured.replace("Z", "+00:00"))
                    if isinstance(configured, str)
                    else configured
                )
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                candidate = parsed.date()
            else:
                candidate = (datetime.now(tz=timezone.utc) - timedelta(days=7)).date()

        floor = self._api_date_floor()
        if candidate < floor:
            self.logger.info(
                "Stream %s: requested start %s is before the API floor %s; "
                "clamping (older records are not retrievable).",
                self.name,
                candidate.isoformat(),
                floor.isoformat(),
            )
            return floor
        return candidate

    @staticmethod
    def _api_date_floor() -> date:
        """First day of the month two months prior to today UTC.

        The Expense API rejects date filters older than this.
        """
        today = datetime.now(tz=timezone.utc).date()
        # Step back two months by walking to the first of this month
        # and subtracting two more months.
        first_of_this_month = today.replace(day=1)
        # Walk back two months.
        year = first_of_this_month.year
        month = first_of_this_month.month - 2
        while month <= 0:
            month += 12
            year -= 1
        return first_of_this_month.replace(year=year, month=month, day=1)


# ---------------------------------------------------------------------------
# Concrete transaction streams
# ---------------------------------------------------------------------------


class CardTransactionsStream(NavanExpenseStream):
    """Navan-issued card transactions (virtual + physical)."""

    name = "card_transactions"
    path = "/v1/expense/card-transactions"


class ConnectTransactionsStream(NavanExpenseStream):
    """External card-program transactions (Amex, Citi, etc.) via Navan Connect."""

    name = "connect_transactions"
    path = "/v1/expense/connect-transactions"


class ManualTransactionsStream(NavanExpenseStream):
    """Manual / payroll / flexible-reimbursement expense submissions."""

    name = "manual_transactions"
    path = "/v1/expense/manual-transactions"


class RepaymentsStream(NavanExpenseStream):
    """Employee repayments to the company."""

    name = "repayments"
    path = "/v1/expense/repayments"


class FeesStream(NavanExpenseStream):
    """Direct-reimbursement, FX, and platform fees charged by Navan."""

    name = "fees"
    path = "/v1/expense/fees"


class AdjustmentsStream(NavanExpenseStream):
    """Credit and debit memo adjustments."""

    name = "adjustments"
    path = "/v1/expense/adjustments"


class DailyRebatesStream(NavanExpenseStream):
    """Daily rebate accruals."""

    name = "daily_rebates"
    path = "/v1/expense/daily-rebates"


class DisputesStream(NavanExpenseStream):
    """Card disputes, including provisional credit and reversals."""

    name = "disputes"
    path = "/v1/expense/disputes"


# ---------------------------------------------------------------------------
# Receipts stream — different shape, lightweight payload
# ---------------------------------------------------------------------------


# All transaction types the receipts endpoint expects in `type[]`.
# Includes both card variants, manual, repayments, fees, adjustments,
# daily rebates, and disputes — matches what the Quick Start Guide
# lists as valid type values.
_RECEIPT_TYPES = (
    "TRANSACTIONS",
    "CONNECT",
    "MANUAL_TRANSACTIONS",
    "REPAYMENTS",
    "FEES",
    "ADJUSTMENTS",
    "DAILY_REBATE",
    "DISPUTE",
)


class ReceiptsStream(NavanExpenseStream):
    """Presigned receipt-download URLs for every transaction.

    Different shape from the transaction streams: only ``transaction_id``
    plus the two URLs. URLs are presigned and expire in 7 days, so this
    stream is intended for daily refresh — downstream consumers should
    fetch the binary content immediately rather than caching the URL.

    The endpoint has no ``modified_timestamp``, so this stream uses
    FULL_TABLE replication scoped to a rolling 7-day window matched to
    the URL TTL.
    """

    name = "receipts"
    path = "/v1/expense/transactions/receipts"
    primary_keys: ClassVar[tuple[str, ...]] = ("transaction_id",)
    # URLs change each call (new presigned signature), so a real
    # replication key would mark every record as changed. FULL_TABLE
    # over a fixed window is the honest answer here.
    replication_key = None

    schema = th.PropertiesList(
        th.Property("transaction_id", th.StringType, required=True),
        th.Property("receipt_url", th.StringType),
        th.Property("e_receipt_url", th.StringType),
    ).to_dict()

    #: How many days back to refresh receipt URLs each run. Matches the
    #: 7-day URL TTL so downstream consumers always have a valid link.
    receipt_lookback_days: ClassVar[int] = 7

    @override
    def get_url_params(
        self,
        context: Context | None,
        next_page_token: Any | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "page_size": PAGE_SIZE,
            # The endpoint requires the `type[]` array param. requests
            # serializes a list value as repeated `type=...` keys.
            "type": list(_RECEIPT_TYPES),
        }
        if context is not None:
            if "date_modified_from" in context:
                params["date_modified.from"] = context["date_modified_from"]
            if "date_modified_to" in context:
                params["date_modified.to"] = context["date_modified_to"]
        if next_page_token:
            params["cursor"] = next_page_token
        return params

    @override
    def request_records(self, context: Context | None) -> Iterable[dict]:
        end_day = datetime.now(tz=timezone.utc).date()
        start_day = end_day - timedelta(days=self.receipt_lookback_days)
        start_day = max(start_day, self._api_date_floor())

        window_context: dict[str, Any] = dict(context or {})
        window_context["date_modified_from"] = start_day.isoformat()
        window_context["date_modified_to"] = end_day.isoformat()

        self.logger.info(
            "Stream %s: refreshing presigned receipt URLs for %s..%s",
            self.name,
            start_day.isoformat(),
            end_day.isoformat(),
        )

        try:
            yield from NavanStream.request_records(self, window_context)
        except FatalAPIError as exc:
            if "403" in str(exc):
                self.logger.warning(
                    "Stream %s: 403 Forbidden. Verify the tenant is enabled "
                    "for the Expense API. Skipping.",
                    self.name,
                )
                return
            raise
