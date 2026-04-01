"""
WhisperMac – Lokale Sprache-zu-Text Mac-App
fn-Taste halten → aufnehmen → loslassen → Text wird eingefügt
"""
import json
import os
import re
import subprocess
import sounddevice as sd
from deep_translator import GoogleTranslator
import sys
import threading
import time
import objc

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
import signal
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
    kCGEventFlagMaskControl,
    kCGEventFlagMaskSecondaryFn,
    kCGEventFlagMaskShift,
    kCGEventKeyDown,
    kCGEventKeyUp,
    kCGEventLeftMouseDown,
    kCGHeadInsertEventTap,
    kCGHIDEventTap,
    kCGKeyboardEventKeycode,
    kCGSessionEventTap,
)

# kCGDirectMainDisplay = 0 (not exported by all PyObjC versions)
kCGDirectMainDisplay = 0

F13_KEYCODE = 105
F14_KEYCODE = 107
F15_KEYCODE = 113

from overlay import RecordingOverlay
from permissions import ensure_permissions
from recorder import AudioRecorder
from shortcuts import apply_shortcuts, load_shortcuts
from shortcuts_window import ShortcutsWindowController
from corrector import TextCorrector
from ki_window import PromptManagerWindowController, load_ki_settings
from transcriber import Transcriber
from workflows import execute_action, load_workflows, paste_html, split_by_triggers
from workflows_window import WorkflowsWindowController

# Pfade: funktioniert sowohl als Skript als auch als gebaute .app
if getattr(sys, "frozen", False):
    _MODELS_DIR        = os.path.expanduser("~/WhisperMac/models/whisper-cpp")
    WHISPER_SERVER_BIN = os.path.expanduser("~/WhisperMac/vendor/whisper.cpp-runtime/build/bin/whisper-server")
    CORRECTOR_PATH     = os.path.expanduser("~/WhisperMac/models/llm")
    MENUBAR_ICON       = os.path.expanduser("~/WhisperMac/Assets/menubar.png")
else:
    BASE_DIR           = os.path.dirname(os.path.abspath(__file__))
    _MODELS_DIR        = os.path.join(BASE_DIR, "models", "whisper-cpp")
    WHISPER_SERVER_BIN = os.path.join(BASE_DIR, "vendor", "whisper.cpp-runtime", "build", "bin", "whisper-server")
    CORRECTOR_PATH     = os.path.join(BASE_DIR, "models", "llm")
    MENUBAR_ICON       = os.path.join(BASE_DIR, "Assets", "menubar.png")

def _find_model(models_dir: str) -> str:
    """Nimmt die erste .bin-Datei im Modell-Ordner."""
    bins = sorted(f for f in os.listdir(models_dir) if f.endswith(".bin"))
    if not bins:
        raise FileNotFoundError(f"Kein GGML-Modell (.bin) in {models_dir} gefunden.")
    return os.path.join(models_dir, bins[0])

def _ensure_coreml_encoder(model_path: str) -> None:
    """Stellt sicher, dass ein CoreML-Encoder für das Modell verfügbar ist.
    whisper.cpp leitet den Encoder-Namen ab, indem es .bin UND Quantisierungs-
    Suffixe (_q5_0, _q4_0 usw.) aus dem Dateinamen entfernt.
    Falls der erwartete Encoder fehlt, wird ein Symlink auf einen vorhandenen erstellt."""
    import glob as _glob
    _QUANT_SUFFIXES = ("_q5_0", "_q4_0", "_q8_0", "_q5_1", "_q4_1",
                       "_q2_k", "_q3_k", "_q4_k", "_q5_k", "_q6_k")
    model_dir  = os.path.dirname(model_path)
    model_stem = os.path.basename(model_path)
    if model_stem.endswith(".bin"):
        model_stem = model_stem[:-4]
    for suf in _QUANT_SUFFIXES:
        if model_stem.endswith(suf):
            model_stem = model_stem[:-len(suf)]
            break
    expected = os.path.join(model_dir, model_stem + "-encoder.mlmodelc")
    if os.path.exists(expected):
        return
    available = sorted(_glob.glob(os.path.join(model_dir, "*-encoder.mlmodelc")))
    if not available:
        return
    try:
        os.symlink(available[0], expected)
        logging.info(f"CoreML-Encoder Symlink: {os.path.basename(expected)} → {os.path.basename(available[0])}")
    except Exception as e:
        logging.warning(f"CoreML-Encoder Symlink fehlgeschlagen: {e}")

MODEL_PATH = _find_model(_MODELS_DIR)
_ensure_coreml_encoder(MODEL_PATH)

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
    """Gibt Liste von (index, name) aller Mikrofon-Geräte zurück.

    Nutzt AVFoundation für die Geräteliste (kein Cache, immer aktuell) und
    sounddevice für die Aufnahme-Indizes. Wenn PortAudio ein Gerät noch nicht
    kennt, wird idx=None zurückgegeben → Aufnahme läuft dann über System-Standard.
    """
    # sounddevice-Indizes (PortAudio, ggf. veraltet bei neuen BT-Geräten)
    sd_map: dict[str, int] = {}
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            sd_map[d["name"]] = i
    # AVFoundation: immer frische Geräteliste direkt aus macOS
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        av_names = [
            str(d.localizedName())
            for d in AVCaptureDevice.devicesWithMediaType_(AVMediaTypeAudio)
        ]
    except Exception:
        return [(idx, name) for name, idx in sd_map.items()]

    result = []
    for name in av_names:
        idx = sd_map.get(name)
        if idx is None:
            # Teilübereinstimmung (PortAudio kürzt manchmal lange Namen)
            for sd_name, sd_idx in sd_map.items():
                if name in sd_name or sd_name in name:
                    idx = sd_idx
                    break
        result.append((idx, name))
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


class _AppMenuDelegate(AppKit.NSObject):
    """Feuert wenn das Statusleisten-Menü geöffnet wird."""

    def menuWillOpen_(self, menu):
        if hasattr(self, '_app'):
            self._app._refresh_mic_menu()


class _AppTerminationObserver(AppKit.NSObject):
    def applicationWillTerminate_(self, notification):
        if hasattr(self, "_app"):
            self._app._cleanup_before_exit("NSApplicationWillTerminate")


