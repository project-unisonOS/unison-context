from __future__ import annotations

import os
from typing import Dict

from src.settings import ContextServiceSettings


def test_settings_defaults(monkeypatch):
    # Clear env to ensure defaults apply
    for key in ("UNISON_STORAGE_HOST", "UNISON_STORAGE_PORT", "UNISON_REQUIRE_CONSENT"):
        monkeypatch.delenv(key, raising=False)

    settings = ContextServiceSettings.from_env()

    assert settings.storage.host == "storage"
    assert settings.storage.port == "8082"
    assert settings.require_consent is False


def test_settings_env_overrides(monkeypatch):
    overrides: Dict[str, str] = {
        "UNISON_STORAGE_HOST": "context-storage",
        "UNISON_STORAGE_PORT": "9000",
        "UNISON_REQUIRE_CONSENT": "TRUE",
    }
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)

    settings = ContextServiceSettings.from_env()

    assert settings.storage.host == "context-storage"
    assert settings.storage.port == "9000"
    assert settings.require_consent is True
