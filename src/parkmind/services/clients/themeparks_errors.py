"""ThemeParks adapter exceptions, shared by the client and its pure normalizer.

Re-exported from ``themeparks_client`` so existing imports keep working.
"""


class ThemeParksClientError(Exception):
    """Base class for all expected ThemeParksClient failures."""


class ThemeParksNotFoundError(ThemeParksClientError):
    """Provider returned 404, or no schedule entry matched the request."""


class ThemeParksUnavailableError(ThemeParksClientError):
    """Transient failure (timeout/connection/5xx/429) survived all retries."""


class ThemeParksSchemaError(ThemeParksClientError):
    """Provider response didn't match the expected shape — unknown status
    value, missing field, ambiguous schedule match, non-JSON body, foreign
    timezone, timestamp without an offset, ... Contract drift, never silently
    swallowed or coerced."""
