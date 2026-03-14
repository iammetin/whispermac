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
    return text
