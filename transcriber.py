import numpy as np
import mlx_whisper


class Transcriber:
    def __init__(self, model_path: str):
        self.model_path = model_path
        self._model_loaded = False

    def preload(self):
        """Modell beim Start einmalig laden (warm-up)."""
        print(f"Lade Modell: {self.model_path}")
        silence = np.zeros(16000, dtype=np.float32)
        mlx_whisper.transcribe(silence, path_or_hf_repo=self.model_path)
        self._model_loaded = True
        print("Modell geladen.")

    def transcribe(self, audio: np.ndarray, language: str = None, task: str = "transcribe") -> str:
        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self.model_path,
            language=language,
            task=task,
            word_timestamps=False,
        )
        text = result.get("text", "").strip()
        # Abschließenden Punkt entfernen den Whisper automatisch hinzufügt
        if text.endswith("."):
            text = text[:-1].rstrip()
        return text