class WhisperMacApp(rumps.App):

    def __init__(self):
        super().__init__("", quit_button=None)
        self.icon     = MENUBAR_ICON
        self.template = True   # passt sich Dark/Light Mode an
        self._setup_edit_menu()

        self.recorder    = AudioRecorder()
        self.transcriber = Transcriber(MODEL_PATH, WHISPER_SERVER_BIN, use_gpu=True, threads=8)
        self.corrector   = TextCorrector(CORRECTOR_PATH)
        self.overlay     = RecordingOverlay()
        self._spinner    = _TranscriptionSpinner()

        self._is_recording    = False
        self._fn_pressed      = False
        self.language         = self._load_setting("language", None, {c for c,_ in LANG_OPTIONS})
        self._translate_to    = self._load_setting("translate_to", None, {c for c,_ in TRANSLATE_OPTIONS})
        self._live_transcription = bool(self._load_raw_setting("live_transcription", True))
        self._ki_korrektur, ki_live_prompt, ki_auswahl_prompt = load_ki_settings()
        self.corrector.system_prompt = ki_live_prompt
        self._ki_auswahl_prompt      = ki_auswahl_prompt
        self._mic_device_name = self._load_raw_setting("mic_device", None)
        self._mic_device_idx  = None  # wird beim Menü-Aufbau aufgelöst
        self._history         = []   # letzte Transkriptionen (neueste zuerst)
        self._last_insert_ends_with_word     = False  # Fallback für iFrames/Browser
        self._last_insert_ends_with_sentence = False  # Fallback: endet mit . ! ?
        self._f13_is_down        = False
        self._f13_hold_timer     = None
        self._f13_hold_triggered = False
        self._f13_last_was_hold  = False
        self._f14_is_down    = False
        self._f14_press_time = 0.0
        self._f15_tap_timer  = None   # Timer für Doppelklick-Erkennung
        self._fn_press_time         = None   # Zeitpunkt des letzten fn-Drucks
        self._fn_last_release_time  = None   # Zeitpunkt des letzten fn-Loslassens
        self._fn_last_hold_duration = 0.0    # Haltedauer des letzten fn-Drucks
        self._fn_is_double_tap      = False  # Zweiter Tipp eines Doppeltipps
        self._recording_live_active = False
        self._transcribe_lock = threading.Lock()
        self._transcription_seq  = 0   # Jeder fn-Druck erhöht diesen Zähler
        self._live_state_lock = threading.Lock()
        self._live_session    = None
        self._cleanup_lock    = threading.Lock()
        self._did_cleanup     = False
        self._shortcuts_win    = ShortcutsWindowController.alloc().init()
        self._workflows_win    = WorkflowsWindowController.alloc().init()
        self._ki_live_win      = PromptManagerWindowController.alloc().initWithMode_("live")
        self._ki_live_win._on_save    = self._on_ki_live_saved
        self._ki_auswahl_win   = PromptManagerWindowController.alloc().initWithMode_("auswahl")
        self._ki_auswahl_win._on_save = self._on_ki_auswahl_saved

        # ── Status-Zeile ──────────────────────────────────────────────────
        self._status_item = rumps.MenuItem("Lade whisper.cpp…")
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

        self._live_item = rumps.MenuItem("Live-Transkription", callback=self._on_live_toggle)

        # ── Mikrofon-Untermenü ────────────────────────────────────────────
        self._mic_submenu    = rumps.MenuItem("Mikrofon")
        self._mic_menu_items = {}  # name -> (index, MenuItem)
        default_item = rumps.MenuItem("System (Standard)", callback=self._on_mic_select)
        self._mic_submenu["System (Standard)"] = default_item
        self._mic_menu_items["System (Standard)"] = (None, default_item)

        # PortAudio-Cache leeren damit beim Start bereits angeschlossene
        # Kopfhörer / BT-Geräte sofort erkannt werden (wie in _refresh_mic_menu)
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass

        for idx, name in _list_input_devices():
            item = rumps.MenuItem(name, callback=self._on_mic_select)
            self._mic_submenu[name] = item
            self._mic_menu_items[name] = (idx, item)
            if name == self._mic_device_name:
                self._mic_device_idx = idx

        # ── KI-Menü-Einträge ──────────────────────────────────────────────
        self._ki_item         = rumps.MenuItem("KI-Live-Korrektur",    callback=self._on_ki_live_toggle)
        self._ki_auswahl_item = rumps.MenuItem("KI-Auswahl-Korrektur", callback=self._on_ki_auswahl_toggle)

        # ── Menü zusammenbauen ────────────────────────────────────────────
        menu = [self._status_item, None, self._hist_header]
        menu.extend(self._history_items)
        menu.extend([None, self._mic_submenu, self._lang_submenu, self._translate_submenu,
                     self._live_item,
                     self._ki_item,
                     self._ki_auswahl_item,
                     rumps.MenuItem("Kürzel…  (F15)",         callback=self._on_shortcuts),
                     rumps.MenuItem("Workflows…  (F15 F15)", callback=self._on_workflows),
                     None,
                     rumps.MenuItem("Beenden", callback=self._on_quit)])
        self.menu = menu

        # Delegate auf das Hauptmenü – feuert beim Öffnen des Statusleisten-Menüs
        self._menu_delegate = _AppMenuDelegate.alloc().init()
        self._menu_delegate._app = self
        self._menu._menu.setDelegate_(self._menu_delegate)

        self._termination_observer = _AppTerminationObserver.alloc().init()
        self._termination_observer._app = self
        AppKit.NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self._termination_observer,
            "applicationWillTerminate:",
            AppKit.NSApplicationWillTerminateNotification,
            None,
        )
        self._install_signal_handlers()

        # Verlauf initial ausblenden
        self._hist_header._menuitem.setHidden_(True)
        for item in self._history_items:
            item._menuitem.setHidden_(True)

        # Häkchen bei gespeicherter Auswahl setzen
        self._lang_menu_items[self.language]._menuitem.setState_(1)
        self._translate_menu_items[self._translate_to]._menuitem.setState_(1)
        if self._live_transcription:
            self._live_item._menuitem.setState_(1)
        if self._ki_korrektur:
            self._ki_item._menuitem.setState_(1)
        saved_mic = self._mic_device_name or "System (Standard)"
        if saved_mic in self._mic_menu_items:
            self._mic_menu_items[saved_mic][1]._menuitem.setState_(1)
        else:
            self._mic_menu_items["System (Standard)"][1]._menuitem.setState_(1)

        # Dock-Icon aktivieren
        rumps.Timer(self._show_dock_icon, 0.2).start()

        # Erst Berechtigungen prüfen, dann Modell laden
        ensure_permissions(self._on_permissions_granted)

    # ── Edit-Menü (ermöglicht Cmd+V/C/X/Z in Textfeldern) ────────────────

    def _setup_edit_menu(self):
        app      = AppKit.NSApplication.sharedApplication()
        mainMenu = AppKit.NSMenu.alloc().init()

        # Erstes Element muss App-Menü sein (macOS-Konvention)
        app_item = AppKit.NSMenuItem.alloc().init()
        mainMenu.addItem_(app_item)

        # Edit-Menü
        edit_item = AppKit.NSMenuItem.alloc().init()
        edit_menu = AppKit.NSMenu.alloc().initWithTitle_("Edit")

        def _add(title, action, key, extra_mask=0):
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, action, key
            )
            item.setKeyEquivalentModifierMask_(
                AppKit.NSEventModifierFlagCommand | extra_mask
            )
            edit_menu.addItem_(item)

        _add("Undo",       "undo:",      "z")
        _add("Redo",       "redo:",      "z", AppKit.NSEventModifierFlagShift)
        edit_menu.addItem_(AppKit.NSMenuItem.separatorItem())
        _add("Cut",        "cut:",       "x")
        _add("Copy",       "copy:",      "c")
        _add("Paste",      "paste:",     "v")
        _add("Select All", "selectAll:", "a")

        edit_item.setSubmenu_(edit_menu)
        mainMenu.addItem_(edit_item)
        app.setMainMenu_(mainMenu)

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
        self._set_ui(status="Lade whisper.cpp…")
        try:
            self.transcriber.preload()
        except Exception as e:
            logging.exception(f"whisper.cpp konnte nicht geladen werden: {e}")
            self._set_ui(status="⚠ whisper.cpp konnte nicht geladen werden")
            return
        if self._ki_korrektur:
            self._set_ui(status="Lade KI-Korrektor…")
            try:
                self.corrector.preload()
            except Exception as e:
                logging.exception(f"KI-Korrektor konnte nicht geladen werden: {e}")
        self._set_ui(status=self._ready_status())
        self._start_fn_listener()
        # Kurz "Bereit" neben dem Icon anzeigen, dann wieder ausblenden
        def _show_ready():
            self.title = " Bereit"
        def _hide_ready():
            self.title = ""
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(_show_ready)
        threading.Timer(3.0, lambda: AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(_hide_ready)).start()

    def _install_signal_handlers(self):
        def _handle_signal(signum, frame):
            logging.info(f"Signal empfangen: {signum}")
            self._cleanup_before_exit(f"signal {signum}")
            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                lambda: AppKit.NSApplication.sharedApplication().terminate_(None)
            )

        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            try:
                signal.signal(signum, _handle_signal)
            except Exception:
                pass

    def _cleanup_before_exit(self, reason: str):
        with self._cleanup_lock:
            if self._did_cleanup:
                return
            self._did_cleanup = True

        logging.info(f"Cleanup vor App-Ende: {reason}")
        self._is_recording = False
        try:
            self.overlay.hide()
        except Exception:
            pass
        try:
            self.recorder.stop()
        except Exception:
            pass
        try:
            self.transcriber.close()
        except Exception as e:
            logging.exception(f"whisper.cpp Cleanup fehlgeschlagen: {e}")
        try:
            sd.stop()
        except Exception:
            pass
        try:
            sd._terminate()
        except Exception:
            pass

    def _on_quit(self, sender):
        self._cleanup_before_exit("menu quit")
        AppKit.NSApplication.sharedApplication().terminate_(None)

    # ── UI-Update (thread-safe) ───────────────────────────────────────────

    def _ready_status(self) -> str:
        live = "Live: an" if self._live_transcription else "Live: aus"
        ki = "KI: aktiv" if self._ki_korrektur else "KI: aus"
        return f"Bereit – fn halten zum Aufnehmen  |  {live}  |  {ki}  (2× fn zum Umschalten)"

    def _set_ui(self, status=None):
        def _update():
            if status is not None:
                self._status_item.title = status
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(_update)

    _LIVE_TRANSCRIBE_INTERVAL = 0.18
    _LIVE_MIN_AUDIO_SECONDS   = 0.25
    _LIVE_MUTABLE_TAIL_WORDS  = 4
    _LIVE_PAUSE_FINALIZE_SECONDS = 0.35
    _LIVE_LLM_MIN_WORDS       = 8
    _TERMINAL_BUNDLE_IDS = {
        "com.apple.Terminal",
        "com.googlecode.iterm2",
        "com.github.wez.wezterm",
        "com.microsoft.VSCode",
        "com.microsoft.VSCodeInsiders",
        "org.alacritty",
        "co.zeit.hyper",
        "net.kovidgoyal.kitty",
        "dev.warp.Warp",
        "dev.warp.Warp-Stable",
        "dev.warp.Warp-Beta",
        "dev.warp.Warp-Preview",
        "com.mitchellh.ghostty",
    }
    _TERMINAL_NAME_SNIPPETS = (
        "terminal",
        "iterm",
        "wezterm",
        "alacritty",
        "hyper",
        "kitty",
        "warp",
        "ghostty",
    )

    def _clear_mlx_cache(self):
        try:
            import mlx.core as mx
            mx.metal.clear_cache()
        except Exception:
            pass

    def _split_words(self, text: str) -> list[str]:
        return text.strip().split() if text else []

    def _join_words(self, words: list[str]) -> str:
        return " ".join(words).strip()

    def _common_prefix_len(self, left: list[str], right: list[str]) -> int:
        count = 0
        for a, b in zip(left, right):
            if a != b:
                break
            count += 1
        return count

    def _basic_live_cleanup(self, text: str) -> str:
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        text = re.sub(r"\s{2,}", " ", text)
        return text.strip()

    def _trailing_silence_seconds(self, audio) -> float:
        import numpy as np

        if audio is None or len(audio) == 0:
            return 0.0

        frame_samples = max(1, int(AudioRecorder.SAMPLE_RATE * 0.02))
        silence_samples = 0
        end = len(audio)

        while end > 0:
            start = max(0, end - frame_samples)
            chunk = audio[start:end].astype(np.float32)
            rms = float(np.sqrt(np.mean(chunk ** 2)))
            if rms >= self._SILENCE_RMS_THRESHOLD:
                break
            silence_samples += end - start
            end = start

        return silence_samples / AudioRecorder.SAMPLE_RATE

    def _transcribe_audio(self, audio, retry_lowercase: bool = True, live_pass: bool = False) -> str:
        text = self.transcriber.transcribe(audio, language=self.language)
        if retry_lowercase:
            for retry in range(2):
                if not (text and text == text.lower() and len(text.split()) >= 2):
                    break
                logging.info(f"Whisper: alles klein – Versuch {retry + 2}/3")
                if not live_pass:
                    self._set_ui(status=f"Wiederhole… ({retry + 2}/3)")
                text = self.transcriber.transcribe(audio, language=self.language)
        return text

    def _prepare_output_text(self, text: str, final: bool = False) -> str:
        text = (text or "").strip()
        if not text or self._is_hallucination(text):
            return ""
        if not final:
            text = self._basic_live_cleanup(text)
            if not text or self._is_hallucination(text):
                return ""
        word_count = len(self._split_words(text))
        should_run_llm = final or (
            self._ki_korrektur
            and word_count >= self._LIVE_LLM_MIN_WORDS
            and text[-1:] in ".!?"
        )
        if self._ki_korrektur and should_run_llm:
            text = self.corrector.correct(
                text,
                max_tokens=16000 if final else 128,
            ).strip()
            self._clear_mlx_cache()
        if text and self._translate_to:
            try:
                text = (
                    GoogleTranslator(
                        source=self.language or "auto",
                        target=self._translate_to,
                    ).translate(text)
                    or text
                )
            except Exception as e:
                logging.warning(f"Übersetzung fehlgeschlagen: {e}")
        return (text or "").strip()

    def _history_text_from_chunks(self, chunks: list[str]) -> str:
        combined = " ".join(chunk.strip() for chunk in chunks if chunk and chunk.strip()).strip()
        combined = re.sub(r"\s+([,.;:!?])", r"\1", combined)
        return re.sub(r"\s{2,}", " ", combined).strip()

    def _current_insert_context(self) -> tuple[bool, bool]:
        char_before = self._get_char_before_cursor()
        if char_before:
            needs_leading_space = char_before not in (" ", "\n", "\t", "\r")
            after_sentence_end = char_before in (".", "!", "?")
        else:
            needs_leading_space = self._last_insert_ends_with_word
            after_sentence_end = self._last_insert_ends_with_sentence
        return needs_leading_space, after_sentence_end

    def _start_live_session(self, seq: int):
        needs_leading_space, after_sentence_end = self._current_insert_context()
        with self._live_state_lock:
            self._live_session = {
                "seq": seq,
                "passes": 0,
                "prev_words": [],
                "frozen_words": [],
                "displayed_text": "",
                "history_text": "",
                "needs_leading_space": needs_leading_space,
                "after_sentence_end": after_sentence_end,
            }
        threading.Thread(target=self._live_transcribe_loop, args=(seq,), daemon=True).start()

    def _session_uses_live(self, seq: int) -> bool:
        with self._live_state_lock:
            return self._live_session is not None and self._live_session["seq"] == seq

    def _clear_live_session(self, seq: int):
        with self._live_state_lock:
            if self._live_session is not None and self._live_session["seq"] == seq:
                self._live_session = None

    def _live_transcribe_loop(self, seq: int):
        min_samples = int(AudioRecorder.SAMPLE_RATE * self._LIVE_MIN_AUDIO_SECONDS)
        while self._is_recording and self._transcription_seq == seq and self._live_transcription:
            time.sleep(self._LIVE_TRANSCRIBE_INTERVAL)
            if not self._is_recording or self._transcription_seq != seq:
                return
            audio = self.recorder.snapshot()
            if audio is None or len(audio) < min_samples or self._is_silence(audio):
                continue
            pause_finalize = self._trailing_silence_seconds(audio) >= self._LIVE_PAUSE_FINALIZE_SECONDS
            if not self._transcribe_lock.acquire(blocking=False):
                continue
            try:
                if not self._is_recording or self._transcription_seq != seq:
                    return
                text = self._transcribe_audio(audio, retry_lowercase=False, live_pass=True)
                words = self._split_words(text)
                if not words:
                    continue
                with self._live_state_lock:
                    state = self._live_session
                    if state is None or state["seq"] != seq:
                        return
                    state["passes"] += 1
                self._sync_live_text(seq, words, final=False, pause_finalize=pause_finalize)
            except Exception as e:
                logging.exception(f"Live-Transkription fehlgeschlagen: {e}")
            finally:
                self._transcribe_lock.release()

    def _apply_live_context(self, text: str, needs_leading_space: bool, after_sentence_end: bool) -> str:
        if not text:
            return ""
        text = apply_shortcuts(text, load_shortcuts())
        if needs_leading_space:
            text = " " + text
        stripped = text.lstrip()
        if after_sentence_end and stripped and stripped[0].islower():
            text = text[: len(text) - len(stripped)] + stripped[0].upper() + stripped[1:]
        return text

    def _ax_text_length(self, text: str) -> int:
        return int(AppKit.NSString.stringWithString_(text or "").length())

    def _common_prefix_text(self, left: str, right: str) -> str:
        count = 0
        for a, b in zip(left, right):
            if a != b:
                break
            count += 1
        return left[:count]

    def _frontmost_app_identity(self) -> tuple[str, str]:
        try:
            app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return "", ""
            return str(app.bundleIdentifier() or ""), str(app.localizedName() or "")
        except Exception as e:
            logging.debug(f"_frontmost_app_identity exception: {e}")
            return "", ""

    def _is_terminal_target(self) -> bool:
        bundle_id, name = self._frontmost_app_identity()
        bundle_id_lc = bundle_id.lower()
        name_lc = name.lower()
        is_terminal = (
            bundle_id in self._TERMINAL_BUNDLE_IDS
            or any(token in bundle_id_lc for token in self._TERMINAL_NAME_SNIPPETS)
            or any(token in name_lc for token in self._TERMINAL_NAME_SNIPPETS)
        )
        logging.debug(
            "Frontmost app: bundle=%s name=%s terminal=%s",
            bundle_id or "-",
            name or "-",
            is_terminal,
        )
        return is_terminal

    def _should_use_live_for_current_target(self) -> bool:
        if not self._live_transcription:
            return False
        if self._is_terminal_target():
            bundle_id, name = self._frontmost_app_identity()
            logging.info(
                "Live-Transkription für diese Session automatisch deaktiviert: bundle=%s name=%s",
                bundle_id or "-",
                name or "-",
            )
            return False
        return True

    def _post_key(self, keycode: int, flags: int = 0):
        down = CGEventCreateKeyboardEvent(None, keycode, True)
        if flags:
            CGEventSetFlags(down, flags)
        CGEventPost(kCGHIDEventTap, down)
        up = CGEventCreateKeyboardEvent(None, keycode, False)
        if flags:
            CGEventSetFlags(up, flags)
        CGEventPost(kCGHIDEventTap, up)

    def _replace_recent_text_terminal(self, old_tail_len: int, new_tail: str) -> bool:
        if old_tail_len > 0:
            for _ in range(old_tail_len):
                self._post_key(123)  # Pfeil links
                time.sleep(0.003)
            self._post_key(40, kCGEventFlagMaskControl)  # Ctrl+K: bis Zeilenende löschen
            time.sleep(0.01)

        if not new_tail:
            return True

        pb = AppKit.NSPasteboard.generalPasteboard()
        return self._paste_plain_text(new_tail, pb)

    def _replace_recent_text(self, old_text: str, new_text: str) -> bool:
        if self._is_terminal_target():
            old_len = self._ax_text_length(old_text)
            return self._replace_recent_text_terminal(old_len, new_text)

        # Gemeinsamen Präfix bestimmen – nur das geänderte Ende ersetzen
        common_prefix = self._common_prefix_text(old_text, new_text)
        old_tail = old_text[len(common_prefix):]
        new_tail = new_text[len(common_prefix):]
        old_tail_len = self._ax_text_length(old_tail)

        pb = AppKit.NSPasteboard.generalPasteboard()

        if old_tail_len == 0:
            # Nur anhängen – direkt einfügen
            if not new_tail:
                return True
            return self._paste_plain_text(new_tail, pb)

        # Alten Tail per Shift+← selektieren (von Cursor rückwärts)
        # und dann durch neuen Tail ersetzen.
        # Vorteil gegenüber Backspace: die Selektion ersetzt chirurgisch nur
        # den geänderten Teil, ohne den gemeinsamen Präfix zu berühren.
        for _ in range(old_tail_len):
            self._post_key(123, kCGEventFlagMaskShift)  # Shift+←

        if not new_tail:
            self._post_key(51)  # Backspace löscht die Selektion
            return True

        return self._paste_plain_text(new_tail, pb)

    def _update_insert_tracking(self, text: str):
        if not text:
            return
        self._last_insert_ends_with_word = text[-1] not in (" ", "\n", "\t", "\r")
        self._last_insert_ends_with_sentence = text[-1] in (".", "!", "?")

    def _sync_live_text(self, seq: int, words: list[str], final: bool, pause_finalize: bool = False):
        with self._live_state_lock:
            state = self._live_session
            if state is None or state["seq"] != seq:
                return ""
            old_displayed = state["displayed_text"]
            needs_leading_space = state["needs_leading_space"]
            after_sentence_end = state["after_sentence_end"]
            prev_words = state["prev_words"]
            frozen_words = state["frozen_words"]

            if final or pause_finalize:
                render_words = words[:]
                state["frozen_words"] = words[:]
            else:
                if prev_words:
                    stable_len = self._common_prefix_len(prev_words, words)
                else:
                    stable_len = 0
                freeze_upto = max(0, stable_len - self._LIVE_MUTABLE_TAIL_WORDS)
                freeze_upto = min(len(words), freeze_upto)
                if freeze_upto > len(frozen_words):
                    frozen_words = words[:freeze_upto]
                    state["frozen_words"] = frozen_words
                render_words = frozen_words + words[len(frozen_words):]

            state["prev_words"] = words[:]

        raw_text = self._join_words(render_words)
        prepared = self._prepare_output_text(raw_text, final=final)
        display_text = self._apply_live_context(prepared, needs_leading_space, after_sentence_end)
        history_text = display_text.lstrip()
        if display_text == old_displayed:
            with self._live_state_lock:
                state = self._live_session
                if state is not None and state["seq"] == seq:
                    state["history_text"] = history_text
            return history_text
        if not self._replace_recent_text(old_displayed, display_text):
            logging.warning("Live-Ersetzen fehlgeschlagen, behalte bisherigen Anzeige-Text")
            return old_displayed.lstrip()
        self._update_insert_tracking(display_text)
        with self._live_state_lock:
            state = self._live_session
            if state is not None and state["seq"] == seq:
                state["displayed_text"] = display_text
                state["history_text"] = history_text
        return history_text

    def _finalize_live_session(self, seq: int, final_text: str) -> str:
        history_text = self._sync_live_text(seq, self._split_words(final_text), final=True)
        with self._live_state_lock:
            state = self._live_session
            if state is None or state["seq"] != seq:
                return history_text or ""
            history_text = state["history_text"] or history_text
            self._live_session = None
        return history_text or ""

    # ── fn-Taste abfangen ─────────────────────────────────────────────────

    # Keycodes die anzeigen, dass der Cursor bewegt / Text geändert wurde
    _CURSOR_MOVE_KEYCODES = {
        36,          # Return/Enter
        51,          # Backspace/Delete
        117,         # Forward Delete
        123, 124, 125, 126,  # Pfeiltasten
        115, 119,    # Home, End
        116, 121,    # Page Up, Page Down
    }

    def _start_fn_listener(self):
        def _callback(proxy, event_type, event, refcon):
            try:
                if event_type == kCGEventLeftMouseDown and not self._is_recording:
                    self._last_insert_ends_with_word     = False
                    self._last_insert_ends_with_sentence = False
                elif event_type == kCGEventKeyDown:
                    kc = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
                    # Cursor bewegt / Text geändert → Tracking-State zurücksetzen
                    if kc in self._CURSOR_MOVE_KEYCODES and not self._is_recording:
                        self._last_insert_ends_with_word     = False
                        self._last_insert_ends_with_sentence = False
                    if kc == F13_KEYCODE:
                        if not self._f13_is_down:
                            self._f13_is_down        = True
                            self._f13_hold_triggered = False
                            self._f13_hold_timer = threading.Timer(0.4, self._on_f13_hold)
                            self._f13_hold_timer.start()
                        return None
                    if kc == F14_KEYCODE:
                        if not self._f14_is_down:
                            self._f14_is_down    = True
                            self._f14_press_time = time.time()
                            if not self._is_recording:
                                self.recorder.start()
                                self.overlay.show(lambda: self.recorder.current_level)
                            # Modell schon jetzt laden falls nötig – parallel zur Aufnahme
                            if self.corrector._model is None:
                                threading.Thread(target=self._load_corrector_bg, daemon=True).start()
                        return None
                    if kc == F15_KEYCODE:
                        def _handle_f15():
                            # Alles auf dem Main Thread – AppKit-Zugriffe nur hier
                            if (self._shortcuts_win.is_open() or self._workflows_win.is_open()
                                    or self._ki_live_win.is_open() or self._ki_auswahl_win.is_open()):
                                self._shortcuts_win.close()
                                self._workflows_win.close()
                                self._ki_live_win.close()
                                self._ki_auswahl_win.close()
                                return
                            if self._f15_tap_timer is not None:
                                # Zweiter Klick innerhalb 350ms → Workflows öffnen
                                self._f15_tap_timer.cancel()
                                self._f15_tap_timer = None
                                self._workflows_win.show()
                            else:
                                # Erster Klick → kurz warten ob zweiter kommt
                                def _open_shortcuts():
                                    self._f15_tap_timer = None
                                    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                                        self._shortcuts_win.show
                                    )
                                self._f15_tap_timer = threading.Timer(0.35, _open_shortcuts)
                                self._f15_tap_timer.start()
                        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(_handle_f15)
                        return None
                elif event_type == kCGEventKeyUp:
                    kc = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
                    if kc == F14_KEYCODE:
                        self._f14_is_down = False
                        self.overlay.hide()
                        hold_dur = time.time() - self._f14_press_time
                        if hold_dur >= 0.35 and not self._is_recording:
                            # Hold: Audio als Prompt verwenden
                            self._spinner.show()
                            threading.Thread(target=self._on_f14_hold_release, daemon=True).start()
                        else:
                            # Tap: Aufnahme wegwerfen, Standard-Prompt
                            self.recorder.stop()
                            threading.Thread(target=self._on_f14_ai_edit, daemon=True).start()
                        return None
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
            (1 << kCGEventFlagsChanged) | (1 << kCGEventKeyDown) | (1 << kCGEventKeyUp) | (1 << kCGEventLeftMouseDown),
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
        now = time.time()
        # Doppeltipp: zweite schnelle Berührung kurz nach einer kurzen Berührung
        if (self._fn_last_release_time is not None
                and now - self._fn_last_release_time < 0.35
                and self._fn_last_hold_duration < 0.25):
            self._fn_is_double_tap = True
            self._fn_press_time = now
            self._toggle_ki_mode()
            return
        self._fn_is_double_tap = False
        self._fn_press_time = now
        if self._is_recording:
            return
        self._is_recording = True
        self._recording_live_active = False
        self._transcription_seq += 1
        if self._should_use_live_for_current_target():
            self._recording_live_active = True
            self._start_live_session(self._transcription_seq)
        else:
            with self._live_state_lock:
                self._live_session = None
        self._play_start_sound()
        self._status_item.title = "Live-Aufnahme läuft…" if self._recording_live_active else "Aufnahme läuft…"
        self.recorder.start()
        self.overlay.show(lambda: self.recorder.current_level)

    def _on_fn_release(self):
        now = time.time()
        self._fn_last_hold_duration = (now - self._fn_press_time) if self._fn_press_time else 0.0
        self._fn_last_release_time  = now
        if self._fn_is_double_tap:
            self._fn_is_double_tap = False
            return
        if not self._is_recording:
            return
        self.overlay.hide()
        self._status_item.title = (
            "Finalisiere…" if self._session_uses_live(self._transcription_seq) else "Transkribiere…"
        )
        self._spinner.show()
        threading.Thread(target=self._transcribe_and_insert, daemon=True).start()

    def _transcribe_and_insert(self):
        my_seq = self._transcription_seq   # Sequenz beim Start merken
        use_live_mode = self._session_uses_live(my_seq)
        audio  = self.recorder.stop()
        self._is_recording = False
        self._recording_live_active = False

        if audio is None or len(audio) < int(AudioRecorder.SAMPLE_RATE * 0.3):
            if use_live_mode:
                self._clear_live_session(my_seq)
            self._spinner.hide()
            self._set_ui(status=self._ready_status())
            return

        # Auf vorherige Transkription warten (blockend statt überspringen)
        self._transcribe_lock.acquire(blocking=True)
        try:
            # Wurde inzwischen eine neue Aufnahme gestartet? → verwerfen
            if self._transcription_seq != my_seq:
                if use_live_mode:
                    self._clear_live_session(my_seq)
                return
            if self._is_silence(audio):
                if use_live_mode:
                    self._clear_live_session(my_seq)
                return
            text = self._transcribe_audio(audio, retry_lowercase=True)
            if use_live_mode:
                history_text = self._finalize_live_session(my_seq, text)
                if history_text:
                    self._add_to_history(history_text)
            else:
                if text and not self._is_hallucination(text) and self._ki_korrektur:
                    self._set_ui(status="Korrigiere…")
                    text = self.corrector.correct(text)
                    self._clear_mlx_cache()
                if text and self._translate_to:
                    text = GoogleTranslator(
                        source=self.language or "auto",
                        target=self._translate_to,
                    ).translate(text) or text
                # Nochmals prüfen – fn könnte während der Transkription gedrückt worden sein
                if text and not self._is_hallucination(text) and self._transcription_seq == my_seq:
                    self._insert_with_workflows(text)
                    self._add_to_history(text)
        finally:
            self._spinner.hide()
            self._transcribe_lock.release()
            self._set_ui(status=self._ready_status())

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

    def _insert_plain_text(self, text: str) -> bool:
        try:
            from ApplicationServices import (
                AXUIElementCreateSystemWide,
                AXUIElementCopyAttributeValue,
                AXUIElementSetAttributeValue,
            )

            system = AXUIElementCreateSystemWide()
            err, focused = AXUIElementCopyAttributeValue(system, "AXFocusedUIElement", None)
            if err != 0 or focused is None:
                logging.debug(f"AX insert skipped: focused err={err}")
                return False

            err = AXUIElementSetAttributeValue(
                focused,
                "AXSelectedText",
                AppKit.NSString.stringWithString_(text),
            )
            if err == 0:
                logging.debug(f"AX insert ok len={len(text)}")
                return True
            logging.debug(f"AX insert failed err={err}")
        except Exception as e:
            logging.debug(f"AX insert exception: {e}")
        return False

    def _paste_plain_text(self, text: str, pb) -> bool:
        try:
            pb.clearContents()
            pb.setString_forType_(text, AppKit.NSPasteboardTypeString)
            time.sleep(0.02)
            # Cmd+V direkt via CGEvent – gleiche HID-Pipeline wie alle anderen Key-Events.
            # Funktioniert zuverlässig in iFrames und Browser-Editoren, wo osascript versagt.
            down = CGEventCreateKeyboardEvent(None, 9, True)   # V
            CGEventSetFlags(down, kCGEventFlagMaskCommand)
            CGEventPost(kCGHIDEventTap, down)
            up = CGEventCreateKeyboardEvent(None, 9, False)
            CGEventSetFlags(up, kCGEventFlagMaskCommand)
            CGEventPost(kCGHIDEventTap, up)
            return True
        except Exception as e:
            logging.exception(f"Paste fehlgeschlagen: {e}")
            return False

    def _insert_with_workflows(self, text: str) -> bool:
        # Workflows zuerst auf Original-Text (verhindert rstrip-Konflikt mit Kürzeln)
        workflows = load_workflows()
        segments  = split_by_triggers(text, workflows)
        shortcuts = load_shortcuts()

        pb      = AppKit.NSPasteboard.generalPasteboard()
        saved   = pb.stringForType_(AppKit.NSPasteboardTypeString)
        inserted_any = False

        # Smartes Leerzeichen + Großschreibung: vor dem ersten Segment prüfen
        # was direkt vor dem Cursor steht. Fallback für iFrames/Browser.
        char_before = self._get_char_before_cursor()
        if char_before:
            needs_leading_space  = char_before not in (" ", "\n", "\t", "\r")
            after_sentence_end   = char_before in (".", "!", "?")
        else:
            needs_leading_space  = self._last_insert_ends_with_word
            after_sentence_end   = self._last_insert_ends_with_sentence

        last_seg     = ""
        pending_after = ""
        pending_wrap  = None   # (pre_html, post_html) für html:...|...-Modus

        for i, (seg_text, workflow) in enumerate(segments):
            seg_text = apply_shortcuts(seg_text, shortcuts)
            # Smart-Space + Großschreibung: erstes Segment
            if seg_text and i == 0 and pending_wrap is None:
                if needs_leading_space:
                    seg_text = " " + seg_text
                if after_sentence_end and seg_text.lstrip() and seg_text.lstrip()[0].islower():
                    stripped = seg_text.lstrip()
                    seg_text = seg_text[: len(seg_text) - len(stripped)] + stripped[0].upper() + stripped[1:]
            # Folgesegmente: Leerzeichen einfügen wenn kein Zeilenumbruch dazwischen
            elif seg_text and i > 0 and last_seg and last_seg[-1] not in (" ", "\n", "\t", "\r"):
                prev_wf = segments[i - 1][1]
                prev_action = (prev_wf.get("action", "") if prev_wf else "").lower()
                if not any(k in prev_action for k in ("enter", "return")):
                    seg_text = " " + seg_text

            if pending_wrap is not None and seg_text:
                # Text in HTML-Wrapper einfügen – alles als ein einziger Paste
                pre, post = pending_wrap
                paste_html(pre + seg_text + post)
                inserted_any = True
                last_seg     = seg_text
                pending_wrap = None
            elif seg_text:
                if self._insert_plain_text(seg_text):
                    inserted_any = True
                elif self._paste_plain_text(seg_text, pb):
                    inserted_any = True
                else:
                    logging.warning("Segment konnte weder per AX noch per Paste eingefügt werden")
                    return False
                last_seg = seg_text

            if pending_after:
                time.sleep(0.08)
                execute_action(pending_after)
                time.sleep(0.08)
                pending_after = ""

            if workflow:
                action = workflow.get("action", "")
                after  = workflow.get("after", "")
                if action.strip().lower().startswith("html:") and "|" in action:
                    # Wrap-Modus: text wird in HTML eingebettet
                    html_tpl      = action.strip()[5:]
                    pre, _, post  = html_tpl.partition("|")
                    pending_wrap  = (pre, post)
                    pending_after = after
                else:
                    time.sleep(0.08)
                    execute_action(action)
                    time.sleep(0.08)
                    pending_after = after

        if pending_wrap is not None:
            # Trigger am Ende ohne Folgetext → leeres HTML einfügen
            pre, post = pending_wrap
            paste_html(pre + post)
            inserted_any = True
        if pending_after:
            time.sleep(0.08)
            execute_action(pending_after)

        if last_seg:
            self._last_insert_ends_with_word     = last_seg[-1] not in (" ", "\n", "\t", "\r")
            self._last_insert_ends_with_sentence = last_seg[-1] in (".", "!", "?")

        if saved:
            def _restore_clipboard(s=saved):
                time.sleep(1.0)
                pb.clearContents()
                pb.setString_forType_(s, AppKit.NSPasteboardTypeString)
            threading.Thread(target=_restore_clipboard, daemon=True).start()

        return inserted_any or not text.strip()

    def _insert_text(self, text: str):
        pb = AppKit.NSPasteboard.generalPasteboard()
        old_text = pb.stringForType_(AppKit.NSPasteboardTypeString)

        pb.clearContents()
        pb.setString_forType_(text, AppKit.NSPasteboardTypeString)

        time.sleep(0.02)
        down = CGEventCreateKeyboardEvent(None, 9, True)   # Cmd+V
        CGEventSetFlags(down, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, down)
        up = CGEventCreateKeyboardEvent(None, 9, False)
        CGEventSetFlags(up, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, up)

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
        # Englisch
        "thank you", "thanks", "thank you.", "thanks.",
        "thanks for watching", "thanks for watching.",
        "thank you for watching", "thank you for watching.",
        "you", "bye", "bye.", "goodbye", "goodbye.",
        "please", "please.",
        # Deutsch
        "vielen dank", "vielen dank.", "vielen dank!",
        "danke", "danke.", "danke schön", "danke schön.",
        "bitte", "bitte.", "tschüss", "tschüss.",
        "auf wiedersehen", "auf wiedersehen.",
        "ja", "ja.", "nein", "nein.", "ok", "ok.",
        # Untertitel-Halluzinationen
        "untertitel",
        "subtitles by",
        "© 2",
    }

    def _is_hallucination(self, text: str) -> bool:
        t = text.strip().lower()
        # Exakter Treffer
        if t in self._HALLUCINATIONS:
            return True
        # Beginnt mit bekannten Untertitel-Phrasen
        for h in self._HALLUCINATIONS:
            if t.startswith(h) and len(t) < len(h) + 10:
                return True
        return False

    _SILENCE_RMS_THRESHOLD = 0.01   # Werte darunter gelten als Stille

    def _is_silence(self, audio) -> bool:
        import numpy as np
        rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        return rms < self._SILENCE_RMS_THRESHOLD

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

    def _on_f14_hold_release(self):
        """F14 nach Halten losgelassen: Audio transkribieren → als Prompt → LLM → einfügen."""
        try:
            self._set_ui(status="Transkribiere Prompt…")
            audio = self.recorder.stop()

            if audio is None or len(audio) < int(AudioRecorder.SAMPLE_RATE * 0.3):
                self._set_ui(status="Zu kurz gesprochen")
                threading.Timer(1.5, lambda: self._set_ui(status=self._ready_status())).start()
                return

            custom_prompt = self.transcriber.transcribe(audio, language=self.language)
            logging.info(f"F14 Hold Prompt: '{custom_prompt}'")
            if not custom_prompt:
                self._set_ui(status="Kein Text erkannt")
                threading.Timer(1.5, lambda: self._set_ui(status=self._ready_status())).start()
                return

            # Ausgewählten Text holen
            time.sleep(0.1)
            subprocess.run([
                "osascript", "-e",
                'tell application "System Events" to keystroke "c" using command down',
            ])
            time.sleep(0.2)
            pb = AppKit.NSPasteboard.generalPasteboard()
            selected = (pb.stringForType_(AppKit.NSPasteboardTypeString) or "").strip()
            logging.info(f"F14 Hold Selected: '{selected[:80]}'")
            if not selected:
                self._set_ui(status="Kein Text ausgewählt")
                threading.Timer(2.0, lambda: self._set_ui(status=self._ready_status())).start()
                return

            if self.corrector._model is None:
                self._set_ui(status="Lade KI-Modell…")
                self.corrector.preload()

            self._set_ui(status="KI verarbeitet…")
            result = self.corrector.correct(selected, system_prompt=custom_prompt)
            logging.info(f"F14 Hold Ergebnis: '{result[:80]}'")

            if result and result != selected:
                pb.clearContents()
                pb.setString_forType_(result, AppKit.NSPasteboardTypeString)
                time.sleep(0.05)
                subprocess.run([
                    "osascript", "-e",
                    'tell application "System Events" to keystroke "v" using command down',
                ])
        except Exception as e:
            logging.exception(f"F14 Hold Fehler: {e}")
        finally:
            self._spinner.hide()
            self._set_ui(status=self._ready_status())

    def _on_f14_ai_edit(self):
        """Markierten Text per F14 an das KI-Modell schicken und ersetzen.
        Unabhängig vom KI-Korrektur-Toggle – aktiviert diesen NICHT."""
        logging.info("F14 AI-Edit gestartet")
        self._spinner.show()
        self._set_ui(status="KI verarbeitet…")
        try:
            # Kurz warten damit Event-Tap fertig ist, dann Cmd+C
            time.sleep(0.15)
            subprocess.run([
                "osascript", "-e",
                'tell application "System Events" to keystroke "c" using command down',
            ])
            time.sleep(0.25)  # Clipboard braucht kurz

            pb = AppKit.NSPasteboard.generalPasteboard()
            selected = (pb.stringForType_(AppKit.NSPasteboardTypeString) or "").strip()
            logging.info(f"F14: Clipboard='{selected[:80]}'")

            if not selected:
                logging.info("F14: Kein Text im Clipboard")
                def _notify():
                    self.title = " Kein Text ausgewählt"
                AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(_notify)
                threading.Timer(2.0, lambda: AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
                    lambda: setattr(self, "title", "")
                )).start()
                return

            # Modell laden falls noch nicht geschehen (bleibt geladen)
            if self.corrector._model is None:
                self._set_ui(status="Lade KI-Modell…")
                self.corrector.preload()

            self._set_ui(status="KI verarbeitet…")
            result = self.corrector.correct(selected, system_prompt=self._ki_auswahl_prompt)
            logging.info(f"F14: Ergebnis='{result[:80]}'")

            if result and result != selected:
                pb.clearContents()
                pb.setString_forType_(result, AppKit.NSPasteboardTypeString)
                time.sleep(0.05)
                subprocess.run([
                    "osascript", "-e",
                    'tell application "System Events" to keystroke "v" using command down',
                ])
        except Exception as e:
            logging.exception(f"F14 AI-Edit Fehler: {e}")
        finally:
            self._spinner.hide()
            self._set_ui(status=self._ready_status())

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
            try:
                with open(SETTINGS_FILE, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
            data.update({
                "language":     self.language,
                "translate_to": self._translate_to,
                "mic_device":   self._mic_device_name,
                "live_transcription": self._live_transcription,
                "ki_korrektur": self._ki_korrektur,
            })
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
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

    def _refresh_mic_menu(self):
        """Aktualisiert die Mikrofonliste (neue Geräte hinzufügen, verschwundene entfernen)."""
        reinit_done = False
        if not self._is_recording:
            try:
                sd._terminate()
                sd._initialize()
                reinit_done = True
            except Exception:
                pass
        current = {name: idx for idx, name in _list_input_devices()}

        # Index aktualisieren (kann sich nach Reconnect ändern)
        for name, idx in current.items():
            if name in self._mic_menu_items:
                old_idx, item = self._mic_menu_items[name]
                if old_idx != idx:
                    self._mic_menu_items[name] = (idx, item)
                    if self._mic_device_name == name:
                        self._mic_device_idx = idx

        # Neue Geräte hinzufügen
        for name, idx in current.items():
            if name not in self._mic_menu_items:
                item = rumps.MenuItem(name, callback=self._on_mic_select)
                self._mic_submenu[name] = item
                self._mic_menu_items[name] = (idx, item)
                if name == self._mic_device_name:
                    # Alle anderen Häkchen entfernen, dann dieses setzen
                    for _, (_, it) in self._mic_menu_items.items():
                        it._menuitem.setState_(0)
                    item._menuitem.setState_(1)
                    self._mic_device_idx = idx

        # Verschwundene Geräte entfernen
        for name in list(self._mic_menu_items.keys()):
            if name == "System (Standard)":
                continue
            if name not in current:
                del self._mic_submenu[name]   # entfernt aus NSMenu UND rumps-internem Dict
                del self._mic_menu_items[name]
                if self._mic_device_name == name:
                    self._mic_device_name = None
                    self._mic_device_idx  = None
                    self._mic_menu_items["System (Standard)"][1]._menuitem.setState_(1)

        # PortAudio-Reset hat den dauerhaften Stream im Recorder abgerissen →
        # sofort neu öffnen, damit die nächste Aufnahme ohne Klick funktioniert
        if reinit_done:
            try:
                self.recorder.set_device(self._mic_device_idx)
            except Exception:
                pass

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

    def _on_live_toggle(self, sender):
        self._live_transcription = not self._live_transcription
        self._live_item._menuitem.setState_(1 if self._live_transcription else 0)
        self._save_settings()
        self._set_ui(status=self._ready_status())

    def _toggle_ki_mode(self):
        """KI-Modus per Doppeltipp an/ausschalten."""
        self._ki_korrektur = not self._ki_korrektur
        enabled = self._ki_korrektur
        msg = "KI-Modus aktiviert" if enabled else "KI-Modus deaktiviert"

        def _update():
            self._ki_item._menuitem.setState_(1 if enabled else 0)
            self._status_item.title = msg
            self.title = f" {msg}"

        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(_update)

        self._save_settings()
        if enabled and self.corrector._model is None:
            threading.Thread(target=self._load_corrector_bg, daemon=True).start()

        # Meldung nach 2 Sekunden wieder ausblenden
        def _reset():
            self._status_item.title = self._ready_status()
            self.title = ""
        threading.Timer(2.0, lambda: AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(_reset)).start()

    def _on_ki_live_toggle(self, sender):
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(self._ki_live_win.show)

    def _on_ki_auswahl_toggle(self, sender):
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(self._ki_auswahl_win.show)

    def _on_ki_live_saved(self, enabled: bool, prompt: str):
        """Callback vom KI-Live-Fenster."""
        self._ki_korrektur = enabled
        self.corrector.system_prompt = prompt
        self._ki_item._menuitem.setState_(1 if enabled else 0)
        if enabled and self.corrector._model is None:
            threading.Thread(target=self._load_corrector_bg, daemon=True).start()

    def _on_ki_auswahl_saved(self, prompt: str):
        """Callback vom KI-Auswahl-Fenster."""
        self._ki_auswahl_prompt = prompt
        # Modell laden falls noch nicht geschehen (F14 könnte gleich genutzt werden)
        if self.corrector._model is None:
            threading.Thread(target=self._load_corrector_bg, daemon=True).start()

    def _load_corrector_bg(self):
        self._set_ui(status="Lade KI-Korrektor…")
        try:
            self.corrector.preload()
        except Exception as e:
            logging.exception(f"KI-Korrektor konnte nicht geladen werden: {e}")
        self._set_ui(status=self._ready_status())


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
