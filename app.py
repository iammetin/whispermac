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
    CGEventCreateKeyboardEvent,
    CGEventGetFlags,
    CGEventGetIntegerValueField,
    CGEventPost,
    CGEventSetFlags,
    CGEventTapCreate,
    CGEventTapEnable,
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    kCFRunLoopCommonModes,
    kCGEventFlagsChanged,
    kCGEventFlagMaskAlternate,
    kCGEventFlagMaskSecondaryFn,
    kCGEventKeyDown,
    kCGHeadInsertEventTap,
    kCGHIDEventTap,
    kCGKeyboardEventKeycode,
    kCGSessionEventTap,
)

F13_KEYCODE = 105
F14_KEYCODE = 179

from overlay import RecordingOverlay
from permissions import ensure_permissions
from recorder import AudioRecorder
from shortcuts import apply_shortcuts, load_shortcuts
from shortcuts_window import ShortcutsWindowController
from transcriber import Transcriber
from workflows import execute_action, load_workflows, split_by_triggers
from workflows_window import WorkflowsWindowController

# Pfade: funktioniert sowohl als Skript als auch als gebaute .app
if getattr(sys, "frozen", False):
    MODEL_PATH    = os.path.expanduser("~/WhisperMac/models/whisper-large-v3-turbo")
    MENUBAR_ICON  = os.path.expanduser("~/WhisperMac/Assets/menubar.png")
else:
    BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
    MODEL_PATH    = os.path.join(BASE_DIR, "models", "whisper-large-v3-turbo")
    MENUBAR_ICON  = os.path.join(BASE_DIR, "Assets", "menubar.png")

FN_FLAG      = kCGEventFlagMaskSecondaryFn   # 0x800000
HISTORY_MAX  = 5
LANG_OPTIONS = [
    (None, "Auto"),
    ("de", "Deutsch"),
    ("en", "English"),
    ("tr", "Türkçe"),
    ("fr", "Français"),
    ("es", "Español"),
    ("it", "Italiano"),
]


