"""REST client handling, including NavanStream base class."""

from __future__ import annotations

import sys
from functools import cached_property
from typing import TYPE_CHECKING, Any

from singer_sdk.pagination import BasePageNumberPaginator
from singer_sdk.streams import RESTStream

from tap_navan.auth import NavanAuthenticator

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override

if TYPE_CHECKING:
    from singer_sdk.helpers.types import Auth, Context


class NavanStream(RESTStream):
    """Navan stream class."""

    # Navan wraps records in a `data` array alongside a `page` metadata object.
    records_jsonpath = "$.data[*]"

    @property
    @override
    def url_base(self) -> str:
        """Return the API URL root, configurable via tap settings.

        Returns:
            The base URL for Navan API requests.
        """
        return self.config.get("api_url", "https://api.navan.com")

    @cached_property
    @override
    def authenticator(self) -> Auth:
        """Return a new authenticator object.

        Returns:
            An authenticator instance.
        """
        api_url = self.config.get("api_url", "https://api.navan.com")
        return NavanAuthenticator(
            client_id=self.config["client_id"],
            client_secret=self.config["client_secret"],
            auth_endpoint=f"{api_url}/ta-auth/oauth/token",
            oauth_scopes="bookings:read users:read",
        )

    @override
    def get_new_paginator(self) -> BasePageNumberPaginator:
        """Create a new pagination helper instance.

        Returns:
            A Navan page-number paginator starting at page 0.
        """
        return BasePageNumberPaginator(start_value=0)

    @override
    def get_url_params(
        self,
        context: Context | None,
        next_page_token: Any | None,
    ) -> dict[str, Any]:
        """Return a dictionary of values to be used in URL parameterization.

        Args:
            context: The stream context.
            next_page_token: The current page number.

        Returns:
            A dictionary of URL query parameters.
        """
        params: dict[str, Any] = {
            "page": next_page_token if next_page_token is not None else 0,
            "size": 100,
        }
        return params
