"""Race-event audio cues.

Generates short sine-wave tones in pure Python and pipes raw PCM into the
first available system audio player: ``paplay`` (PulseAudio /
PipeWire-pulse), ``pw-cat`` (native PipeWire), or ``aplay`` (ALSA).
Fire-and-forget — playback runs in its own subprocess so the Textual loop
keeps moving while the tone plays. If no player is on PATH the calls
silently no-op.
"""

from __future__ import annotations

import array
import logging
import math
import shutil
import subprocess

log = logging.getLogger(__name__)

_SAMPLE_RATE = 44100
_RAMP_SAMPLES = 220  # ~5 ms linear fade in/out to suppress start/stop clicks

# Per-event tone presets. The countdown ticks share a pitch, the GO is an
# octave higher (clearly distinguishable), and lap beeps sit in between so
# they don't get confused with either.
_COUNTDOWN_HZ = 800.0
_GO_HZ = 1600.0
_LAP_HZ = 1200.0


def _player_argv() -> list[str] | None:
    """Pick a player.

    Order: paplay (Pulse / PipeWire-pulse) → pw-cat (PipeWire native) →
    aplay (ALSA). pw-cat ships with the ``pipewire`` package on NixOS even
    when ``paplay`` (which lives in the separate ``pulseaudio`` client
    package) isn't installed.
    """
    if shutil.which("paplay"):
        return [
            "paplay",
            "--raw",
            f"--rate={_SAMPLE_RATE}",
            "--format=s16le",
            "--channels=1",
        ]
    if shutil.which("pw-cat"):
        return [
            "pw-cat",
            "--playback", "-",
            f"--rate={_SAMPLE_RATE}",
            "--format=s16",
            "--channels=1",
        ]
    if shutil.which("aplay"):
        return [
            "aplay",
            "-q",
            "-t", "raw",
            "-f", "S16_LE",
            "-r", str(_SAMPLE_RATE),
            "-c", "1",
        ]
    return None


def _tone_pcm(freq_hz: float, duration_s: float, volume: float = 0.4) -> bytes:
    n = max(1, int(_SAMPLE_RATE * duration_s))
    amp = 32767.0 * volume
    angular = 2.0 * math.pi * freq_hz / _SAMPLE_RATE
    sin = math.sin
    samples = [int(sin(angular * i) * amp) for i in range(n)]
    ramp = min(_RAMP_SAMPLES, n // 2)
    for i in range(ramp):
        scale = i / ramp
        samples[i] = int(samples[i] * scale)
        samples[n - 1 - i] = int(samples[n - 1 - i] * scale)
    return array.array("h", samples).tobytes()


def play_tone(freq_hz: float, duration_s: float) -> None:
    """Play a brief sine-wave tone without blocking the caller."""
    argv = _player_argv()
    if argv is None:
        return
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        log.warning("audio: failed to spawn %s: %s", argv[0], exc)
        return
    if proc.stdin is None:
        return
    try:
        proc.stdin.write(_tone_pcm(freq_hz, duration_s))
        proc.stdin.close()
    except BrokenPipeError:
        pass


def play_countdown_beep() -> None:
    play_tone(_COUNTDOWN_HZ, 0.3)


def play_go_beep() -> None:
    play_tone(_GO_HZ, 0.5)


def play_lap_beep() -> None:
    play_tone(_LAP_HZ, 0.1)