class WhisperMacApp(rumps.App):

    def __init__(self):
        super().__init__("", quit_button=None)
        self.icon     = MENUBAR_ICON
        self.template = True   # passt sich Dark/Light Mode an

        self.recorder    = AudioRecorder()
        self.transcriber = Transcriber(MODEL_PATH)
        self.overlay     = RecordingOverlay()

        self._is_recording    = False
        self._fn_pressed      = False
        self.language         = None
        self._history         = []   # letzte Transkriptionen (neueste zuerst)
        self._f13_last_tap    = 0.0
        self._transcribe_lock = threading.Lock()
        self._shortcuts_win   = ShortcutsWindowController.alloc().init()
        self._workflows_win   = WorkflowsWindowController.alloc().init()

        # ── Status-Zeile ──────────────────────────────────────────────────
        self._status_item = rumps.MenuItem("Lade Modell…")
        self._status_item.set_callback(None)

        # ── Verlauf (letzte 5 Transkriptionen) ────────────────────────────
        self._hist_header = rumps.MenuItem("Zuletzt transkribiert:")
        self._hist_header.set_callback(None)
        self._history_items = [
            rumps.MenuItem("", callback=self._on_history_click)
            for _ in range(HISTORY_MAX)
        ]

        # ── Sprache-Untermenü ─────────────────────────────────────────────
        self._lang_submenu    = rumps.MenuItem("Sprache")
        self._lang_menu_items = {}
        for code, label in LANG_OPTIONS:
            item = rumps.MenuItem(label, callback=self._on_lang_select)
            self._lang_submenu[label] = item
            self._lang_menu_items[code] = item

        # ── Menü zusammenbauen ────────────────────────────────────────────
        menu = [self._status_item, None, self._hist_header]
        menu.extend(self._history_items)
        menu.extend([None, self._lang_submenu,
                     rumps.MenuItem("Kürzel…",    callback=self._on_shortcuts),
                     rumps.MenuItem("Workflows…", callback=self._on_workflows),
                     None,
                     rumps.MenuItem("Beenden", callback=rumps.quit_application)])
        self.menu = menu

        # Verlauf initial ausblenden
        self._hist_header._menuitem.setHidden_(True)
        for item in self._history_items:
            item._menuitem.setHidden_(True)

        # Häkchen bei "Auto" setzen
        self._lang_menu_items[None]._menuitem.setState_(1)   # NSOnState

        # Dock-Icon aktivieren
        rumps.Timer(self._show_dock_icon, 0.2).start()

        # Erst Berechtigungen prüfen, dann Modell laden
        ensure_permissions(self._on_permissions_granted)

    # ── Modell laden ──────────────────────────────────────────────────────

    def _show_dock_icon(self, timer):
        AppKit.NSApplication.sharedApplication().setActivationPolicy_(
            AppKit.NSApplicationActivationPolicyRegular
        )
        timer.stop()

    def _on_permissions_granted(self):
        threading.Thread(target=self._preload_model, daemon=True).start()

    def _preload_model(self):
        self.recorder.warmup()
        self.overlay.prebuild()
        self.transcriber.preload()
        self._set_ui(status="Bereit – fn halten zum Aufnehmen")
        self._start_fn_listener()
        # Kurz "Bereit" neben dem Icon anzeigen, dann wieder ausblenden
        def _show_ready():
            self.title = " Bereit"
        def _hide_ready():
            self.title = ""
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(_show_ready)
        threading.Timer(3.0, lambda: AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(_hide_ready)).start()

    # ── UI-Update (thread-safe) ───────────────────────────────────────────

    def _set_ui(self, status=None):
        def _update():
            if status is not None:
                self._status_item.title = status
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(_update)

    # ── fn-Taste abfangen ─────────────────────────────────────────────────

    def _start_fn_listener(self):
        def _callback(proxy, event_type, event, refcon):
            try:
                if event_type == kCGEventKeyDown:
                    kc = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
                    if kc == F13_KEYCODE:
                        now = time.time()
                        if now - self._f13_last_tap < 0.4:
                            self._f13_last_tap = 0.0
                            self._delete_last_word()
                            self._delete_last_word()
                            self._delete_last_word()
                        else:
                            self._f13_last_tap = now
                            self._delete_last_word()
                        return None
                    if kc == F14_KEYCODE:
                        self._undo()
                        return None
                else:
                    flags   = CGEventGetFlags(event)
                    fn_down = bool(flags & FN_FLAG)
                    if fn_down and not self._fn_pressed:
                        self._fn_pressed = True
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
            (1 << kCGEventFlagsChanged) | (1 << kCGEventKeyDown),
            _callback,
            None,
        )

        if tap is None:
            print(
                "\n⚠  Accessibility-Berechtigung fehlt!\n"
                "→  Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen\n"
                "→  Terminal (oder deine App) hinzufügen und neu starten.\n"
            )
            self._set_ui(status="⚠ Berechtigung fehlt – siehe Terminal")
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
        self._status_item.title = "Aufnahme läuft…"
        self.recorder.start()
        self.overlay.show(lambda: self.recorder.current_level)

    def _on_fn_release(self):
        if not self._is_recording:
            return
        self.overlay.hide()
        self._status_item.title = "Transkribiere…"
        threading.Thread(target=self._transcribe_and_insert, daemon=True).start()

    def _transcribe_and_insert(self):
        audio = self.recorder.stop()
        self._is_recording = False

        if audio is None or len(audio) < int(AudioRecorder.SAMPLE_RATE * 0.8):
            self._set_ui(status="Bereit – fn halten zum Aufnehmen")
            return

        if not self._transcribe_lock.acquire(blocking=False):
            # Transkription läuft bereits – Aufnahme verwerfen
            self._set_ui(status="Bereit – fn halten zum Aufnehmen")
            return

        try:
            text = self.transcriber.transcribe(audio, language=self.language)
            if text and not self._is_hallucination(text):
                self._insert_with_workflows(text)
                self._add_to_history(text)
        finally:
            self._transcribe_lock.release()
            self._set_ui(status="Bereit – fn halten zum Aufnehmen")

    # ── Text einfügen (mit Workflow-Unterstützung) ────────────────────────

    def _insert_with_workflows(self, text: str):
        # Workflows zuerst auf Original-Text (verhindert rstrip-Konflikt mit Kürzeln)
        workflows = load_workflows()
        segments  = split_by_triggers(text, workflows)
        shortcuts = load_shortcuts()

        pb      = AppKit.NSPasteboard.generalPasteboard()
        saved   = pb.stringForType_(AppKit.NSPasteboardTypeString)

        for i, (seg_text, workflow) in enumerate(segments):
            is_last   = (i == len(segments) - 1)
            seg_text  = apply_shortcuts(seg_text, shortcuts)
            to_insert = (seg_text + " ") if (seg_text and is_last) else seg_text
            if to_insert:
                pb.clearContents()
                pb.setString_forType_(to_insert, AppKit.NSPasteboardTypeString)
                time.sleep(0.05)
                subprocess.run([
                    "osascript", "-e",
                    'tell application "System Events" to keystroke "v" using command down',
                ])
            if workflow:
                time.sleep(0.08)
                execute_action(workflow.get("action", ""))
                time.sleep(0.08)

        if saved:
            time.sleep(0.15)
            pb.clearContents()
            pb.setString_forType_(saved, AppKit.NSPasteboardTypeString)

    def _insert_text(self, text: str):
        pb = AppKit.NSPasteboard.generalPasteboard()
        old_text = pb.stringForType_(AppKit.NSPasteboardTypeString)

        pb.clearContents()
        pb.setString_forType_(text, AppKit.NSPasteboardTypeString)

        time.sleep(0.05)
        subprocess.run([
            "osascript", "-e",
            'tell application "System Events" to keystroke "v" using command down',
        ])

        if old_text:
            time.sleep(0.35)
            pb.clearContents()
            pb.setString_forType_(old_text, AppKit.NSPasteboardTypeString)

    # ── Verlauf ───────────────────────────────────────────────────────────

    def _add_to_history(self, text: str):
        self._history.insert(0, text)
        if len(self._history) > HISTORY_MAX:
            self._history = self._history[:HISTORY_MAX]

        def _update():
            self._hist_header._menuitem.setHidden_(False)
            for i, item in enumerate(self._history_items):
                if i < len(self._history):
                    full  = self._history[i]
                    short = (full[:55] + "…") if len(full) > 55 else full
                    item.title      = short
                    item._full_text = full
                    item._menuitem.setHidden_(False)
                else:
                    item._menuitem.setHidden_(True)

        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(_update)

    def _on_history_click(self, sender):
        text = getattr(sender, "_full_text", sender.title)
        pb = AppKit.NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, AppKit.NSPasteboardTypeString)

    # ── Letztes Wort löschen (F13) ────────────────────────────────────────

    # ── Halluzinations-Filter ─────────────────────────────────────────────

    _HALLUCINATIONS = {
        "thank you", "thank you.", "thanks for watching", "thanks for watching.",
        "thank you for watching", "thank you for watching.",
    }

    def _is_hallucination(self, text: str) -> bool:
        return text.strip().lower() in self._HALLUCINATIONS

    def _delete_last_word(self):
        """Schickt Option+Delete direkt über Quartz – kein Prozess, kein Delay."""
        KEY_DELETE = 51
        down = CGEventCreateKeyboardEvent(None, KEY_DELETE, True)
        CGEventSetFlags(down, kCGEventFlagMaskAlternate)
        CGEventPost(kCGHIDEventTap, down)
        up = CGEventCreateKeyboardEvent(None, KEY_DELETE, False)
        CGEventSetFlags(up, kCGEventFlagMaskAlternate)
        CGEventPost(kCGHIDEventTap, up)

    def _undo(self):
        """Schickt Cmd+Z direkt über Quartz."""
        from Quartz import kCGEventFlagMaskCommand
        KEY_Z = 6
        down = CGEventCreateKeyboardEvent(None, KEY_Z, True)
        CGEventSetFlags(down, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, down)
        up = CGEventCreateKeyboardEvent(None, KEY_Z, False)
        CGEventSetFlags(up, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, up)

    # ── Kürzel & Workflows ────────────────────────────────────────────────

    def _on_shortcuts(self, sender):
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
            self._shortcuts_win.show
        )

    def _on_workflows(self, sender):
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
            self._workflows_win.show
        )

    # ── Sprache ───────────────────────────────────────────────────────────

    def _on_lang_select(self, sender):
        for code, label in LANG_OPTIONS:
            self._lang_menu_items[code]._menuitem.setState_(0)
        for code, label in LANG_OPTIONS:
            if sender.title == label:
                self.language = code
                self._lang_menu_items[code]._menuitem.setState_(1)
                break


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
