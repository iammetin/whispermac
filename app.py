"""
WhisperMac – Lokale Sprache-zu-Text Mac-App
fn-Taste halten → aufnehmen → loslassen → Text wird eingefügt
"""
import os
import subprocess
import sys
import threading
import time

# Frühes Logging – damit wir sehen was beim Start passiert
import logging
logging.basicConfig(
    filename="/tmp/whispermac.log",
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)
logging.info("=== WhisperMac startet ===")
logging.info(f"Python: {sys.executable}")
logging.info(f"sys.frozen: {getattr(sys, 'frozen', False)}")

import AppKit
import rumps
logging.info("AppKit + rumps importiert")
from CoreFoundation import CFRunLoopGetMain
from Quartz import (
    CGEventGetFlags,
    CGEventTapCreate,
    CGEventTapEnable,
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    kCFRunLoopCommonModes,
    kCGEventFlagsChanged,
    kCGEventFlagMaskSecondaryFn,
    kCGHeadInsertEventTap,
    kCGSessionEventTap,
)

from overlay import RecordingOverlay
from permissions import ensure_permissions
from recorder import AudioRecorder
from transcriber import Transcriber

# Modell-Pfad: funktioniert sowohl als Skript als auch als gebaute .app
if getattr(sys, "frozen", False):
    # Läuft als .app-Bundle → Modell liegt in ~/WhisperMac/models/
    MODEL_PATH = os.path.expanduser("~/WhisperMac/models/whisper-large-v3-turbo")
else:
    # Läuft als Skript (Entwicklung)
    BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
    MODEL_PATH = os.path.join(BASE_DIR, "models", "whisper-large-v3-turbo")

FN_FLAG = kCGEventFlagMaskSecondaryFn   # 0x800000


class WhisperMacApp(rumps.App):

    def __init__(self):
        super().__init__("◌", quit_button=None)

        self.recorder     = AudioRecorder()
        self.transcriber  = Transcriber(MODEL_PATH)
        self.overlay      = RecordingOverlay()

        self._is_recording = False
        self._fn_pressed   = False
        self.language      = None

        self._status_item = rumps.MenuItem("Lade Modell…")
        self._status_item.set_callback(None)
        self._lang_item = rumps.MenuItem("Sprache: Auto", callback=self._cycle_language)

        self.menu = [
            self._status_item,
            None,
            self._lang_item,
            None,
            rumps.MenuItem("Beenden", callback=rumps.quit_application),
        ]

        # Erst Berechtigungen prüfen, dann Modell laden
        ensure_permissions(self._on_permissions_granted)

    # ── Modell laden ──────────────────────────────────────────────────────

    def _on_permissions_granted(self):
        threading.Thread(target=self._preload_model, daemon=True).start()

    def _preload_model(self):
        # Audio-Subsystem vorwärmen (eliminiert Verzögerung beim ersten Start)
        self.recorder.warmup()
        # Overlay-Fenster vorbauen damit es sofort erscheint
        self.overlay.prebuild()
        self.transcriber.preload()
        self._set_ui(title="⬤", status="Bereit – fn halten zum Aufnehmen")
        self._start_fn_listener()

    # ── UI-Update (thread-safe) ───────────────────────────────────────────

    def _set_ui(self, title=None, status=None):
        def _update():
            if title is not None:
                self.title = title
            if status is not None:
                self._status_item.title = status
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(_update)

    # ── fn-Taste abfangen ─────────────────────────────────────────────────

    def _start_fn_listener(self):
        def _callback(proxy, event_type, event, refcon):
            try:
                flags   = CGEventGetFlags(event)
                fn_down = bool(flags & FN_FLAG)

                if fn_down and not self._fn_pressed:
                    self._fn_pressed = True
                    # Callback läuft bereits auf dem Main-Thread → direkt aufrufen
                    self._on_fn_press()
                elif not fn_down and self._fn_pressed:
                    self._fn_pressed = False
                    self._on_fn_release()
            except Exception as e:
                print(f"fn-Listener Fehler: {e}")
            return event

        tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            0,
            1 << kCGEventFlagsChanged,
            _callback,
            None,
        )

        if tap is None:
            print(
                "\n⚠  Accessibility-Berechtigung fehlt!\n"
                "→  Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen\n"
                "→  Terminal (oder deine App) hinzufügen und neu starten.\n"
            )
            self._set_ui(title="⚠", status="⚠ Berechtigung fehlt – siehe Terminal")
            return

        source = CFMachPortCreateRunLoopSource(None, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), source, kCFRunLoopCommonModes)
        CGEventTapEnable(tap, True)
        print("fn-Listener aktiv.")

    # ── Aufnahme-Workflow ─────────────────────────────────────────────────

    def _on_fn_press(self):
        if self._is_recording:
            return
        self._is_recording = True
        self.title = "🔴"
        self._status_item.title = "Aufnahme läuft…"
        self.recorder.start()
        self.overlay.show(lambda: self.recorder.current_level)

    def _on_fn_release(self):
        if not self._is_recording:
            return
        self.overlay.hide()
        self.title = "◌"
        self._status_item.title = "Transkribiere…"
        threading.Thread(target=self._transcribe_and_insert, daemon=True).start()

    def _transcribe_and_insert(self):
        audio = self.recorder.stop()
        self._is_recording = False

        # Zu kurze Aufnahme ignorieren (< 0.3 s)
        if audio is None or len(audio) < int(AudioRecorder.SAMPLE_RATE * 0.3):
            self._set_ui(title="⬤", status="Bereit – fn halten zum Aufnehmen")
            return

        text = self.transcriber.transcribe(audio, language=self.language)

        if text:
            self._insert_text(text)

        self._set_ui(title="⬤", status="Bereit – fn halten zum Aufnehmen")

    # ── Text einfügen ─────────────────────────────────────────────────────

    def _insert_text(self, text: str):
        pb = AppKit.NSPasteboard.generalPasteboard()

        # Alten Clipboard-Inhalt sichern
        old_text = pb.stringForType_(AppKit.NSPasteboardTypeString)

        # Neuen Text in Clipboard
        pb.clearContents()
        pb.setString_forType_(text, AppKit.NSPasteboardTypeString)

        # Kurz warten, dann Cmd+V senden
        time.sleep(0.05)
        subprocess.run([
            "osascript", "-e",
            'tell application "System Events" to keystroke "v" using command down',
        ])

        # Alten Inhalt wiederherstellen
        if old_text:
            time.sleep(0.35)
            pb.clearContents()
            pb.setString_forType_(old_text, AppKit.NSPasteboardTypeString)

    # ── Sprache wechseln ──────────────────────────────────────────────────

    def _cycle_language(self, sender):
        options = [
            (None,  "Auto"),
            ("de",  "Deutsch"),
            ("en",  "English"),
            ("tr",  "Türkçe"),
            ("fr",  "Français"),
            ("es",  "Español"),
            ("it",  "Italiano"),
        ]
        codes  = [o[0] for o in options]
        labels = [o[1] for o in options]
        idx      = codes.index(self.language) if self.language in codes else 0
        next_idx = (idx + 1) % len(options)
        self.language = codes[next_idx]
        self._lang_item.title = f"Sprache: {labels[next_idx]}"


if __name__ == "__main__":
    logging.info("Starte WhisperMacApp...")
    try:
        app = WhisperMacApp()
        logging.info("WhisperMacApp erstellt, starte run()...")
        app.run()
        logging.info("run() beendet")
    except Exception as e:
        logging.exception(f"FEHLER: {e}")
        raise
