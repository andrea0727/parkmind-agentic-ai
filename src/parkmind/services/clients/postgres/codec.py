"""JSONB (de)serialization of contract payloads.

Every aggregate row stores ``model_dump(mode="json")`` of its section 33
contract in a ``payload`` column. Reading validates it back through the same
contract, so a stored row that no longer matches raises ``StoredDataError``
instead of being coerced into a best guess.

Datetimes round-trip as ISO-8601 with their UTC offset: they stay
timezone-aware and denote the same instant, but come back carrying the stored
fixed offset rather than a ``ZoneInfo``.
"""

from collections.abc import Mapping
from typing import Any, TypeVar

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ValidationError

from parkmind.services.ports.errors import StoredDataError

ModelT = TypeVar("ModelT", bound=BaseModel)


def to_jsonb(model: BaseModel) -> Jsonb:
    return Jsonb(model.model_dump(mode="json"))


def raw_to_jsonb(payload: Mapping[str, Any]) -> Jsonb:
    """Wrap an opaque provider payload; it is stored verbatim, never interpreted."""
    return Jsonb(dict(payload))


def from_payload(model_cls: type[ModelT], payload: object, *, what: str) -> ModelT:
    """Validate a stored ``payload`` as ``model_cls``.

    ``from None`` and a count-only message keep the stored values out of tracebacks
    and logs: payloads can hold derived accessibility flags (section 12).
    """
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise StoredDataError(
            f"stored {what} no longer matches {model_cls.__name__} "
            f"({exc.error_count()} validation error(s))"
        ) from None
