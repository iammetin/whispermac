"""
WhisperMac – KI-Korrektur-Fenster
Aktivieren / Deaktivieren + Prompt bearbeiten
"""
import json
import os

import AppKit
import objc

SETTINGS_FILE = os.path.expanduser("~/.whispermac_settings.json")

DEFAULT_PROMPT = (
    "Du bist ein Grammatik-Korrekturdienst für Spracherkennung. "
    "Korrigiere den deutschen Text grammatikalisch (Groß-/Kleinschreibung, "
    "Zeichensetzung, Beugung). Behalte den Originalwortlaut so weit wie möglich. "
    "Antworte ausschließlich mit dem korrigierten Text – keine Erklärungen, "
    "keine Anmerkungen."
)


def load_ki_settings():
    """Gibt (enabled: bool, prompt: str) zurück."""
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("ki_korrektur", False)), data.get("ki_prompt", DEFAULT_PROMPT)
    except Exception:
        return False, DEFAULT_PROMPT


def save_ki_settings(enabled: bool, prompt: str):
    """Schreibt nur ki_korrektur + ki_prompt, lässt andere Keys unverändert."""
    try:
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        data["ki_korrektur"] = enabled
        data["ki_prompt"]    = prompt
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class KIWindowController(AppKit.NSObject):

    def init(self):
        self = objc.super(KIWindowController, self).init()
        if self is None:
            return None
        self._win       = None
        self._checkbox  = None
        self._text_view = None
        self._on_save   = None   # callback(enabled: bool, prompt: str)
        return self

    def is_open(self):
        return self._win is not None and self._win.isVisible()

    def close(self):
        if self._win is not None:
            self._win.orderOut_(None)

    def show(self):
        if self._win is None:
            self._build()
        enabled, prompt = load_ki_settings()
        self._checkbox.setState_(1 if enabled else 0)
        self._text_view.setString_(prompt)
        self._win.makeKeyAndOrderFront_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    # ── Fenster aufbauen ──────────────────────────────────────────────────

    def _build(self):
        W, H     = 520, 380
        TOP_H    = 44
        LABEL_H  = 22
        BOTTOM_H = 48

        win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, W, H),
            AppKit.NSWindowStyleMaskTitled |
            AppKit.NSWindowStyleMaskClosable |
            AppKit.NSWindowStyleMaskResizable |
            AppKit.NSWindowStyleMaskMiniaturizable,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        win.setTitle_("KI-Korrektur")
        win.setReleasedWhenClosed_(False)
        win.setMinSize_(AppKit.NSMakeSize(380, 280))
        win.center()
        cv = win.contentView()

        # ── Checkbox (oben) ───────────────────────────────────────────────
        checkbox = AppKit.NSButton.alloc().initWithFrame_(
            AppKit.NSMakeRect(16, H - TOP_H + 10, W - 32, 24)
        )
        checkbox.setButtonType_(AppKit.NSSwitchButton)
        checkbox.setTitle_("KI-Korrektur aktivieren")
        checkbox.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin
        )
        cv.addSubview_(checkbox)
        self._checkbox = checkbox

        # Trennlinie unter Checkbox
        sep_top = AppKit.NSBox.alloc().initWithFrame_(AppKit.NSMakeRect(0, H - TOP_H, W, 1))
        sep_top.setBoxType_(AppKit.NSBoxSeparator)
        sep_top.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin)
        cv.addSubview_(sep_top)

        # ── Label ─────────────────────────────────────────────────────────
        lbl_y = H - TOP_H - LABEL_H - 6
        label = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(16, lbl_y, W - 32, LABEL_H)
        )
        label.setStringValue_("Prompt (wird vor jeder Transkription an die KI gesendet):")
        label.setFont_(AppKit.NSFont.systemFontOfSize_(12.0))
        label.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin)
        cv.addSubview_(label)

        # ── Textfeld (Prompt) ──────────────────────────────────────────────
        text_top = lbl_y - 4
        text_scroll = AppKit.NSScrollView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, BOTTOM_H, W, text_top - BOTTOM_H)
        )
        text_scroll.setHasVerticalScroller_(True)
        text_scroll.setAutohidesScrollers_(True)
        text_scroll.setBorderType_(AppKit.NSBezelBorder)
        text_scroll.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )

        text_view = AppKit.NSTextView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, W, text_top - BOTTOM_H)
        )
        text_view.setFont_(AppKit.NSFont.systemFontOfSize_(13.0))
        text_view.setAutomaticQuoteSubstitutionEnabled_(False)
        text_view.setAutomaticDashSubstitutionEnabled_(False)
        text_view.setAutomaticSpellingCorrectionEnabled_(False)
        text_view.setRichText_(False)
        text_view.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        text_view.textContainer().setWidthTracksTextView_(True)
        text_view.textContainer().setLineFragmentPadding_(6)
        text_scroll.setDocumentView_(text_view)
        cv.addSubview_(text_scroll)
        self._text_view = text_view

        # Trennlinie über Toolbar
        sep_bot = AppKit.NSBox.alloc().initWithFrame_(AppKit.NSMakeRect(0, BOTTOM_H - 1, W, 1))
        sep_bot.setBoxType_(AppKit.NSBoxSeparator)
        sep_bot.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        cv.addSubview_(sep_bot)

        # ── Speichern-Button ───────────────────────────────────────────────
        btn = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(W - 112, 10, 104, 28))
        btn.setTitle_("Speichern")
        btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
        btn.setKeyEquivalent_("\r")
        btn.setTarget_(self)
        btn.setAction_("onSave:")
        btn.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMaxYMargin)
        cv.addSubview_(btn)

        self._win = win

    # ── Aktionen ──────────────────────────────────────────────────────────

    def onSave_(self, sender):
        enabled = self._checkbox.state() == 1
        prompt  = str(self._text_view.string()).strip()
        if not prompt:
            prompt = DEFAULT_PROMPT
            self._text_view.setString_(prompt)
        save_ki_settings(enabled, prompt)
        if self._on_save:
            self._on_save(enabled, prompt)
        self._win.orderOut_(None)
