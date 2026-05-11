"""Tests for tap-navan.

Unit tests (always run, no credentials needed) live at the top of this file.
Integration tests are gated by the include_* flags and only run outside CI.
"""

from __future__ import annotations

import datetime
import os

import pytest
from singer_sdk.testing import SuiteConfig, get_tap_test_class

from tap_navan.expense_streams import (
    EXPENSE_TRANSACTION_SCHEMA,
    AdjustmentsStream,
    CardTransactionsStream,
    ConnectTransactionsStream,
    DailyRebatesStream,
    DisputesStream,
    FeesStream,
    ManualTransactionsStream,
    NavanExpenseCursorPaginator,
    NavanExpenseStream,
    ReceiptsStream,
    RepaymentsStream,
)
from tap_navan.streams import BookingsStream, UsersStream
from tap_navan.tap import TapNavan

CI = "CI" in os.environ

_SAMPLE_CURSOR = "cursor_abc"
_EXPECTED_PAGE_SIZE = 500
_API_FLOOR_MIN_DAYS_BACK = 30
_API_FLOOR_MAX_DAYS_BACK = 122

ALL_TRANSACTION_STREAM_CLASSES = (
    CardTransactionsStream,
    ConnectTransactionsStream,
    ManualTransactionsStream,
    RepaymentsStream,
    FeesStream,
    AdjustmentsStream,
    DailyRebatesStream,
    DisputesStream,
)

EXPECTED_STREAM_NAMES = {
    "bookings",
    "users",
    "card_transactions",
    "connect_transactions",
    "manual_transactions",
    "repayments",
    "fees",
    "adjustments",
    "daily_rebates",
    "disputes",
    "receipts",
}


# ---------------------------------------------------------------------------
# Unit tests — no credentials or network access required
# ---------------------------------------------------------------------------


def _one_week_ago() -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
    return dt.strftime("%Y-%m-%d")


@pytest.fixture(scope="module")
def tap() -> TapNavan:
    """Return a TapNavan instance with stub credentials."""
    return TapNavan(
        config={
            "client_id": "stub",
            "client_secret": "stub",
            "start_date": _one_week_ago(),
        }
    )


def test_discover_streams_returns_all_known_streams(tap: TapNavan) -> None:
    names = {s.name for s in tap.discover_streams()}
    assert names == EXPECTED_STREAM_NAMES


def test_bookings_primary_key_and_replication_key() -> None:
    assert BookingsStream.primary_keys == ("uuid",)
    assert BookingsStream.replication_key == "created"


def test_users_primary_key_and_no_replication_key() -> None:
    assert UsersStream.primary_keys == ("id",)
    assert UsersStream.replication_key is None


def test_config_requires_client_id_and_secret() -> None:
    required = TapNavan.config_jsonschema.get("required", [])
    assert "client_id" in required
    assert "client_secret" in required


def test_bookings_schema_includes_expense_fields(tap: TapNavan) -> None:
    stream = next(s for s in tap.discover_streams() if s.name == "bookings")
    props = stream.schema["properties"]
    for field in ("uuid", "created", "grandTotal", "currency", "booker", "outOfPolicy"):
        assert field in props, f"Missing expense field: {field}"


# ---------------------------------------------------------------------------
# Expense transaction streams — unit tests
# ---------------------------------------------------------------------------


def test_all_transaction_streams_share_keys_and_schema() -> None:
    for cls in ALL_TRANSACTION_STREAM_CLASSES:
        assert cls.primary_keys == ("id",), f"{cls.__name__} wrong primary_keys"
        assert cls.replication_key == "modified_timestamp", (
            f"{cls.__name__} wrong replication_key"
        )
        assert cls.schema is EXPENSE_TRANSACTION_SCHEMA, (
            f"{cls.__name__} not sharing the unified schema"
        )


def test_transaction_stream_paths_match_api_docs() -> None:
    expected = {
        "card_transactions": "/v1/expense/card-transactions",
        "connect_transactions": "/v1/expense/connect-transactions",
        "manual_transactions": "/v1/expense/manual-transactions",
        "repayments": "/v1/expense/repayments",
        "fees": "/v1/expense/fees",
        "adjustments": "/v1/expense/adjustments",
        "daily_rebates": "/v1/expense/daily-rebates",
        "disputes": "/v1/expense/disputes",
    }
    for cls in ALL_TRANSACTION_STREAM_CLASSES:
        assert cls.path == expected[cls.name], f"{cls.__name__} path wrong"


def test_expense_schema_covers_documented_fields() -> None:
    props = EXPENSE_TRANSACTION_SCHEMA["properties"]
    for field in (
        "id",
        "_type",
        "modified_timestamp",
        "posted_amount",
        "posted_currency",
        "vendor_name",
        "gl_code_number",
        "custom_field_values",
        "booking_details",
        "tax_details",
        "line_items",
        "traveler_department",
        "rejected_amount",
        "reporting_transaction_id",
    ):
        assert field in props, f"Missing expense field: {field}"


def test_custom_field_values_uses_camelcase_display_value() -> None:
    """The live API returns ``displayValue`` (camelCase), not snake_case.

    Catching this with a test because the Quick Start Guide PDF
    incorrectly documents it as ``display_value`` and Singer SDK would
    silently strip the field if the schema disagreed with the wire.
    """
    cf = EXPENSE_TRANSACTION_SCHEMA["properties"]["custom_field_values"]
    item_props = cf["items"]["properties"]
    assert "displayValue" in item_props
    assert "display_value" not in item_props


