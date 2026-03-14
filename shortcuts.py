"""
WhisperMac – Kürzel-Verwaltung (Laden, Speichern, Anwenden)
"""
import json
import os
import re

SHORTCUTS_FILE = os.path.expanduser("~/.whispermac_shortcuts.json")


def load_shortcuts() -> dict:
    if os.path.exists(SHORTCUTS_FILE):
        try:
            with open(SHORTCUTS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_shortcuts(shortcuts: dict) -> None:
    with open(SHORTCUTS_FILE, "w", encoding="utf-8") as f:
        json.dump(shortcuts, f, ensure_ascii=False, indent=2)


def apply_shortcuts(text: str, shortcuts: dict) -> str:
    # Längere Phrasen zuerst ersetzen (verhindert Teilersetzungen)
    for word in sorted(shortcuts.keys(), key=len, reverse=True):
        replacement = shortcuts[word]
        if word:
            text = re.sub(re.escape(word), replacement, text, flags=re.IGNORECASE)
    text = _clean_duplicate_punctuation(text)
    text = _capitalize_after_punctuation(text)
    return text


def _capitalize_after_punctuation(text: str) -> str:
    """Großschreibung nach Satzzeichen, die durch Kürzel entstanden sind.
    Whisper kennt das Kürzel nicht → schreibt den Folgesatz klein.
    Nach der Ersetzung wird der erste Buchstabe nachträglich großgeschrieben.
    """
    return re.sub(
        r'([\.!?:]\s+)([a-zäöüà-ÿ])',
        lambda m: m.group(1) + m.group(2).upper(),
        text,
    )


def _clean_duplicate_punctuation(text: str) -> str:
    """Bereinigt Satzzeichen-Kollisionen zwischen Whisper-Auto-Interpunktion und Kürzeln.
    Whisper fügt oft Kommas um Pausen ein – diese werden entfernt wenn ein
    anderes Satzzeichen direkt daneben steht.
    """
    # Komma VOR einem anderen Satzzeichen entfernen: ", :" → ":"
    text = re.sub(r',\s*([\.!?;:])', r'\1', text)
    # Komma NACH einem anderen Satzzeichen entfernen: ":," → ":"
    text = re.sub(r'([\.!?;:])\s*,', r'\1', text)
    # Gleiches Satzzeichen doppelt (mit Leerzeichen): ". ." → "."
    text = re.sub(r'([\.!?;:])\s+\1', r'\1', text)
    # Doppelkomma: ", ," → ","
    text = re.sub(r',\s+,', ',', text)
    # Komma VOR öffnender Klammer entfernen: ", (" → " ("
    text = re.sub(r',(\s*[\(\[\{])', r'\1', text)
    # Komma NACH öffnender Klammer entfernen: "(, " → "("
    text = re.sub(r'([\(\[\{])\s*,\s*', r'\1', text)
    # Komma VOR schließender Klammer entfernen: ", )" → ")"
    text = re.sub(r'\s*,\s*([\)\]\}])', r'\1', text)
    # Leerzeichen VOR Satzzeichen entfernen: " :" → ":"
    text = re.sub(r' +([\.!?:,;])', r'\1', text)
    # Punkt nach stärkerem Satzzeichen entfernen: ":." "!." "?." → ":' "!" "?"
    text = re.sub(r'([!?:])\s*\.', r'\1', text)
    return text
