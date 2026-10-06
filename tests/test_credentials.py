from subprocess import CalledProcessError
from unittest.mock import Mock, patch

import pytest

from ovos_tts_plugin_elevenlabs_v4.credentials import resolve_api_key


def test_config_key_wins():
    assert resolve_api_key({"api_key": "explicit-value"}, {"ELEVENLABS_API_KEY": "env-value"}) == "explicit-value"


def test_environment_key_is_supported():
    assert resolve_api_key({}, {"ELEVENLABS_API_KEY": " env-key "}) == "env-key"


@patch("ovos_tts_plugin_elevenlabs_v4.credentials.subprocess.run")
def test_keychain_fallback(run):
    run.return_value = Mock(stdout=" keychain-key\n")

    assert resolve_api_key({}, {}) == "keychain-key"
    command = run.call_args.args[0]
    assert "ovos-elevenlabs-tts" in command
    assert "api-key" in command


@patch("ovos_tts_plugin_elevenlabs_v4.credentials.subprocess.run")
def test_missing_key_raises(run):
    run.side_effect = CalledProcessError(44, ["security"])

    with pytest.raises(RuntimeError, match="ElevenLabs API key unavailable"):
        resolve_api_key({}, {})
