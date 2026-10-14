"""Use case: read the saved parties of known accounts from their JSON file.

The file I/O lives here, not in ``services/personalization`` (a deterministic core
that only parses) and not in the entrypoint (so it is tested apart from the server).
"""

import json
from pathlib import Path

from parkmind.services.personalization.household import (
    Household,
    HouseholdFileError,
    parse_households,
)


def load_households(path: Path) -> dict[str, Household]:
    """An absent file means no saved parties; a present but broken one is an error."""
    if not path.is_file():
        return {}
    try:
        return parse_households(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise HouseholdFileError(f"{path.name} is not valid JSON: {exc}") from None
