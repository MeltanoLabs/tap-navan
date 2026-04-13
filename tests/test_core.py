"""Tests for tap-navan.

Unit tests (always run, no credentials needed) live at the top of this file.
Integration tests are gated by the include_* flags and only run outside CI.
"""

from __future__ import annotations

import datetime
import os

import pytest
from singer_sdk.testing import SuiteConfig, get_tap_test_class

from tap_navan.streams import BookingsStream, UsersStream
from tap_navan.tap import TapNavan

CI = "CI" in os.environ


# ---------------------------------------------------------------------------
# Unit tests — no credentials or network access required
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tap() -> TapNavan:
    """Return a TapNavan instance with stub credentials."""
    return TapNavan(config={"client_id": "stub", "client_secret": "stub"})


def test_discover_streams_returns_bookings_and_users(tap: TapNavan) -> None:
    names = {s.name for s in tap.discover_streams()}
    assert names == {"bookings", "users"}


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
# Integration tests — skipped in CI (require real credentials)
# ---------------------------------------------------------------------------


def _one_week_ago() -> str:
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)
    return dt.strftime("%Y-%m-%d")


SAMPLE_CONFIG = {"start_date": _one_week_ago()}

# The users stream requires the ``users:read`` OAuth scope. TMC credentials
# typically only have ``bookings:read``; 403 is swallowed and the stream
# yields 0 records, so mark it as optional.
TestTapNavan = get_tap_test_class(
    tap_class=TapNavan,
    config=SAMPLE_CONFIG,
    include_tap_tests=not CI,
    include_stream_tests=not CI,
    include_stream_attribute_tests=not CI,
    suite_config=SuiteConfig(ignore_no_records_for_streams=["users"]),
)
