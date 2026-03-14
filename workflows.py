"""
WhisperMac – Workflow-Engine
Trigger-Phrasen in der Transkription → Tastenkombinationen ausführen
"""
import json
import os
import subprocess
import time

import AppKit

from Quartz import (
    CGEventCreateKeyboardEvent,
    CGEventPost,
    CGEventSetFlags,
    kCGHIDEventTap,
    kCGEventFlagMaskCommand,
    kCGEventFlagMaskShift,
    kCGEventFlagMaskAlternate,
    kCGEventFlagMaskControl,
)

WORKFLOWS_FILE = os.path.expanduser("~/.whispermac_workflows.json")

# Virtuelle Keycodes (macOS)
KEY_MAP = {
    "enter": 36, "return": 36,
    "tab": 48,
    "space": 49,
    "delete": 51, "backspace": 51,
    "escape": 53,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "a": 0,  "b": 11, "c": 8,  "d": 2,  "e": 14, "f": 3,
    "g": 5,  "h": 4,  "i": 34, "j": 38, "k": 40, "l": 37,
    "m": 46, "n": 45, "o": 31, "p": 35, "q": 12, "r": 15,
    "s": 1,  "t": 17, "u": 32, "v": 9,  "w": 13, "x": 7,
    "y": 16, "z": 6,
    "1": 18, "2": 19, "3": 20, "4": 21, "5": 23,
    "6": 22, "7": 26, "8": 28, "9": 25, "0": 29,
}

MOD_MAP = {
    "cmd": kCGEventFlagMaskCommand,
    "command": kCGEventFlagMaskCommand,
    "shift": kCGEventFlagMaskShift,
    "opt": kCGEventFlagMaskAlternate,
    "option": kCGEventFlagMaskAlternate,
    "alt": kCGEventFlagMaskAlternate,
    "ctrl": kCGEventFlagMaskControl,
    "control": kCGEventFlagMaskControl,
}


# ── Laden / Speichern ──────────────────────────────────────────────────────

def load_workflows() -> list:
    if os.path.exists(WORKFLOWS_FILE):
        try:
            with open(WORKFLOWS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_workflows(workflows: list) -> None:
    with open(WORKFLOWS_FILE, "w", encoding="utf-8") as f:
        json.dump(workflows, f, ensure_ascii=False, indent=2)


# ── Aktion ausführen ───────────────────────────────────────────────────────

def _send_key(keycode: int, flags: int = 0) -> None:
    down = CGEventCreateKeyboardEvent(None, keycode, True)
    CGEventSetFlags(down, flags)
    CGEventPost(kCGHIDEventTap, down)
    up = CGEventCreateKeyboardEvent(None, keycode, False)
    CGEventSetFlags(up, flags)
    CGEventPost(kCGHIDEventTap, up)


def _paste_text(text: str) -> None:
    """Fügt beliebigen Text über die Zwischenablage ein."""
    pb = AppKit.NSPasteboard.generalPasteboard()
    saved = pb.stringForType_(AppKit.NSPasteboardTypeString)
    pb.clearContents()
    pb.setString_forType_(text, AppKit.NSPasteboardTypeString)
    time.sleep(0.05)
    subprocess.run([
        "osascript", "-e",
        'tell application "System Events" to keystroke "v" using command down',
    ])
    if saved:
        time.sleep(0.1)
        pb.clearContents()
        pb.setString_forType_(saved, AppKit.NSPasteboardTypeString)


def execute_action(action_str: str) -> None:
    """
    Führt eine Aktion aus. Format: 'enter', 'cmd+b', 'enter,enter', 'cmd+shift+k'
    Mehrere Aktionen durch Komma trennen.
    Sonderformat: 'text:•' fügt beliebigen Text ein.
    """
    for raw in action_str.split(","):
        if not raw.strip():
            continue
        if raw.strip().lower().startswith("text:"):
            _paste_text(raw.lstrip()[5:])  # führende Spaces entfernen, Rest behalten
            time.sleep(0.05)
            continue
        keystroke = raw.strip().lower()
        parts = keystroke.split("+")
        key   = parts[-1]
        mods  = parts[:-1]
        keycode = KEY_MAP.get(key)
        if keycode is None:
            continue
        flags = 0
        for m in mods:
            flags |= MOD_MAP.get(m, 0)
        _send_key(keycode, flags)
        time.sleep(0.02)


# ── Text anhand von Trigger-Phrasen aufteilen ──────────────────────────────

def split_by_triggers(text: str, workflows: list) -> list:
    """
    Teilt den transkribierten Text an Trigger-Phrasen auf.

    Rückgabe: Liste von (text_segment, workflow_oder_None)
    → text_segment wird eingefügt, danach wird die Aktion des Workflows ausgeführt.

    Beispiel:
        "Hallo Welt nächste Zeile Wie geht es dir"
        + Workflow {"trigger": "nächste Zeile", "action": "enter"}
        →  [("Hallo Welt", {…enter…}), ("Wie geht es dir", None)]
    """
    if not workflows:
        return [(text, None)]

    # Längste Trigger zuerst (verhindert Teilersetzungen)
    active = [w for w in workflows if w.get("trigger", "").strip()]
    active.sort(key=lambda w: len(w["trigger"]), reverse=True)

    result   = []
    remaining = text

    while remaining:
        best_pos = len(remaining)
        best_wf  = None

        for wf in active:
            pos = remaining.lower().find(wf["trigger"].lower())
            if 0 <= pos < best_pos:
                best_pos = pos
                best_wf  = wf

        if best_wf is None:
            result.append((remaining.strip(), None))
            break

        # Satzzeichen entfernen, die Whisper rund um den Trigger einfügt
        before = remaining[:best_pos].strip().rstrip(" \t,.")
        result.append((before, best_wf))
        remaining = remaining[best_pos + len(best_wf["trigger"]):]
        remaining = remaining.lstrip(" \t,.")

    return result
