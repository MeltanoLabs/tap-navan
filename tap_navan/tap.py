"""Navan tap class."""

from __future__ import annotations

import sys

from singer_sdk import Tap
from singer_sdk import typing as th  # JSON schema typing helpers

from tap_navan import streams

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override


class TapNavan(Tap):
    """Singer tap for Navan.

    Extracts travel booking data (flights, hotels, rail, cars, black cars)
    and user records from the Navan TMC API for use in expense management
    and travel spend analytics.
    """

    name = "tap-navan"

    config_jsonschema = th.PropertiesList(
        th.Property(
            "client_id",
            th.StringType(nullable=False),
            required=True,
            secret=True,
            title="Client ID",
            description="OAuth client ID for the Navan API, from the Navan Admin Dashboard.",
        ),
        th.Property(
            "client_secret",
            th.StringType(nullable=False),
            required=True,
            secret=True,
            title="Client Secret",
            description=(
                "OAuth client secret for the Navan API. "
                "For TMC integrations this value is RSA-encrypted by Navan "
                "and must be decrypted with your private key before use."
            ),
        ),
        th.Property(
            "start_date",
            th.DateTimeType(nullable=True),
            description=(
                "Earliest booking creation date to sync (ISO 8601). "
                "Converted to an epoch-second ``createdFrom`` parameter. "
                "Defaults to six months ago when used with Meltano."
            ),
        ),
        th.Property(
            "api_url",
            th.StringType(nullable=False),
            title="API URL",
            default="https://api.navan.com",
            description=(
                "Base URL for the Navan API. "
                "Staging: https://staging-prime.tripactions.com  "
                "Production US (default): https://api.navan.com  "
                "Production EU: https://app-fra.navan.com"
            ),
        ),
    ).to_dict()

    @override
    def discover_streams(self) -> list[streams.NavanStream]:
        """Return a list of discovered streams.

        Returns:
            A list of discovered streams.
        """
        return [
            streams.UsersStream(self),
            streams.BookingsStream(self),
        ]


if __name__ == "__main__":
    TapNavan.cli()
