"""Credential resolution without logging secret values."""

from __future__ import annotations

import os
import subprocess
from typing import Mapping, Optional

DEFAULT_KEYCHAIN_SERVICE = "ovos-elevenlabs-tts"
DEFAULT_KEYCHAIN_ACCOUNT = "api-key"


def resolve_api_key(
    config: Optional[Mapping] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> str:
    """Resolve an ElevenLabs key from config, environment, or macOS Keychain."""
    config = config or {}
    environ = environ or os.environ

    key = str(config.get("api_key") or "").strip()
    if key:
        return key

    env_name = str(config.get("api_key_env") or "ELEVENLABS_API_KEY")
    key = str(environ.get(env_name) or "").strip()
    if key:
        return key

    service = str(config.get("keychain_service") or DEFAULT_KEYCHAIN_SERVICE)
    account = str(config.get("keychain_account") or DEFAULT_KEYCHAIN_ACCOUNT)
    try:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-w",
                "-s",
                service,
                "-a",
                account,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        result = None

    key = result.stdout.strip() if result else ""
    if key:
        return key

    raise RuntimeError(
        "ElevenLabs API key unavailable; set ELEVENLABS_API_KEY or store it "
        f"in Keychain service {service!r}, account {account!r}"
    )
