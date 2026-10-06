import json

import pytest

from ovos_tts_plugin_elevenlabs_v4.voices import (
    VOICE_PRESETS,
    get_configured_voice,
    main,
    resolve_voice,
    set_configured_voice,
)


def test_resolves_presets_and_custom_ids():
    assert resolve_voice("George") == VOICE_PRESETS["george"]["id"]
    assert resolve_voice("custom-voice-id") == "custom-voice-id"
    with pytest.raises(ValueError, match="must not be empty"):
        resolve_voice("  ")


def test_set_voice_preserves_existing_configuration(tmp_path):
    config = tmp_path / "mycroft.conf"
    config.write_text(json.dumps({"listener": {"wake_word": "hey mycroft"}}))

    voice_id = set_configured_voice("chris", config)
    saved = json.loads(config.read_text())

    assert voice_id == VOICE_PRESETS["chris"]["id"]
    assert saved["listener"] == {"wake_word": "hey mycroft"}
    assert (
        saved["tts"]["ovos-tts-plugin-elevenlabs-v4"]["voice_id"]
        == VOICE_PRESETS["chris"]["id"]
    )
    assert get_configured_voice(config) == voice_id


def test_cli_lists_and_switches_voice(tmp_path, capsys):
    config = tmp_path / "mycroft.conf"

    assert main(["--config", str(config), "list"]) == 0
    assert "george" in capsys.readouterr().out

    assert main(["--config", str(config), "set", "river"]) == 0
    output = capsys.readouterr().out
    assert "river" in output
    assert "Restart ovos-audio" in output

    assert main(["--config", str(config), "show"]) == 0
    assert "river" in capsys.readouterr().out
