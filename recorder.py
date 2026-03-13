import threading
import numpy as np
import sounddevice as sd


class AudioRecorder:
    SAMPLE_RATE = 16000

    def __init__(self):
        self.frames = []
        self._lock = threading.Lock()
        self._stream = None

    def start(self):
        self.frames = []
        self._stream = sd.InputStream(
            samplerate=self.SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, indata, frames, time, status):
        with self._lock:
            self.frames.append(indata.copy())

    def stop(self) -> np.ndarray | None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            if self.frames:
                return np.concatenate(self.frames, axis=0).flatten()
        return None

    @property
    def current_level(self) -> float:
        """RMS-Pegel der aktuellen Aufnahme (0.0 - 1.0) für Wellenform-Animation."""
        with self._lock:
            if not self.frames:
                return 0.0
            latest = self.frames[-1]
            rms = float(np.sqrt(np.mean(latest ** 2)))
            return min(rms * 10, 1.0)
