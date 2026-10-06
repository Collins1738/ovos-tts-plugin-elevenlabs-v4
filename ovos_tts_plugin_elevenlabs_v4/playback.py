"""Interruptible streaming playback callbacks for OVOS."""

from __future__ import annotations

import subprocess
from contextlib import suppress

from ovos_plugin_manager.templates.tts import StreamingTTSCallbacks


class InterruptibleStreamingCallbacks(StreamingTTSCallbacks):
    """Add a prompt, idempotent abort operation to OVOS pipe playback."""

    def stream_abort(self):
        process = self._process
        self._process = None
        if process is None:
            return
        with suppress(Exception):
            if process.stdin:
                process.stdin.close()
        with suppress(Exception):
            process.terminate()
        try:
            process.wait(timeout=1)
        except (subprocess.TimeoutExpired, OSError):
            with suppress(Exception):
                process.kill()
            with suppress(Exception):
                process.wait(timeout=1)
