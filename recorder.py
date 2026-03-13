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

    def warmup(self):
        """Stream dauerhaft öffnen – start()/stop() setzen nur ein Flag, kein Overhead."""
        self._stream = sd.InputStream(
            samplerate=self.SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

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

    @property
    def current_level(self) -> float:
        """RMS-Pegel (0.0–1.0) für Wellenform-Animation."""
        with self._lock:
            if not self.frames:
                return 0.0
            rms = float(np.sqrt(np.mean(self.frames[-1] ** 2)))
            return min(rms * 10, 1.0)
