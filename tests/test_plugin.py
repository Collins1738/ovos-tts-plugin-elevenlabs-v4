import asyncio
from unittest.mock import Mock, patch

import httpx
import pytest

from ovos_tts_plugin_elevenlabs_v4 import (
    API_ROOT,
    DEFAULT_MODEL,
    DEFAULT_OUTPUT_FORMAT,
    DEFAULT_VOICE,
    ElevenLabsV4TTS,
)


def plugin(**config) -> ElevenLabsV4TTS:
    with patch(
        "ovos_tts_plugin_elevenlabs_v4.load_credential",
        return_value="test-value",
    ):
        return ElevenLabsV4TTS(config=config)


class FakeResponse:
    def __init__(self, chunks=(b"one", b"", b"two"), content_type="audio/mpeg", error=None):
        self.chunks = chunks
        self.headers = {"content-type": content_type}
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def raise_for_status(self):
        if self.error:
            raise self.error

    async def aiter_bytes(self, chunk_size):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def stream(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


async def collect(engine, sentence, **kwargs):
    return [chunk async for chunk in engine.stream_tts(sentence, **kwargs)]


def patch_client(response):
    client = FakeClient(response)
    return client, patch(
        "ovos_tts_plugin_elevenlabs_v4.httpx.AsyncClient",
        return_value=client,
    )


def test_streams_v4_turbo_audio_chunks():
    engine = plugin()
    client, patched = patch_client(FakeResponse())

    with patched:
        assert asyncio.run(collect(engine, "Hello Collins")) == [b"one", b"two"]

    args, kwargs = client.calls[0]
    assert args == ("POST", f"{API_ROOT}/{DEFAULT_VOICE}/stream")
    assert kwargs["params"] == {"output_format": DEFAULT_OUTPUT_FORMAT}
    assert kwargs["json"] == {
        "text": "Hello Collins",
        "model_id": DEFAULT_MODEL,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
            "speed": 1.0,
        },
        "apply_text_normalization": "auto",
    }


def test_request_overrides_voice_model_and_language():
    engine = plugin(voice_id="configured", speed=1.1)
    client, patched = patch_client(FakeResponse())

    with patched:
        asyncio.run(
            collect(
                engine,
                "Good morning",
                voice_id="requested",
                model_id="another-model",
                language_code="en-US",
            )
        )

    args, kwargs = client.calls[0]
    assert args[1].endswith("/requested/stream")
    assert kwargs["json"]["model_id"] == "another-model"
    assert kwargs["json"]["language_code"] == "en"
    assert kwargs["json"]["voice_settings"]["speed"] == 1.1


def test_sync_get_tts_atomically_writes_streamed_file(tmp_path):
    engine = plugin()
    target = tmp_path / "speech.mp3"
    target.write_bytes(b"old")
    _, patched = patch_client(FakeResponse((b"audio-", b"bytes")))

    with patched:
        path, phonemes = engine.get_tts("Hello", str(target))

    assert path == str(target)
    assert phonemes is None
    assert target.read_bytes() == b"audio-bytes"
    assert list(tmp_path.glob(".elevenlabs-*")) == []


def test_partial_stream_never_replaces_final_file(tmp_path):
    engine = plugin()
    target = tmp_path / "speech.mp3"
    target.write_bytes(b"known-good")
    _, patched = patch_client(
        FakeResponse((b"partial", RuntimeError("stream interrupted")))
    )

    with patched, pytest.raises(RuntimeError, match="stream interrupted"):
        engine.get_tts("Hello", str(target))

    assert target.read_bytes() == b"known-good"
    assert list(tmp_path.glob(".elevenlabs-*")) == []


def test_http_failure_propagates_for_piper_fallback():
    engine = plugin()
    request = httpx.Request("POST", "https://example.test")
    failure = httpx.HTTPStatusError(
        "provider unavailable", request=request, response=Mock(status_code=503)
    )
    _, patched = patch_client(FakeResponse(error=failure))

    with patched, pytest.raises(httpx.HTTPStatusError, match="provider unavailable"):
        asyncio.run(collect(engine, "Hello"))


def test_rejects_non_audio_response():
    engine = plugin()
    _, patched = patch_client(FakeResponse(content_type="application/json"))

    with patched, pytest.raises(RuntimeError, match="unexpected content type"):
        asyncio.run(collect(engine, "Hello"))


def test_cache_identity_changes_with_voice_and_model():
    engine = plugin()

    default_context = engine._get_ctxt({})
    other_voice = engine._get_ctxt({"voice_id": "other"}).tts_id
    other_model = engine._get_ctxt({"model_id": "other-model"}).tts_id

    assert default_context.voice == DEFAULT_VOICE
    assert default_context.synth_kwargs["voice_id"] == DEFAULT_VOICE
    assert default_context.tts_id != other_voice
    assert default_context.tts_id != other_model


def test_available_languages_is_available_on_the_class():
    assert "en-us" in ElevenLabsV4TTS.available_languages


def test_opm_entry_point_is_discoverable():
    from ovos_plugin_manager.tts import find_tts_plugins

    assert find_tts_plugins()["ovos-tts-plugin-elevenlabs-v4"] is ElevenLabsV4TTS


def test_validator_performs_local_checks_only():
    engine = plugin()
    engine.validator.validate()


@pytest.mark.parametrize(
    "config, message",
    [
        ({"output_format": "pcm_24000"}, "MP3"),
        ({"chunk_size": 1}, "chunk_size"),
        ({"stability": 2}, "stability"),
        ({"speed": 2}, "speed"),
        ({"apply_text_normalization": "sometimes"}, "apply_text_normalization"),
    ],
)
def test_rejects_invalid_configuration(config, message):
    with patch(
        "ovos_tts_plugin_elevenlabs_v4.load_credential",
        return_value="test-value",
    ):
        with pytest.raises(ValueError, match=message):
            ElevenLabsV4TTS(config=config)


class RecordingCallbacks:
    def __init__(self):
        self.calls = []

    def stream_start(self, message=None):
        self.calls.append(("start", message))

    def stream_chunk(self, chunk):
        self.calls.append(("chunk", chunk))

    def stream_stop(self, listen=False, message=None):
        self.calls.append(("stop", listen, message))

    def stream_abort(self):
        self.calls.append(("abort",))


def test_completed_playback_emits_one_start_and_stop(tmp_path):
    engine = plugin()
    engine.callbacks = RecordingCallbacks()
    _, patched = patch_client(FakeResponse((b"audio",)))

    with patched:
        asyncio.run(
            engine.generate_audio(
                "Hello", str(tmp_path / "speech.mp3"), listen=True
            )
        )

    assert [call[0] for call in engine.callbacks.calls] == [
        "start", "chunk", "stop"
    ]
    assert engine.callbacks.calls[-1][1] is True


def test_failed_playback_aborts_and_never_listens(tmp_path):
    engine = plugin()
    engine.callbacks = RecordingCallbacks()
    _, patched = patch_client(
        FakeResponse((b"partial", RuntimeError("stream interrupted")))
    )

    with patched, pytest.raises(RuntimeError, match="stream interrupted"):
        asyncio.run(
            engine.generate_audio(
                "Hello", str(tmp_path / "speech.mp3"), listen=True
            )
        )

    names = [call[0] for call in engine.callbacks.calls]
    assert names == ["start", "chunk", "abort", "stop"]
    assert engine.callbacks.calls[-1][1] is False
    assert not (tmp_path / "speech.mp3").exists()