def test_expense_paginator_stops_on_has_next_false() -> None:
    paginator = NavanExpenseCursorPaginator()

    class _Resp:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def json(self) -> dict:
            return self._payload

    assert paginator.has_more(_Resp({"has_next": True, "next_cursor": "abc"})) is True
    assert paginator.has_more(_Resp({"has_next": False})) is False
    # Defensive default — missing has_next means stop.
    assert paginator.has_more(_Resp({})) is False
    # get_next mirrors the same semantics.
    assert paginator.get_next(_Resp({"has_next": True, "next_cursor": "abc"})) == "abc"
    assert paginator.get_next(_Resp({"has_next": False, "next_cursor": "abc"})) is None


def test_expense_get_url_params_uses_window_context(tap: TapNavan) -> None:
    stream = next(s for s in tap.discover_streams() if s.name == "manual_transactions")
    params = stream.get_url_params(
        context={
            "date_modified_from": "2026-03-15",
            "date_modified_to": "2026-05-15",
        },
        next_page_token=_SAMPLE_CURSOR,
    )
    assert params["date_modified.from"] == "2026-03-15"
    assert params["date_modified.to"] == "2026-05-15"
    assert params["cursor"] == _SAMPLE_CURSOR
    assert params["page_size"] == _EXPECTED_PAGE_SIZE
    assert "POLICY" in params["include_field_group"]
    assert "TAX" in params["include_field_group"]
    assert "TRAVELER_HR" in params["include_field_group"]


def test_expense_get_url_params_omits_cursor_on_first_page(tap: TapNavan) -> None:
    stream = next(s for s in tap.discover_streams() if s.name == "card_transactions")
    params = stream.get_url_params(
        context={"date_modified_from": "2026-05-01", "date_modified_to": "2026-05-15"},
        next_page_token=None,
    )
    assert "cursor" not in params


def test_api_date_floor_is_first_of_month_two_months_back() -> None:
    floor = NavanExpenseStream._api_date_floor()  # noqa: SLF001 — testing the helper
    today = datetime.datetime.now(datetime.timezone.utc).date()
    assert floor.day == 1
    # Should be roughly 60-90 days back depending on month length.
    delta = (today - floor).days
    assert _API_FLOOR_MIN_DAYS_BACK < delta < _API_FLOOR_MAX_DAYS_BACK, (
        f"Floor {floor} is {delta} days back from {today}"
    )


def test_eu_header_added_when_api_url_is_eu() -> None:
    eu_tap = TapNavan(
        config={
            "client_id": "stub",
            "client_secret": "stub",
            "start_date": _one_week_ago(),
            "api_url": "https://app-fra.navan.com",
        }
    )
    eu_stream = next(s for s in eu_tap.discover_streams() if s.name == "card_transactions")
    assert eu_stream.http_headers.get("X-ta-region") == "EU"

    us_stream = next(
        s for s in TapNavan(
            config={
                "client_id": "stub",
                "client_secret": "stub",
                "start_date": _one_week_ago(),
            }
        ).discover_streams()
        if s.name == "card_transactions"
    )
    assert "X-ta-region" not in us_stream.http_headers


# ---------------------------------------------------------------------------
# Receipts stream — unit tests
# ---------------------------------------------------------------------------


def test_receipts_stream_is_full_table() -> None:
    assert ReceiptsStream.primary_keys == ("transaction_id",)
    assert ReceiptsStream.replication_key is None
    assert ReceiptsStream.path == "/v1/expense/transactions/receipts"


def test_receipts_schema_is_minimal() -> None:
    props = ReceiptsStream.schema["properties"]
    assert set(props) == {"transaction_id", "receipt_url", "e_receipt_url"}


def test_receipts_url_params_include_all_transaction_types(tap: TapNavan) -> None:
    stream = next(s for s in tap.discover_streams() if s.name == "receipts")
    params = stream.get_url_params(
        context={"date_modified_from": "2026-05-01", "date_modified_to": "2026-05-08"},
        next_page_token=None,
    )
    assert isinstance(params["type"], list)
    assert "TRANSACTIONS" in params["type"]
    assert "CONNECT" in params["type"]
    assert "MANUAL_TRANSACTIONS" in params["type"]


# ---------------------------------------------------------------------------
# Integration tests — skipped in CI (require real credentials)
# ---------------------------------------------------------------------------


SAMPLE_CONFIG = {"start_date": _one_week_ago()}

# The users stream requires the ``users:read`` OAuth scope. TMC credentials
# typically only have ``bookings:read``; 403 is swallowed and the stream
# yields 0 records, so mark it as optional. The expense streams may also
# yield zero records on a freshly enabled tenant — same treatment.
_NO_RECORDS_OK = [
    "users",
    "card_transactions",
    "connect_transactions",
    "manual_transactions",
    "repayments",
    "fees",
    "adjustments",
    "daily_rebates",
    "disputes",
    "receipts",
]

TestTapNavan = get_tap_test_class(
    tap_class=TapNavan,
    config=SAMPLE_CONFIG,
    include_tap_tests=not CI,
    include_stream_tests=not CI,
    include_stream_attribute_tests=not CI,
    suite_config=SuiteConfig(ignore_no_records_for_streams=_NO_RECORDS_OK),
)
