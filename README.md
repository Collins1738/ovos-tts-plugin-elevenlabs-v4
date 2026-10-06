# OVOS ElevenLabs v4 Turbo TTS Plugin

Stream ElevenLabs v4 Turbo speech through OpenVoiceOS. Playback begins while
audio is still being generated, and OVOS can retain a local TTS provider such
as Piper as its automatic fallback.

## Install

```bash
pip install ovos-tts-plugin-elevenlabs-v4
```

For local development:

```bash
pip install -e '.[test]'
```

## Credentials

Create an ElevenLabs key with Text to Speech access. Avoid storing it in
`mycroft.conf` or source control.

On macOS, store it in Keychain. Leaving `-w` last opens a secure password prompt:

```bash
security add-generic-password \
  -U \
  -s ovos-elevenlabs-tts \
  -a api-key \
  -w
```

The plugin checks, in order:

1. `api_key` in plugin configuration, supported but discouraged
2. `ELEVENLABS_API_KEY`
3. macOS Keychain service `ovos-elevenlabs-tts`, account `api-key`

## Configure OVOS

```json
{
  "tts": {
    "module": "ovos-tts-plugin-elevenlabs-v4",
    "fallback_module": "ovos-tts-plugin-piper",
    "ovos-tts-plugin-elevenlabs-v4": {
      "voice_id": "JBFqnCBsd6RMkjVDRZzb",
      "model_id": "eleven_v4_turbo",
      "output_format": "mp3_44100_128",
      "enable_streaming": true,
      "enable_cache": true,
      "stability": 0.5,
      "similarity_boost": 0.75,
      "style": 0.0,
      "speed": 1.0,
      "use_speaker_boost": true,
      "apply_text_normalization": "auto"
    },
    "ovos-tts-plugin-piper": {
      "voice": "alan-low",
      "lang": "en-us"
    }
  }
}
```

The default voice ID is ElevenLabs' George voice. Replace it with any voice
available to your ElevenLabs account.

### Switch voices

The package includes four curated presets from the original audition:

```bash
ovos-elevenlabs-voice list
ovos-elevenlabs-voice show
ovos-elevenlabs-voice set chris
```

Available preset names are `george`, `river`, `chris`, and `eric`. You may also
pass any ElevenLabs voice ID to `set`. The command preserves the rest of
`mycroft.conf` and prints a reminder to restart `ovos-audio` after a change.

## Streaming and caching

`enable_streaming=true` starts playback as response chunks arrive. Completed
audio is published atomically and registered with the OVOS cache; repeated
phrases can then play locally without another API generation. Interrupted or
failed streams do not replace a known-good cache file.

Active provider streams and their player process can be cancelled through the
normal OVOS stop path. If synthesis raises an HTTP, timeout, or response error,
`ovos-audio` invokes the configured `fallback_module`.

## Test

```bash
python -m pytest -q
```
