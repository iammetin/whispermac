from collections import deque
import threading
import numpy as np
import sounddevice as sd


class AudioRecorder:
    SAMPLE_RATE = 16000
    PRE_ROLL_SECONDS = 0.35
    BLOCK_DURATION_SECONDS = 0.02

    def __init__(self):
        self.frames     = []
        self._lock      = threading.Lock()
        self._recording = False
        self._stream    = None
        self._device    = None
        self._pre_roll = deque()
        self._pre_roll_samples = 0
        self._pre_roll_max_samples = int(self.SAMPLE_RATE * self.PRE_ROLL_SECONDS)

    def warmup(self, device=None):
        """Stream dauerhaft öffnen – start()/stop() setzen nur ein Flag, kein Overhead."""
        self._device = device
        self._open_stream()

    def _open_stream(self):
        if self._stream:
            self._stream.stop()
            self._stream.close()
        self._stream = sd.InputStream(
            samplerate=self.SAMPLE_RATE,
            channels=1,
            dtype="float32",
            device=self._device,
            latency="low",
            blocksize=int(self.SAMPLE_RATE * self.BLOCK_DURATION_SECONDS),
            callback=self._callback,
        )
        self._stream.start()

    def set_device(self, device):
        """Mikrofon wechseln während die App läuft."""
        self._device = device
        self._open_stream()

    def start(self):
        """Aufnahme starten – laufenden Pre-Roll voranstellen, stream läuft bereits."""
        with self._lock:
            self.frames = [chunk.copy() for chunk in self._pre_roll]
            self._recording = True

    def _callback(self, indata, frames, time, status):
        chunk = indata.copy()
        with self._lock:
            self._pre_roll.append(chunk)
            self._pre_roll_samples += len(chunk)
            while self._pre_roll and self._pre_roll_samples > self._pre_roll_max_samples:
                dropped = self._pre_roll.popleft()
                self._pre_roll_samples -= len(dropped)
            if self._recording:
                self.frames.append(chunk)

    def stop(self) -> np.ndarray | None:
        """Aufnahme stoppen und aufgezeichnete Audio-Daten zurückgeben."""
        self._recording = False
        with self._lock:
            if self.frames:
                return np.concatenate(self.frames, axis=0).flatten()
        return None

    def snapshot(self) -> np.ndarray | None:
        """Liefert einen Snapshot der laufenden Aufnahme, ohne sie zu stoppen."""
        with self._lock:
            if not self.frames:
                return None
            chunks = [frame.copy() for frame in self.frames]
        return np.concatenate(chunks, axis=0).flatten()

    @property
    def current_level(self) -> float:
        """RMS-Pegel (0.0–1.0) für Wellenform-Animation."""
        with self._lock:
            if not self.frames:
                return 0.0
            rms = float(np.sqrt(np.mean(self.frames[-1] ** 2)))
            return min(rms * 10, 1.0)
