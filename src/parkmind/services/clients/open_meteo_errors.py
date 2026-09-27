"""Open-Meteo adapter exceptions, shared by the client and its pure normalizer.

Re-exported from ``open_meteo_client`` so existing imports keep working.
"""


class OpenMeteoClientError(Exception):
    """Base class for all expected OpenMeteoClient failures."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        original_error: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.original_error = original_error


class OpenMeteoNotFoundError(OpenMeteoClientError):
    """Provider returned 404, or no forecast data was found for the requested date."""


class OpenMeteoUnavailableError(OpenMeteoClientError):
    """Transient failure (timeout/connection/5xx/429) survived all retries."""


class OpenMeteoSchemaError(OpenMeteoClientError):
    """Provider response didn't match the expected shape — redirect (3xx),
    missing field, mismatched array lengths, non-JSON body, ...
    Contract drift, never silently swallowed or coerced."""
