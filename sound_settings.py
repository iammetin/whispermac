"""Auswählbare macOS-Systemtöne und das native Toneinstellungsfenster."""
import logging
from pathlib import Path

import AppKit
import objc


EVENTS = ("recording_start", "model_ready")
DEFAULTS = {
    "recording_start": {"enabled": True, "sound": "Funk"},
    "model_ready": {"enabled": False, "sound": "Glass"},
}


def system_sound_names():
    return sorted({
        path.stem for path in Path("/System/Library/Sounds").glob("*")
        if path.is_file() and path.suffix.lower() in (".aiff", ".aif", ".wav", ".caf")
    }, key=str.casefold)


class SoundFeedback:
    def __init__(self, settings=None):
        self.names = system_sound_names()
        self._settings = {}
        self._playing = None
        raw = settings if isinstance(settings, dict) else {}
        for event, default in DEFAULTS.items():
            value = raw.get(event, {})
            if not isinstance(value, dict):
                value = {}
            fallback = default["sound"] if default["sound"] in self.names else next(iter(self.names), None)
            name = value.get("sound", fallback)
            if not isinstance(name, str) or name not in self.names:
                name = fallback
            enabled = value.get("enabled", default["enabled"])
            if not isinstance(enabled, bool):
                enabled = default["enabled"]
            self._settings[event] = {"enabled": enabled and name is not None, "sound": name}

    def snapshot(self):
        return {event: dict(value) for event, value in self._settings.items()}

    def update_event(self, event, *, enabled=None, sound=None):
        value = dict(self._settings[event])
        if enabled is not None:
            value["enabled"] = bool(enabled) and bool(self.names)
        if sound is not None:
            if sound not in self.names:
                raise ValueError("Unbekannter Systemton")
            value["sound"] = sound
        self._settings[event] = value

    def play(self, event):
        value = self._settings[event]
        if value["enabled"]:
            self.preview(value["sound"])

    def preview(self, name):
        if name not in self.names:
            return
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(
            lambda: self._play_sound(name)
        )

    def _play_sound(self, name):
        try:
            sound = AppKit.NSSound.soundNamed_(name)
            if sound is None:
                logging.warning("Systemton nicht verfügbar: %s", name)
                return
            if self._playing is not None:
                self._playing.stop()
            self._playing = sound
            if not sound.play():
                logging.warning("Systemton konnte nicht abgespielt werden: %s", name)
        except Exception:
            logging.exception("Systemton konnte nicht abgespielt werden: %s", name)


def _label(parent, text, x, y, width, *, size=12, bold=False, secondary=False):
    label = AppKit.NSTextField.labelWithString_(text)
    font = AppKit.NSFont.boldSystemFontOfSize_ if bold else AppKit.NSFont.systemFontOfSize_
    label.setFont_(font(size))
    label.setFrame_(AppKit.NSMakeRect(x, y, width, 24))
    if secondary:
        label.setTextColor_(AppKit.NSColor.secondaryLabelColor())
    parent.addSubview_(label)


class SoundSettingsWindowController(AppKit.NSObject):
    def init(self):
        self = objc.super(SoundSettingsWindowController, self).init()
        if self is None:
            return None
        self._win = None
        self._feedback = None
        self._on_change = None
        self._controls = {}
        return self

    def is_open(self):
        return self._win is not None and self._win.isVisible()

    def close(self):
        if self._win is not None:
            self._win.orderOut_(None)

    def show(self):
        if self._win is None:
            self._build()
        self._refresh()
        if not self._win.isVisible():
            self._win.center()
        self._win.makeKeyAndOrderFront_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def _build(self):
        width, height = 500, 380
        win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, width, height),
            AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable,
            AppKit.NSBackingStoreBuffered, False,
        )
        win.setTitle_("WhisperMac – Toneinstellungen")
        win.setReleasedWhenClosed_(False)
        cv = win.contentView()
        _label(cv, "Toneinstellungen", 24, height - 52, 452, size=18, bold=True)
        _label(cv, "Wähle für beide Ereignisse einen Mac-Systemton.",
               24, height - 80, 452, secondary=True)

        sections = (
            ("Beim Aufnahmestart", "Wenn du die Aufnahmetaste drückst.", height - 122),
            ("Wenn WhisperMac bereit ist", "Nach dem Laden des Modells und der Aufnahmefunktion.", height - 242),
        )
        for index, (title, description, y) in enumerate(sections):
            _label(cv, title, 24, y, 452, size=14, bold=True)
            enabled = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(24, y - 36, 150, 26))
            enabled.setButtonType_(AppKit.NSSwitchButton)
            enabled.setTitle_("Ton abspielen")
            enabled.setTag_(index)
            enabled.setTarget_(self)
            enabled.setAction_("enabledChanged:")
            cv.addSubview_(enabled)

            popup = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
                AppKit.NSMakeRect(190, y - 36, 180, 26), False
            )
            popup.addItemsWithTitles_(self._feedback.names or ["Keine Systemtöne"])
            popup.setTag_(index)
            popup.setTarget_(self)
            popup.setAction_("soundChanged:")
            cv.addSubview_(popup)

            preview = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(380, y - 36, 96, 26))
            preview.setTitle_("Anhören")
            preview.setBezelStyle_(AppKit.NSBezelStyleRounded)
            preview.setTag_(index)
            preview.setTarget_(self)
            preview.setAction_("previewSound:")
            cv.addSubview_(preview)
            for control in (enabled, popup, preview):
                control.setEnabled_(bool(self._feedback.names))
            self._controls[EVENTS[index]] = (enabled, popup)
            _label(cv, description, 24, y - 66, 452, size=11, secondary=True)

        sep = AppKit.NSBox.alloc().initWithFrame_(AppKit.NSMakeRect(24, 174, 452, 1))
        sep.setBoxType_(AppKit.NSBoxSeparator)
        cv.addSubview_(sep)
        _label(cv, "Auswahl wird vorgespielt · Änderungen werden automatisch gespeichert.",
               24, 22, 452, size=11, secondary=True)
        self._win = win

    def _refresh(self):
        for event, value in self._feedback.snapshot().items():
            enabled, popup = self._controls[event]
            enabled.setState_(1 if value["enabled"] else 0)
            if value["sound"] is not None:
                popup.selectItemWithTitle_(value["sound"])

    def enabledChanged_(self, sender):
        event = EVENTS[int(sender.tag())]
        self._feedback.update_event(event, enabled=bool(sender.state()))
        if self._on_change:
            self._on_change()

    def soundChanged_(self, sender):
        event = EVENTS[int(sender.tag())]
        name = str(sender.titleOfSelectedItem())
        self._feedback.update_event(event, sound=name)
        if self._on_change:
            self._on_change()
        self._feedback.preview(name)

    def previewSound_(self, sender):
        event = EVENTS[int(sender.tag())]
        self._feedback.preview(self._feedback.snapshot()[event]["sound"])
