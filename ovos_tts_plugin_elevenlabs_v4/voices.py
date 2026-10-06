"""Curated voice presets and a small OVOS configuration CLI."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

PLUGIN_ID = "ovos-tts-plugin-elevenlabs-v4"
VOICE_PRESETS = {
    "george": {
        "id": "JBFqnCBsd6RMkjVDRZzb",
        "description": "Warm British storyteller",
    },
    "river": {
        "id": "SAz9YHcvj6GT2YYXdXww",
        "description": "Relaxed, neutral American",
    },
    "chris": {
        "id": "iP95p4xoKVk53GoZ742B",
        "description": "Charming, down-to-earth American",
    },
    "eric": {
        "id": "cjVigY5qzO86Huf0OWal",
        "description": "Smooth, trustworthy American",
    },
}


def resolve_voice(value: str) -> str:
    """Resolve a preset name while allowing an arbitrary ElevenLabs voice ID."""
    value = value.strip()
    if not value:
        raise ValueError("voice must not be empty")
    preset = VOICE_PRESETS.get(value.lower())
    return preset["id"] if preset else value


def describe_voice(voice_id: str) -> str:
    for name, preset in VOICE_PRESETS.items():
        if preset["id"] == voice_id:
            return f"{name} ({voice_id})"
    return voice_id


def set_configured_voice(value: str, config_path: Path) -> str:
    """Atomically update only this plugin's voice in an OVOS JSON config."""
    voice_id = resolve_voice(value)
    config_path = Path(config_path).expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(config_path.read_text()) if config_path.exists() else {}
    provider = data.setdefault("tts", {}).setdefault(PLUGIN_ID, {})
    provider["voice_id"] = voice_id

    fd, temporary = tempfile.mkstemp(
        prefix=f"{config_path.name}.", dir=config_path.parent
    )
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(data, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, config_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return voice_id


def get_configured_voice(config_path: Path) -> str | None:
    config_path = Path(config_path).expanduser()
    if not config_path.exists():
        return None
    data = json.loads(config_path.read_text())
    return data.get("tts", {}).get(PLUGIN_ID, {}).get("voice_id")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="List or switch the ElevenLabs voice used by OVOS"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("~/.config/mycroft/mycroft.conf").expanduser(),
        help="OVOS configuration file",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="list curated voice presets")
    commands.add_parser("show", help="show the configured voice")
    setter = commands.add_parser("set", help="set a preset name or voice ID")
    setter.add_argument("voice")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "list":
        for name, preset in VOICE_PRESETS.items():
            print(f"{name:8} {preset['id']}  {preset['description']}")
        return 0
    if args.command == "show":
        voice_id = get_configured_voice(args.config)
        print(describe_voice(voice_id) if voice_id else "not configured")
        return 0

    voice_id = set_configured_voice(args.voice, args.config)
    print(f"Voice set to {describe_voice(voice_id)}")
    print("Restart ovos-audio to apply the change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
