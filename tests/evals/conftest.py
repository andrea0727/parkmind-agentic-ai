"""Keeps DeepEval local and quiet before any test module imports it."""

import os
import tempfile
from pathlib import Path

_STATE = Path(tempfile.gettempdir()) / "parkmind-deepeval"

for _name, _value in {
    "DEEPEVAL_TELEMETRY_OPT_OUT": "YES",
    "DEEPEVAL_DISABLE_DOTENV": "1",
    "DEEPEVAL_UPDATE_WARNING_OPT_IN": "0",
    "DEEPEVAL_HOME": str(_STATE / "home"),
    "DEEPEVAL_CACHE_FOLDER": str(_STATE / "cache"),
}.items():
    os.environ.setdefault(_name, _value)
