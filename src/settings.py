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
class PolicySettings:
    host: str = "policy"
    port: str = "8083"
    enable_validation: bool = False


@dataclass(frozen=True)
class ContextServiceSettings:
    """Top-level service configuration surface."""

    storage: StorageSettings = field(default_factory=StorageSettings)
    policy: PolicySettings = field(default_factory=PolicySettings)
    require_consent: bool = False
    conversation_db_path: str = "/tmp/unison-context-conversation.db"
    profile_enc_key: str = ""

    @classmethod
    def from_env(cls) -> "ContextServiceSettings":
        """Construct settings once from environment variables."""
        return cls(
            storage=StorageSettings(
                host=os.getenv("UNISON_STORAGE_HOST", "storage"),
                port=os.getenv("UNISON_STORAGE_PORT", "8082"),
            ),
            policy=PolicySettings(
                host=os.getenv("UNISON_POLICY_HOST", "policy"),
                port=os.getenv("UNISON_POLICY_PORT", "8083"),
                enable_validation=_as_bool(os.getenv("UNISON_POLICY_VALIDATE_GROUPS"), False),
            ),
            require_consent=_as_bool(os.getenv("UNISON_REQUIRE_CONSENT", "false")),
            conversation_db_path=os.getenv("UNISON_CONTEXT_DB_PATH", "/tmp/unison-context-conversation.db"),
            profile_enc_key=os.getenv("UNISON_CONTEXT_PROFILE_KEY", ""),
        )


__all__ = ["ContextServiceSettings", "StorageSettings", "PolicySettings"]
