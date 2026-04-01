import threading
import numpy as np
import sounddevice as sd


class AudioRecorder:
    SAMPLE_RATE = 16000

    def __init__(self):
        self.frames     = []
        self._lock      = threading.Lock()
        self._recording = False
        self._stream    = None
        self._device    = None

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
            callback=self._callback,
        )
        self._stream.start()

    def set_device(self, device):
        """Mikrofon wechseln während die App läuft."""
        self._device = device
        self._open_stream()

    def start(self):
        """Aufnahme starten – nur Flag setzen, stream läuft bereits."""
        with self._lock:
            self.frames = []
            self._recording = True

    def _callback(self, indata, frames, time, status):
        if self._recording:
            with self._lock:
                self.frames.append(indata.copy())

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
