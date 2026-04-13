"""Tests standard tap features using the built-in SDK tests library."""

import datetime
import os

import pytest
from dotenv import load_dotenv
from singer_sdk.testing import SuiteConfig, get_tap_test_class

from tap_navan.tap import TapNavan

load_dotenv()  # populate os.environ from .env before pytestmark is evaluated

pytestmark = pytest.mark.skipif(
    not os.environ.get("TAP_NAVAN_CLIENT_ID"),
    reason="TAP_NAVAN_CLIENT_ID not set",
)


SAMPLE_CONFIG = {
    "start_date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"),
}

# The users stream requires the ``users:read`` OAuth scope.  TMC credentials
# may only have ``bookings:read``, in which case the stream returns 0 records
# without raising an error.  Mark it as optional so the suite doesn't fail.
TestTapNavan = get_tap_test_class(
    tap_class=TapNavan,
    config=SAMPLE_CONFIG,
    suite_config=SuiteConfig(ignore_no_records_for_streams=["users"]),
)
