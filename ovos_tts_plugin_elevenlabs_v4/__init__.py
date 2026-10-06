"""Streaming ElevenLabs v4 Turbo text-to-speech plugin for OpenVoiceOS."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import threading
from contextlib import suppress
from pathlib import Path
from typing import AsyncIterable

import httpx
from ovos_plugin_manager.templates.tts import (
    StreamingTTS,
    TTSContext,
    TTSValidator,
)
from ovos_plugin_manager.utils.tts_cache import AudioFile, hash_sentence
from ovos_utils import classproperty

from .credentials import resolve_api_key as load_credential
from .playback import InterruptibleStreamingCallbacks
from .voices import VOICE_PRESETS

API_ROOT = "https://api.elevenlabs.io/v1/text-to-speech"
DEFAULT_MODEL = "eleven_v4_turbo"
DEFAULT_VOICE = VOICE_PRESETS["george"]["id"]
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"


class ElevenLabsV4TTS(StreamingTTS):
    """Stream ElevenLabs speech into OVOS playback as it is generated."""

    def __init__(self, *args, **kwargs):
        kwargs.pop("validator", None)
        self._active_streams = {}
        self._streams_lock = threading.Lock()
        super().__init__(
            *args,
            **kwargs,
            audio_ext="mp3",
            ssml_tags=[],
            validator=ElevenLabsV4TTSValidator(self),
        )
        self.api_key = load_credential(self.config)
        self.voice_id = str(self.config.get("voice_id") or DEFAULT_VOICE)
        self.model_id = str(self.config.get("model_id") or DEFAULT_MODEL)
        self.output_format = str(
            self.config.get("output_format") or DEFAULT_OUTPUT_FORMAT
        )
        if not self.output_format.startswith("mp3_"):
            raise ValueError("output_format must be an MP3 format")

        self.api_root = str(self.config.get("api_root") or API_ROOT).rstrip("/")
        self.connect_timeout = float(self.config.get("connect_timeout") or 5)
        self.read_timeout = float(self.config.get("read_timeout") or 60)
        self.chunk_size = int(self.config.get("chunk_size") or 4096)
        if not 256 <= self.chunk_size <= 1024 * 1024:
            raise ValueError("chunk_size must be between 256 and 1048576 bytes")

        self.voice_settings = {
            "stability": self._ratio("stability", 0.5),
            "similarity_boost": self._ratio("similarity_boost", 0.75),
            "style": self._ratio("style", 0.0),
            "use_speaker_boost": bool(self.config.get("use_speaker_boost", True)),
            "speed": self._speed(self.config.get("speed", 1.0)),
        }
        self.apply_text_normalization = str(
            self.config.get("apply_text_normalization") or "auto"
        )
        if self.apply_text_normalization not in {"auto", "on", "off"}:
            raise ValueError("apply_text_normalization must be auto, on, or off")

    def _ratio(self, name: str, default: float) -> float:
        value = float(self.config.get(name, default))
        if not 0 <= value <= 1:
            raise ValueError(f"{name} must be between 0 and 1")
        return value

    @staticmethod
    def _speed(value) -> float:
        value = float(value)
        if not 0.7 <= value <= 1.2:
            raise ValueError("speed must be between 0.7 and 1.2")
        return value

    def _get_ctxt(self, kwargs=None):
        """Include every synthesis-affecting setting in the OVOS cache identity."""
        request = dict(kwargs or {})
        requested_voice = request.get("voice_id") or request.get("voice")
        ctxt = super()._get_ctxt(request)
        voice_id = str(requested_voice or self.voice_id)
        model_id = str(request.get("model_id") or self.model_id)
        language_code = str(
            request.get("language_code") or request.get("lang") or ctxt.lang
        )
        settings = {
            "model_id": model_id,
            "output_format": self.output_format,
            "voice_settings": self.voice_settings,
            "apply_text_normalization": self.apply_text_normalization,
        }
        fingerprint = hashlib.sha256(
            json.dumps(settings, sort_keys=True).encode()
        ).hexdigest()[:16]
        return TTSContext(
            plugin_id=f"{ctxt.plugin_id}/{fingerprint}",
            lang=ctxt.lang,
            voice=voice_id,
            synth_kwargs={
                "voice_id": voice_id,
                "model_id": model_id,
                "language_code": language_code,
            },
        )

    async def stream_tts(
        self,
        sentence: str,
        voice_id: str | None = None,
        model_id: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterable[bytes]:
        """Yield MP3 bytes with async network I/O and natural backpressure."""
        voice_id = str(voice_id or self.voice_id)
        model_id = str(model_id or self.model_id)
        payload = {
            "text": sentence,
            "model_id": model_id,
            "voice_settings": dict(self.voice_settings),
            "apply_text_normalization": self.apply_text_normalization,
        }
        if language_code:
            payload["language_code"] = str(language_code).split("-")[0].lower()

        url = f"{self.api_root}/{voice_id}/stream"
        timeout = httpx.Timeout(self.read_timeout, connect=self.connect_timeout)
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": self.api_key,
        }
        async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
            async with client.stream(
                "POST",
                url,
                params={"output_format": self.output_format},
                json=payload,
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if not content_type.startswith("audio/"):
                    raise RuntimeError(
                        f"ElevenLabs returned unexpected content type {content_type!r}"
                    )
                async for chunk in response.aiter_bytes(self.chunk_size):
                    if chunk:
                        yield chunk

    @staticmethod
    def _temporary_audio(wav_file):
        directory = os.path.dirname(os.path.abspath(wav_file))
        os.makedirs(directory, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix=".elevenlabs-", dir=directory)
        os.close(fd)
        return path

    def _register_stream_audio(self, sentence, wav_file, kwargs):
        """Register a completed OVOS cache file, never a partial stream."""
        sentence_hash = hash_sentence(sentence)
        path = Path(wav_file).absolute()
        if path.name != f"{sentence_hash}.{self.audio_ext}":
            return
        ctxt = self._get_ctxt(kwargs)
        cache = ctxt.get_cache(self.audio_ext, self.config)
        allowed = {
            cache.temporary_cache_dir.absolute(),
            cache.persistent_cache_dir.absolute(),
        }
        if path.parent in allowed:
            audio = AudioFile(path.parent, sentence_hash, self.audio_ext)
            self._cache_sentence(sentence, ctxt.lang, audio, cache)

    async def generate_audio(
        self,
        sentence,
        wav_file,
        play_streaming=True,
        listen=False,
        message=None,
        plugin_kwargs=None,
    ):
        """Stream and atomically publish only complete audio files."""
        kwargs = plugin_kwargs or {}
        path = self._temporary_audio(wav_file)
        started = False
        completed = False
        stopped = threading.Event()
        task = asyncio.current_task()
        callbacks = self.callbacks if play_streaming else None
        if play_streaming:
            with self._streams_lock:
                if self._active_streams:
                    os.unlink(path)
                    raise RuntimeError("Streaming playback is already active")
                self._active_streams[task] = (
                    asyncio.get_running_loop(),
                    stopped,
                    callbacks,
                )
        chunks = self.stream_tts(sentence, **kwargs)
        try:
            if play_streaming:
                started = True
                await self._playback_call(callbacks, "stream_start", message)
            with open(path, "wb") as audio:
                async for chunk in chunks:
                    if stopped.is_set():
                        raise asyncio.CancelledError()
                    audio.write(chunk)
                    if play_streaming:
                        await self._playback_call(callbacks, "stream_chunk", chunk)
            if stopped.is_set():
                raise asyncio.CancelledError()
            os.replace(path, wav_file)
            if self.enable_cache:
                await asyncio.to_thread(
                    self._register_stream_audio, sentence, wav_file, kwargs
                )
            completed = True
            return wav_file
        finally:
            try:
                await chunks.aclose()
            finally:
                if os.path.exists(path):
                    os.unlink(path)
                try:
                    if started:
                        if not completed or stopped.is_set():
                            self._abort_player(callbacks)
                        await self._playback_call(
                            callbacks,
                            "stream_stop",
                            listen and completed and not stopped.is_set(),
                            message,
                        )
                finally:
                    if play_streaming:
                        with self._streams_lock:
                            self._active_streams.pop(task, None)

    async def _playback_call(self, callbacks, method, *args):
        """Keep player pipe operations off the event loop."""
        pending = asyncio.create_task(
            asyncio.to_thread(getattr(callbacks, method), *args)
        )
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            self._abort_player(callbacks)
            try:
                with suppress(Exception):
                    await pending
            finally:
                self._abort_player(callbacks)
            raise

    @staticmethod
    def _abort_player(callbacks):
        abort = getattr(callbacks, "stream_abort", None)
        if callable(abort):
            abort()

    def init(self, bus=None, playback=None, callbacks=None):
        if callbacks is None:
            callbacks = InterruptibleStreamingCallbacks(
                bus, tts_config=self.config
            )
        super().init(bus, playback, callbacks)

    def stop(self):
        """Stop queued playback and cancel any active provider stream."""
        super().stop()
        with self._streams_lock:
            active = [
                (task, state)
                for task, state in self._active_streams.items()
                if not state[1].is_set()
            ]
            for _, (_, stopped, _) in active:
                stopped.set()
        for task, (loop, _, callbacks) in active:
            self._abort_player(callbacks)
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(task.cancel)

    def _execute(self, sentence, ident, listen, **kwargs):
        if self.config.get("enable_streaming"):
            ctxt = self._get_ctxt(kwargs)
            kwargs.update(ctxt.synth_kwargs)
        try:
            return super()._execute(sentence, ident, listen, **kwargs)
        except asyncio.CancelledError:
            return None

    @classproperty
    def available_languages(cls):
        return {
            "en", "en-us", "en-gb", "de", "es", "fr", "it", "pt",
            "pl", "hi", "ja", "ko", "zh", "nl", "sv", "tr", "ru",
            "ar", "cs", "da", "fi", "el", "he", "hu", "id", "no",
            "ro", "sk", "uk", "vi",
        }


class ElevenLabsV4TTSValidator(TTSValidator):
    """Validate local configuration without making startup network calls."""

    def validate_dependencies(self):
        if not hasattr(httpx, "AsyncClient"):
            raise ImportError("httpx with AsyncClient support is required")

    def validate_instance(self):
        if not self.tts.api_key:
            raise RuntimeError("ElevenLabs API key is required")
        if not self.tts.voice_id:
            raise ValueError("voice_id is required")

    def validate_lang(self):
        language = self.tts.lang.lower()
        if language not in self.tts.available_languages:
            base = language.split("-")[0]
            if base not in self.tts.available_languages:
                raise ValueError(f"Unsupported ElevenLabs language: {language}")


ElevenLabsV4TTSConfig = {
    "en-US": [
        {
            "lang": "en-US",
            "voice_id": preset["id"],
            "model_id": DEFAULT_MODEL,
            "output_format": DEFAULT_OUTPUT_FORMAT,
            "enable_streaming": True,
            "meta": {
                "priority": 70,
                "display_name": f"ElevenLabs v4 Turbo · {name.title()}",
                "offline": False,
            },
        }
        for name, preset in VOICE_PRESETS.items()
    ]
}
