"""
Planning service custom exceptions.
"""


class ContextReloadError(Exception):
    """Raised when reloading the live context fails in a recoverable way."""
