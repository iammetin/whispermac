"""
WhisperMac – Text-Korrektor
Grammatikalische Korrektur des transkribierten Textes per lokalem Qwen-Modell.
"""
import logging
import re

_SYSTEM_PROMPT = (
    "Du bist ein Grammatik-Korrekturdienst für Spracherkennung. "
    "Korrigiere den deutschen Text grammatikalisch (Groß-/Kleinschreibung, "
    "Zeichensetzung, Beugung). Behalte den Originalwortlaut so weit wie möglich. "
    "Antworte ausschließlich mit dem korrigierten Text – keine Erklärungen, "
    "keine Anmerkungen."
)


class TextCorrector:
    def __init__(self, model_path: str):
        self.model_path   = model_path
        self.system_prompt = _SYSTEM_PROMPT
        self._model       = None
        self._tokenizer   = None

    def preload(self):
        logging.info(f"Lade Korrektor-Modell: {self.model_path}")
        from mlx_lm import load
        self._model, self._tokenizer = load(self.model_path)
        logging.info("Korrektor-Modell geladen.")

    def correct(self, text: str, system_prompt: str = None) -> str:
        if self._model is None or self._tokenizer is None:
            return text
        try:
            from mlx_lm import generate
            effective_prompt = system_prompt if system_prompt is not None else self.system_prompt
            messages = [
                {"role": "system", "content": effective_prompt},
                {"role": "user",   "content": text},
            ]
            if getattr(self._tokenizer, "chat_template", None):
                prompt = self._tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
            else:
                prompt = f"{_SYSTEM_PROMPT}\n\n{text}"

            result = generate(
                self._model, self._tokenizer,
                prompt=prompt,
                max_tokens=512,
                verbose=False,
            )
            # Qwen3 Denk-Tokens entfernen (<think>…</think>)
            result = re.sub(r"<think>.*?</think>", "", result, flags=re.DOTALL).strip()
            logging.debug(f"Korrektor: '{text}' → '{result}'")
            return result if result else text
        except Exception as e:
            logging.warning(f"Korrektor-Fehler: {e}")
            return text
