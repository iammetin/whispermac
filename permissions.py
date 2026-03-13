"""
Berechtigungsprüfung und Onboarding-Fenster für WhisperMac.
Prüft Mikrofon + Bedienungshilfen und führt den Nutzer durch den Prozess.
"""
import subprocess
import threading
import time

import AppKit
import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSButton,
    NSColor,
    NSFont,
    NSMakeRect,
    NSTextField,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)

try:
    import AVFoundation
    _AV_OK = True
except ImportError:
    _AV_OK = False

try:
    from ApplicationServices import AXIsProcessTrusted
    _AX_OK = True
except ImportError:
    _AX_OK = False

MIC_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
ACC_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"


# ── Berechtigungen prüfen ─────────────────────────────────────────────────────

def mic_granted() -> bool:
    if not _AV_OK:
        return True
    status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
        AVFoundation.AVMediaTypeAudio
    )
    return int(status) == 3  # AVAuthorizationStatusAuthorized


def accessibility_granted() -> bool:
    if not _AX_OK:
        return True
    return bool(AXIsProcessTrusted())


def all_granted() -> bool:
    return mic_granted() and accessibility_granted()


def request_microphone(callback):
    """Fordert Mikrofon-Berechtigung an (nur beim ersten Mal möglich)."""
    if not _AV_OK:
        callback(True)
        return
    status = int(AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
        AVFoundation.AVMediaTypeAudio
    ))
    if status == 0:  # NotDetermined → direkt anfragen
        AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVFoundation.AVMediaTypeAudio, callback
        )
    else:
        # Bereits abgelehnt → Einstellungen öffnen
        subprocess.run(["open", MIC_URL])
        callback(False)


# ── Button-Handler (NSObject-Subklasse für target-action) ────────────────────

class _BtnHandler(AppKit.NSObject):
    _cb = None

    def initWithCallback_(self, cb):
        self = objc.super(_BtnHandler, self).init()
        if self is None:
            return None
        self._cb = cb
        return self

    def fire_(self, sender):
        if self._cb:
            self._cb()


# ── Berechtigungs-Fenster ─────────────────────────────────────────────────────

