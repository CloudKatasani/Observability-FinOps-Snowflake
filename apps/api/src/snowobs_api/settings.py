"""Settings access for the API process.

The typed schema lives in ``snowobs_common.config`` — this module only owns
process-lifetime instantiation, so the app and its tests share one entry point.
"""

from functools import lru_cache

from snowobs_common.config import Settings, load_settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
