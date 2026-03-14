"""
WhisperMac – Lokale Sprache-zu-Text Mac-App
fn-Taste halten → aufnehmen → loslassen → Text wird eingefügt
"""
import json
import os
import subprocess
import sounddevice as sd
from deep_translator import GoogleTranslator
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
    CGDisplayHideCursor,
    CGDisplayShowCursor,
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
    kCGEventFlagMaskCommand,
    kCGEventFlagMaskSecondaryFn,
    kCGEventKeyDown,
    kCGEventKeyUp,
    kCGHeadInsertEventTap,
    kCGHIDEventTap,
    kCGKeyboardEventKeycode,
    kCGSessionEventTap,
)

# kCGDirectMainDisplay = 0 (not exported by all PyObjC versions)
kCGDirectMainDisplay = 0

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

SETTINGS_FILE = os.path.expanduser("~/.whispermac_settings.json")
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
TRANSLATE_OPTIONS = [
    (None, "Aus"),
    ("de", "→ Deutsch"),
    ("en", "→ Englisch"),
    ("tr", "→ Türkisch"),
    ("fr", "→ Französisch"),
    ("es", "→ Spanisch"),
    ("it", "→ Italienisch"),
    ("pt", "→ Portugiesisch"),
    ("nl", "→ Niederländisch"),
    ("pl", "→ Polnisch"),
    ("ru", "→ Russisch"),
    ("ja", "→ Japanisch"),
    ("zh-CN", "→ Chinesisch"),
    ("ar", "→ Arabisch"),
]


def _list_input_devices():
    """Gibt Liste von (index, name) aller Mikrofon-Geräte zurück."""
    result = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            result.append((i, d["name"]))
    return result


class _TranscriptionSpinner:
    """Ersetzt den Cursor durch einen modernen Glas-Spinner während der Transkription."""

    SIZE = 32

    def __init__(self):
        self._window   = None
        self._spinner  = None
        self._tracking = False

    def show(self):
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(self._show_main)

    def hide(self):
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(self._hide_main)

    def _show_main(self):
        if self._window is None:
            self._build()
        self._update_pos()
        self._spinner.startAnimation_(None)
        self._window.orderFrontRegardless()
        CGDisplayHideCursor(kCGDirectMainDisplay)
        self._tracking = True
        threading.Thread(target=self._track_loop, daemon=True).start()

    def _hide_main(self):
        self._tracking = False
        if self._window:
            self._spinner.stopAnimation_(None)
            self._window.orderOut_(None)
        CGDisplayShowCursor(kCGDirectMainDisplay)

    def _track_loop(self):
        """Hält den Spinner am Cursor, solange er läuft."""
        while self._tracking:
            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(self._update_pos)
            time.sleep(0.05)

    def _update_pos(self):
        if self._window:
            S = self.SIZE
            m = AppKit.NSEvent.mouseLocation()
            self._window.setFrameOrigin_(
                AppKit.NSMakePoint(m.x - S / 2, m.y - S / 2)
            )

    def _build(self):
        S = self.SIZE
        win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, S, S),
            AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        win.setLevel_(AppKit.NSStatusWindowLevel + 3)
        win.setOpaque_(False)
        win.setBackgroundColor_(AppKit.NSColor.clearColor())
        win.setIgnoresMouseEvents_(True)
        win.setHasShadow_(True)
        win.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
            AppKit.NSWindowCollectionBehaviorStationary
        )

        # Helles, modernes Glas (Popover-Material – passt sich Dark/Light Mode an)
        fx = AppKit.NSVisualEffectView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, S, S)
        )
        fx.setMaterial_(6)    # Popover – hell, glasig
        fx.setBlendingMode_(0)
        fx.setState_(1)
        fx.setWantsLayer_(True)
        fx.layer().setCornerRadius_(S / 2.0)
        fx.layer().setMasksToBounds_(True)
        win.setContentView_(fx)

        spinner = AppKit.NSProgressIndicator.alloc().initWithFrame_(
            AppKit.NSMakeRect(7, 7, S - 14, S - 14)
        )
        spinner.setStyle_(AppKit.NSProgressIndicatorStyleSpinning)
        spinner.setControlSize_(AppKit.NSControlSizeSmall)
        spinner.setUsesThreadedAnimation_(True)
        fx.addSubview_(spinner)

        self._window  = win
        self._spinner = spinner


