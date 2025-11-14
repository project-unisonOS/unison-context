"""Typed configuration objects for the unison-context service."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class StorageSettings:
    host: str = "storage"
    port: str = "8082"


@dataclass(frozen=True)
class ContextServiceSettings:
    """Top-level service configuration surface."""

    storage: StorageSettings = field(default_factory=StorageSettings)
    require_consent: bool = False

    @classmethod
    def from_env(cls) -> "ContextServiceSettings":
        """Construct settings once from environment variables."""
        return cls(
            storage=StorageSettings(
                host=os.getenv("UNISON_STORAGE_HOST", "storage"),
                port=os.getenv("UNISON_STORAGE_PORT", "8082"),
            ),
            require_consent=_as_bool(os.getenv("UNISON_REQUIRE_CONSENT", "false")),
        )


__all__ = ["ContextServiceSettings", "StorageSettings"]
