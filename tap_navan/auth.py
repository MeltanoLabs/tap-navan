"""Navan Authentication."""

from __future__ import annotations

import sys

from singer_sdk.authenticators import OAuthAuthenticator, SingletonMeta

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override


# The SingletonMeta metaclass makes your streams reuse the same authenticator instance.
# If this behaviour interferes with your use-case, you can remove the metaclass.
class NavanAuthenticator(OAuthAuthenticator, metaclass=SingletonMeta):
    """Authenticator class for Navan."""

    @property
    @override
    def oauth_request_body(self) -> dict:
        """Define the OAuth request body for the Navan API.

        Navan uses the OAuth2 client_credentials grant type.

        Returns:
            A dict with the request body.
        """
        return {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