class WhisperMacApp(rumps.App):

    def __init__(self):
        super().__init__("", quit_button=None)
        self.icon     = MENUBAR_ICON
        self.template = True   # passt sich Dark/Light Mode an

        self.recorder    = AudioRecorder()
        self.transcriber = Transcriber(MODEL_PATH)
        self.overlay     = RecordingOverlay()
        self._spinner    = _TranscriptionSpinner()

        self._is_recording    = False
        self._fn_pressed      = False
        self.language         = self._load_setting("language", None, {c for c,_ in LANG_OPTIONS})
        self._translate_to    = self._load_setting("translate_to", None, {c for c,_ in TRANSLATE_OPTIONS})
        self._mic_device_name = self._load_raw_setting("mic_device", None)
        self._mic_device_idx  = None  # wird beim Menü-Aufbau aufgelöst
        self._history         = []   # letzte Transkriptionen (neueste zuerst)
        self._last_insert_ends_with_word     = False  # Fallback für iFrames/Browser
        self._last_insert_ends_with_sentence = False  # Fallback: endet mit . ! ?
        self._f13_is_down        = False
        self._f13_hold_timer     = None
        self._f13_hold_triggered = False
        self._f13_last_was_hold  = False
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

        # ── Live übersetzen-Untermenü ──────────────────────────────────────
        self._translate_submenu    = rumps.MenuItem("Live übersetzen")
        self._translate_menu_items = {}
        for code, label in TRANSLATE_OPTIONS:
            item = rumps.MenuItem(label, callback=self._on_translate_select)
            self._translate_submenu[label] = item
            self._translate_menu_items[code] = item

        # ── Mikrofon-Untermenü ────────────────────────────────────────────
        self._mic_submenu    = rumps.MenuItem("Mikrofon")
        self._mic_menu_items = {}  # name -> (index, MenuItem)
        default_item = rumps.MenuItem("System (Standard)", callback=self._on_mic_select)
        self._mic_submenu["System (Standard)"] = default_item
        self._mic_menu_items["System (Standard)"] = (None, default_item)

        for idx, name in _list_input_devices():
            item = rumps.MenuItem(name, callback=self._on_mic_select)
            self._mic_submenu[name] = item
            self._mic_menu_items[name] = (idx, item)
            if name == self._mic_device_name:
                self._mic_device_idx = idx

        # ── Menü zusammenbauen ────────────────────────────────────────────
        menu = [self._status_item, None, self._hist_header]
        menu.extend(self._history_items)
        menu.extend([None, self._mic_submenu, self._lang_submenu, self._translate_submenu,
                     rumps.MenuItem("Kürzel…",    callback=self._on_shortcuts),
                     rumps.MenuItem("Workflows…", callback=self._on_workflows),
                     None,
                     rumps.MenuItem("Beenden", callback=rumps.quit_application)])
        self.menu = menu

        # Verlauf initial ausblenden
        self._hist_header._menuitem.setHidden_(True)
        for item in self._history_items:
            item._menuitem.setHidden_(True)

        # Häkchen bei gespeicherter Auswahl setzen
        self._lang_menu_items[self.language]._menuitem.setState_(1)
        self._translate_menu_items[self._translate_to]._menuitem.setState_(1)
        saved_mic = self._mic_device_name or "System (Standard)"
        if saved_mic in self._mic_menu_items:
            self._mic_menu_items[saved_mic][1]._menuitem.setState_(1)
        else:
            self._mic_menu_items["System (Standard)"][1]._menuitem.setState_(1)

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
        self.recorder.warmup(device=self._mic_device_idx)
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
                        if not self._f13_is_down:
                            self._f13_is_down        = True
                            self._f13_hold_triggered = False
                            self._f13_hold_timer = threading.Timer(0.4, self._on_f13_hold)
                            self._f13_hold_timer.start()
                        return None
                    if kc == F14_KEYCODE:
                        self._undo()
                        return None
                elif event_type == kCGEventKeyUp:
                    kc = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
                    if kc == F13_KEYCODE:
                        self._f13_is_down = False
                        if self._f13_hold_timer:
                            self._f13_hold_timer.cancel()
                            self._f13_hold_timer = None
                        if not self._f13_hold_triggered:
                            self._delete_last_word()
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
                logging.exception(f"fn-Listener Fehler: {e}")
            return event

        tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            0,
            (1 << kCGEventFlagsChanged) | (1 << kCGEventKeyDown) | (1 << kCGEventKeyUp),
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

    _DICTATION_SOUND = (
        "/System/Library/PrivateFrameworks/SpeechObjects.framework"
        "/Versions/A/Frameworks/DictationServices.framework"
        "/Versions/A/Resources/DefaultRecognitionSound.aiff"
    )

    def _play_start_sound(self):
        snd = AppKit.NSSound.soundNamed_("Funk")
        if snd:
            snd.play()

    def _on_fn_press(self):
        if self._is_recording:
            return
        self._is_recording = True
        self._play_start_sound()
        self._status_item.title = "Aufnahme läuft…"
        self.recorder.start()
        self.overlay.show(lambda: self.recorder.current_level)

    def _on_fn_release(self):
        if not self._is_recording:
            return
        self.overlay.hide()
        self._status_item.title = "Transkribiere…"
        self._spinner.show()
        threading.Thread(target=self._transcribe_and_insert, daemon=True).start()

    def _transcribe_and_insert(self):
        audio = self.recorder.stop()
        self._is_recording = False

        if audio is None or len(audio) < int(AudioRecorder.SAMPLE_RATE * 0.8):
            self._spinner.hide()
            self._set_ui(status="Bereit – fn halten zum Aufnehmen")
            return

        if not self._transcribe_lock.acquire(blocking=False):
            self._spinner.hide()
            self._set_ui(status="Bereit – fn halten zum Aufnehmen")
            return

        try:
            text = self.transcriber.transcribe(audio, language=self.language)
            if text and self._translate_to:
                text = GoogleTranslator(
                    source=self.language or "auto",
                    target=self._translate_to,
                ).translate(text) or text
            if text and not self._is_hallucination(text):
                self._insert_with_workflows(text)
                self._add_to_history(text)
        finally:
            self._spinner.hide()
            self._transcribe_lock.release()
            self._set_ui(status="Bereit – fn halten zum Aufnehmen")

    # ── Text einfügen (mit Workflow-Unterstützung) ────────────────────────

    def _get_char_before_cursor(self) -> str:
        """Gibt das Zeichen direkt vor dem Cursor zurück, oder '' wenn unbekannt."""
        try:
            from ApplicationServices import (
                AXUIElementCreateSystemWide,
                AXUIElementCopyAttributeValue,
            )

            system = AXUIElementCreateSystemWide()
            err, focused = AXUIElementCopyAttributeValue(system, "AXFocusedUIElement", None)
            logging.debug(f"AX focused: err={err}, focused={focused is not None}")
            if err != 0 or focused is None:
                return ""
            err, sel_range = AXUIElementCopyAttributeValue(focused, "AXSelectedTextRange", None)
            logging.debug(f"AX sel_range: err={err}, sel_range={sel_range}")
            if err != 0 or sel_range is None:
                return ""
            import re as _re
            m = _re.search(r'location:(\d+)', str(sel_range))
            if not m:
                return ""
            loc = int(m.group(1))
            logging.debug(f"AX cursor loc={loc}")
            if loc == 0:
                return ""
            err, full_text = AXUIElementCopyAttributeValue(focused, "AXValue", None)
            logging.debug(f"AX full_text: err={err}, type={type(full_text)}, loc={loc}")
            if err != 0 or not full_text:
                return ""
            text_str = str(full_text)
            if loc > len(text_str):
                return ""
            char = text_str[loc - 1]
            logging.debug(f"AX char_before={repr(char)}")
            return char
        except Exception as e:
            logging.debug(f"_get_char_before_cursor exception: {e}")
            return ""

    def _insert_with_workflows(self, text: str):
        # Workflows zuerst auf Original-Text (verhindert rstrip-Konflikt mit Kürzeln)
        workflows = load_workflows()
        segments  = split_by_triggers(text, workflows)
        shortcuts = load_shortcuts()

        pb      = AppKit.NSPasteboard.generalPasteboard()
        saved   = pb.stringForType_(AppKit.NSPasteboardTypeString)

        # Smartes Leerzeichen + Großschreibung: vor dem ersten Segment prüfen
        # was direkt vor dem Cursor steht. Fallback für iFrames/Browser.
        char_before = self._get_char_before_cursor()
        if char_before:
            needs_leading_space  = char_before not in (" ", "\n", "\t", "\r")
            after_sentence_end   = char_before in (".", "!", "?")
        else:
            needs_leading_space  = self._last_insert_ends_with_word
            after_sentence_end   = self._last_insert_ends_with_sentence

        last_seg = ""
        for i, (seg_text, workflow) in enumerate(segments):
            seg_text = apply_shortcuts(seg_text, shortcuts)
            if seg_text and i == 0:
                if needs_leading_space:
                    seg_text = " " + seg_text
                if after_sentence_end and seg_text.lstrip() and seg_text.lstrip()[0].islower():
                    stripped = seg_text.lstrip()
                    seg_text = seg_text[: len(seg_text) - len(stripped)] + stripped[0].upper() + stripped[1:]
            to_insert = seg_text
            if to_insert:
                pb.clearContents()
                pb.setString_forType_(to_insert, AppKit.NSPasteboardTypeString)
                time.sleep(0.05)
                subprocess.run([
                    "osascript", "-e",
                    'tell application "System Events" to keystroke "v" using command down',
                ])
                last_seg = to_insert
            if workflow:
                time.sleep(0.08)
                execute_action(workflow.get("action", ""))
                time.sleep(0.08)

        if last_seg:
            self._last_insert_ends_with_word     = last_seg[-1] not in (" ", "\n", "\t", "\r")
            self._last_insert_ends_with_sentence = last_seg[-1] in (".", "!", "?")

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

    def _on_f13_hold(self):
        self._f13_hold_triggered = True
        if self._f13_last_was_hold:
            self._delete_line_above()
        else:
            self._delete_line()
        self._f13_last_was_hold = True

    def _delete_last_word(self):
        self._f13_last_was_hold = False
        KEY_DELETE = 51
        down = CGEventCreateKeyboardEvent(None, KEY_DELETE, True)
        CGEventSetFlags(down, kCGEventFlagMaskAlternate)
        CGEventPost(kCGHIDEventTap, down)
        up = CGEventCreateKeyboardEvent(None, KEY_DELETE, False)
        CGEventSetFlags(up, kCGEventFlagMaskAlternate)
        CGEventPost(kCGHIDEventTap, up)

    def _delete_line(self):
        subprocess.run(["osascript", "-e",
            "tell application \"System Events\" to key code 51 using command down"])

    def _delete_line_above(self):
        subprocess.run(["osascript", "-e", """tell application "System Events"
    key code 126
    key code 124 using command down
    key code 51 using command down
end tell"""])

    def _undo(self):
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

    def _load_raw_setting(self, key, default):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                return json.load(f).get(key, default)
        except Exception:
            return default

    def _load_setting(self, key, default, valid_values):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            val = data.get(key, default)
            return val if val in valid_values else default
        except Exception:
            return default

    def _save_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "language":    self.language,
                    "translate_to": self._translate_to,
                    "mic_device":  self._mic_device_name,
                }, f)
        except Exception:
            pass

    def _on_lang_select(self, sender):
        for code, label in LANG_OPTIONS:
            self._lang_menu_items[code]._menuitem.setState_(0)
        for code, label in LANG_OPTIONS:
            if sender.title == label:
                self.language = code
                self._lang_menu_items[code]._menuitem.setState_(1)
                self._save_settings()
                break

    def _on_mic_select(self, sender):
        for name, (idx, item) in self._mic_menu_items.items():
            item._menuitem.setState_(0)
        name = sender.title
        if name in self._mic_menu_items:
            idx, item = self._mic_menu_items[name]
            item._menuitem.setState_(1)
            self._mic_device_idx  = idx
            self._mic_device_name = None if name == "System (Standard)" else name
            threading.Thread(target=self.recorder.set_device, args=(idx,), daemon=True).start()
            self._save_settings()

    def _on_translate_select(self, sender):
        for code, label in TRANSLATE_OPTIONS:
            self._translate_menu_items[code]._menuitem.setState_(0)
        for code, label in TRANSLATE_OPTIONS:
            if sender.title == label:
                self._translate_to = code
                self._translate_menu_items[code]._menuitem.setState_(1)
                self._save_settings()
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
