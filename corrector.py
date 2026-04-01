"""
WhisperMac – Text-Korrektor
Grammatikalische Korrektur des transkribierten Textes per lokalem Qwen-Modell.
"""
import logging
import re

_GLOBAL_PREFIX = (
    "Antworte ausschließlich mit dem Ergebnis – ohne Einleitung, Erklärung, Kommentar oder Begründung. "
    "Kein Markdown, keine Sternchen, keine Aufzählungszeichen."
)

_SYSTEM_PROMPT = (
    "Du bist ein Grammatik-Korrekturdienst für Spracherkennung. "
    "Korrigiere den deutschen Text grammatikalisch (Groß-/Kleinschreibung, "
    "Zeichensetzung, Beugung). Entferne reine Füllwörter wie 'äh', 'ähm' oder "
    "'hm' sowie offensichtliche Selbstkorrekturen, wenn der Satz dadurch "
    "natürlicher wird. Behalte den Originalwortlaut sonst so weit wie möglich. "
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

    def correct(self, text: str, system_prompt: str = None, max_tokens: int = 16000) -> str:
        if self._model is None or self._tokenizer is None:
            return text
        try:
            from mlx_lm import generate
            from mlx_lm.sample_utils import make_sampler, make_logits_processors
            individual = system_prompt if system_prompt is not None else self.system_prompt
            # System: nur Ausgabe-Verhalten (kurz, kein "Gib den Text aus")
            # User: Anweisung + Text zusammen → Modell versteht Aufgabe korrekt
            messages = [
                {"role": "system", "content": _GLOBAL_PREFIX},
                {"role": "user",   "content": f"{individual}\n\n{text}"},
            ]
            if getattr(self._tokenizer, "chat_template", None):
                try:
                    prompt = self._tokenizer.apply_chat_template(
                        messages, tokenize=False, add_generation_prompt=True,
                        enable_thinking=False,
                    )
                except TypeError:
                    prompt = self._tokenizer.apply_chat_template(
                        messages, tokenize=False, add_generation_prompt=True,
                    )
            else:
                prompt = f"{individual}\n\n{text}"

            sampler = make_sampler(temp=1.0, top_p=1.0, top_k=20, min_p=0.0)
            logits_processors = make_logits_processors(presence_penalty=2.0)
            result = generate(
                self._model, self._tokenizer,
                prompt=prompt,
                max_tokens=max_tokens,
                verbose=False,
                sampler=sampler,
                logits_processors=logits_processors,
            )
            result = re.sub(r"<think>.*?</think>", "", result, flags=re.DOTALL).strip()
            logging.debug(f"Korrektor: '{text}' → '{result}'")
            return result if result else text
        except Exception as e:
            logging.warning(f"Korrektor-Fehler: {e}")
            return text