class PermissionsWindow:
    W, H = 420, 300

    def __init__(self, on_all_granted):
        self._on_done = on_all_granted
        self._win = None
        self._rows = {}       # key → (status_label, button, handler)
        self._polling = False
        self._handlers = []   # starke Referenzen auf Handler

    def show(self):
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(self._build)

    def _build(self):
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, self.W, self.H),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered,
            False,
        )
        win.setTitle_("WhisperMac – Berechtigungen")
        win.center()
        win.setLevel_(AppKit.NSFloatingWindowLevel)
        win.setReleasedWhenClosed_(False)
        win.setMinSize_(AppKit.NSMakeSize(self.W, self.H))
        win.setMaxSize_(AppKit.NSMakeSize(self.W, self.H))

        cv = win.contentView()

        # ── Titel ────────────────────────────────────────────────────────────
        t = NSTextField.labelWithString_("Berechtigungen erforderlich")
        t.setFont_(NSFont.boldSystemFontOfSize_(16))
        t.setFrame_(NSMakeRect(24, self.H - 52, self.W - 48, 24))
        cv.addSubview_(t)

        sub = NSTextField.labelWithString_(
            "WhisperMac benötigt die folgenden Berechtigungen um zu funktionieren."
        )
        sub.setFont_(NSFont.systemFontOfSize_(12))
        sub.setTextColor_(NSColor.secondaryLabelColor())
        sub.setFrame_(NSMakeRect(24, self.H - 76, self.W - 48, 18))
        cv.addSubview_(sub)

        # ── Trennlinie ───────────────────────────────────────────────────────
        sep = AppKit.NSBox.alloc().initWithFrame_(NSMakeRect(24, self.H - 92, self.W - 48, 1))
        sep.setBoxType_(AppKit.NSBoxSeparator)
        cv.addSubview_(sep)

        # ── Berechtigungs-Reihen ─────────────────────────────────────────────
        self._add_row(cv, key="mic", y=self.H - 165,
                      icon="🎙", title="Mikrofon",
                      desc="Zum Aufnehmen deiner Sprache",
                      action=self._action_mic)

        self._add_row(cv, key="acc", y=self.H - 235,
                      icon="⌨", title="Bedienungshilfen",
                      desc="Zum Erkennen der fn-Taste & Einfügen von Text",
                      action=self._action_acc)

        self._win = win
        win.makeKeyAndOrderFront_(None)
        self._update()
        self._start_polling()

    def _add_row(self, parent, key, y, icon, title, desc, action):
        # Titel
        lbl = NSTextField.labelWithString_(f"{icon}  {title}")
        lbl.setFont_(NSFont.systemFontOfSize_(13))
        lbl.setFrame_(NSMakeRect(24, y + 18, 220, 20))
        parent.addSubview_(lbl)

        # Beschreibung
        d = NSTextField.labelWithString_(desc)
        d.setFont_(NSFont.systemFontOfSize_(11))
        d.setTextColor_(NSColor.secondaryLabelColor())
        d.setFrame_(NSMakeRect(24, y, 220, 17))
        parent.addSubview_(d)

        # Status-Label
        status = NSTextField.labelWithString_("–")
        status.setFont_(NSFont.systemFontOfSize_(12))
        status.setFrame_(NSMakeRect(self.W - 200, y + 12, 90, 20))
        status.setAlignment_(AppKit.NSTextAlignmentRight)
        parent.addSubview_(status)

        # Button
        btn = NSButton.alloc().initWithFrame_(NSMakeRect(self.W - 110, y + 8, 90, 26))
        btn.setTitle_("Erteilen →")
        btn.setBezelStyle_(AppKit.NSBezelStyleRounded)

        handler = _BtnHandler.alloc().initWithCallback_(action)
        self._handlers.append(handler)
        btn.setTarget_(handler)
        btn.setAction_(objc.selector(handler.fire_, selector=b"fire:", signature=b"v@:@"))
        parent.addSubview_(btn)

        self._rows[key] = (status, btn)

    def _update(self):
        """Aktualisiert Status-Labels und Buttons."""
        states = {"mic": mic_granted(), "acc": accessibility_granted()}

        for key, granted in states.items():
            if key not in self._rows:
                continue
            status_lbl, btn = self._rows[key]
            if granted:
                status_lbl.setStringValue_("✓ Erteilt")
                status_lbl.setTextColor_(NSColor.systemGreenColor())
                btn.setHidden_(True)
            else:
                status_lbl.setStringValue_("✗ Fehlt")
                status_lbl.setTextColor_(NSColor.systemRedColor())
                btn.setHidden_(False)

        if all(states.values()):
            self._finish()

    def _finish(self):
        self._polling = False
        if self._win:
            self._win.orderOut_(None)
            self._win = None
        self._on_done()

    def _start_polling(self):
        self._polling = True

        def poll():
            while self._polling:
                time.sleep(1)
                AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(self._update)

        threading.Thread(target=poll, daemon=True).start()

    # ── Button-Aktionen ───────────────────────────────────────────────────────

    def _action_mic(self):
        def _cb(granted):
            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(self._update)

        request_microphone(_cb)

    def _action_acc(self):
        subprocess.run(["open", ACC_URL])


# ── Öffentliche Funktion ──────────────────────────────────────────────────────

def ensure_permissions(on_granted):
    """
    Prüft Berechtigungen. Wenn alle vorhanden → on_granted() sofort.
    Sonst → Fenster zeigen, on_granted() wenn alles erteilt.
    """
    if all_granted():
        on_granted()
        return

    win = PermissionsWindow(on_granted)
    win.show()
