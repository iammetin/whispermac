"""
WhisperMac – KI-Prompt-Manager
Verwaltet Prompts für KI-Live-Korrektur (fn fn) und KI-Auswahl-Korrektur (F14).
"""
import json
import os
import uuid

import AppKit
import objc

SETTINGS_FILE = os.path.expanduser("~/.whispermac_settings.json")

DEFAULT_LIVE_PROMPT = (
    "Du bist ein Grammatik-Korrekturdienst für Spracherkennung. "
    "Korrigiere den deutschen Text grammatikalisch (Groß-/Kleinschreibung, "
    "Zeichensetzung, Beugung). Entferne reine Füllwörter wie 'äh', 'ähm' oder "
    "'hm' sowie offensichtliche Selbstkorrekturen, wenn der Satz dadurch "
    "natürlicher wird. Behalte den Originalwortlaut sonst so weit wie möglich. "
    "Antworte ausschließlich mit dem korrigierten Text – keine Erklärungen, "
    "keine Anmerkungen."
)

LEGACY_LIVE_PROMPT = (
    "Du bist ein Grammatik-Korrekturdienst für Spracherkennung. "
    "Korrigiere den deutschen Text grammatikalisch (Groß-/Kleinschreibung, "
    "Zeichensetzung, Beugung). Behalte den Originalwortlaut so weit wie möglich. "
    "Antworte ausschließlich mit dem korrigierten Text – keine Erklärungen, "
    "keine Anmerkungen."
)

DEFAULT_AUSWAHL_PROMPT = (
    "Bearbeite den folgenden Text: Korrigiere Grammatik, Rechtschreibung und "
    "Zeichensetzung. Antworte ausschließlich mit dem bearbeiteten Text – "
    "keine Erklärungen, keine Anmerkungen."
)


def _load_raw() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_raw(patch: dict):
    try:
        data = _load_raw()
        data.update(patch)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _ensure_prompts(data: dict, key: str, default: str) -> list:
    """Gibt die Prompt-Liste zurück; legt einen Default an falls leer."""
    prompts = data.get(key, [])
    if not prompts:
        # Rückwärtskompatibilität: alter ki_prompt-Key als Fallback
        if key == "ki_live_prompts" and data.get("ki_prompt"):
            fallback = data["ki_prompt"]
        else:
            fallback = default
        prompts = [{"id": "default", "name": "Standard", "prompt": fallback, "active": True}]
    elif key == "ki_live_prompts":
        for prompt in prompts:
            if prompt.get("id") == "default" and prompt.get("prompt") == LEGACY_LIVE_PROMPT:
                prompt["prompt"] = default
    # Genau einer muss aktiv sein
    if not any(p.get("active") for p in prompts):
        prompts[0]["active"] = True
    return prompts


def load_ki_settings():
    """Gibt (ki_live_enabled, active_live_prompt, active_auswahl_prompt) zurück."""
    data = _load_raw()
    enabled = bool(data.get("ki_korrektur", False))
    live_p    = _ensure_prompts(data, "ki_live_prompts",    DEFAULT_LIVE_PROMPT)
    auswahl_p = _ensure_prompts(data, "ki_auswahl_prompts", DEFAULT_AUSWAHL_PROMPT)
    active_live    = next((p["prompt"] for p in live_p    if p.get("active")), live_p[0]["prompt"])
    active_auswahl = next((p["prompt"] for p in auswahl_p if p.get("active")), auswahl_p[0]["prompt"])
    return enabled, active_live, active_auswahl


# ─────────────────────────────────────────────────────────────────────────────

class PromptManagerWindowController(AppKit.NSObject):
    """
    Fenster zur Verwaltung von KI-Prompts.

    mode = "live"    → KI-Live-Korrektur (mit Aktivieren-Checkbox)
                        Callback: on_save(enabled: bool, prompt: str)
    mode = "auswahl" → KI-Auswahl-Korrektur (nur Prompts, kein Toggle)
                        Callback: on_save(prompt: str)
    """

    def initWithMode_(self, mode: str):
        self = objc.super(PromptManagerWindowController, self).init()
        if self is None:
            return None
        self._mode     = mode      # "live" | "auswahl"
        self._on_save  = None      # wird von app.py gesetzt
        self._new_mode = False     # True: gerade neuen Prompt anlegen
        self._cur_id   = None      # ID des aktuell angezeigten Prompts
        self._win      = None
        return self

    # ── Settings ─────────────────────────────────────────────────────────────

    def _settings_key(self) -> str:
        return "ki_live_prompts" if self._mode == "live" else "ki_auswahl_prompts"

    def _get_prompts(self) -> list:
        data    = _load_raw()
        default = DEFAULT_LIVE_PROMPT if self._mode == "live" else DEFAULT_AUSWAHL_PROMPT
        return _ensure_prompts(data, self._settings_key(), default)

    def _write_prompts(self, prompts: list):
        _save_raw({self._settings_key(): prompts})

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def is_open(self) -> bool:
        return self._win is not None and self._win.isVisible()

    def close(self):
        if self._win is not None:
            self._win.orderOut_(None)

    def show(self):
        if self._win is None:
            self._build()
        self._refresh_popup(select_active=True)
        if not self._win.isVisible():
            self._win.center()
        self._win.makeKeyAndOrderFront_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    # ── Fenster aufbauen ──────────────────────────────────────────────────────

    def _build(self):
        title    = "KI-Live-Korrektur" if self._mode == "live" else "KI-Auswahl-Korrektur"
        W        = 520
        H        = 500 if self._mode == "live" else 460
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
        win.setTitle_(title)
        win.setReleasedWhenClosed_(False)
        win.setMinSize_(AppKit.NSMakeSize(400, 360))
        win.center()
        cv = win.contentView()

        y = H - 10  # Startposition, Steuerung von oben nach unten

        # ── Aktivieren-Checkbox (nur live) ────────────────────────────────────
        if self._mode == "live":
            data    = _load_raw()
            enabled = bool(data.get("ki_korrektur", False))
            self._checkbox = AppKit.NSButton.alloc().initWithFrame_(
                AppKit.NSMakeRect(16, y - 28, W - 32, 24)
            )
            self._checkbox.setButtonType_(AppKit.NSSwitchButton)
            self._checkbox.setTitle_("KI-Live-Korrektur aktivieren")
            self._checkbox.setState_(1 if enabled else 0)
            self._checkbox.setAutoresizingMask_(
                AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin
            )
            cv.addSubview_(self._checkbox)
            y -= 40
            sep = AppKit.NSBox.alloc().initWithFrame_(AppKit.NSMakeRect(0, y, W, 1))
            sep.setBoxType_(AppKit.NSBoxSeparator)
            sep.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin)
            cv.addSubview_(sep)
            y -= 10

        # ── Label: Gespeicherte Prompts ───────────────────────────────────────
        lbl = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(16, y - 18, 200, 18))
        lbl.setStringValue_("Gespeicherte Prompts:")
        lbl.setFont_(AppKit.NSFont.systemFontOfSize_(12.0))
        lbl.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        lbl.setAutoresizingMask_(AppKit.NSViewMinYMargin)
        cv.addSubview_(lbl)
        y -= 26

        # ── Popup + Aktions-Buttons ───────────────────────────────────────────
        self._popup = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
            AppKit.NSMakeRect(16, y - 26, 210, 26), False
        )
        self._popup.setTarget_(self)
        self._popup.setAction_("onPopupChanged:")
        self._popup.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin)
        cv.addSubview_(self._popup)

        btn_activate = AppKit.NSButton.alloc().initWithFrame_(
            AppKit.NSMakeRect(234, y - 26, 88, 26)
        )
        btn_activate.setTitle_("Aktivieren")
        btn_activate.setBezelStyle_(AppKit.NSBezelStyleRounded)
        btn_activate.setTarget_(self)
        btn_activate.setAction_("onActivate:")
        btn_activate.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMinYMargin)
        cv.addSubview_(btn_activate)

        btn_new = AppKit.NSButton.alloc().initWithFrame_(
            AppKit.NSMakeRect(330, y - 26, 80, 26)
        )
        btn_new.setTitle_("Neu")
        btn_new.setBezelStyle_(AppKit.NSBezelStyleRounded)
        btn_new.setTarget_(self)
        btn_new.setAction_("onNew:")
        btn_new.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMinYMargin)
        cv.addSubview_(btn_new)

        btn_del = AppKit.NSButton.alloc().initWithFrame_(
            AppKit.NSMakeRect(418, y - 26, 86, 26)
        )
        btn_del.setTitle_("Löschen")
        btn_del.setBezelStyle_(AppKit.NSBezelStyleRounded)
        btn_del.setTarget_(self)
        btn_del.setAction_("onDelete:")
        btn_del.setAutoresizingMask_(AppKit.NSViewMinXMargin | AppKit.NSViewMinYMargin)
        cv.addSubview_(btn_del)
        y -= 40

        # Trennlinie
        sep2 = AppKit.NSBox.alloc().initWithFrame_(AppKit.NSMakeRect(0, y, W, 1))
        sep2.setBoxType_(AppKit.NSBoxSeparator)
        sep2.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin)
        cv.addSubview_(sep2)
        y -= 14

        # ── Name-Feld ─────────────────────────────────────────────────────────
        lbl_name = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(16, y - 20, 46, 18)
        )
        lbl_name.setStringValue_("Name:")
        lbl_name.setBezeled_(False)
        lbl_name.setDrawsBackground_(False)
        lbl_name.setEditable_(False)
        lbl_name.setSelectable_(False)
        lbl_name.setAutoresizingMask_(AppKit.NSViewMinYMargin)
        cv.addSubview_(lbl_name)

        self._name_field = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(68, y - 22, W - 84, 22)
        )
        self._name_field.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin
        )
        cv.addSubview_(self._name_field)
        y -= 36

        # ── Prompt-Label ──────────────────────────────────────────────────────
        lbl_prompt = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(16, y - 18, W - 32, 18)
        )
        lbl_prompt.setStringValue_("Prompt (wird an die KI gesendet):")
        lbl_prompt.setFont_(AppKit.NSFont.systemFontOfSize_(12.0))
        lbl_prompt.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        lbl_prompt.setBezeled_(False)
        lbl_prompt.setDrawsBackground_(False)
        lbl_prompt.setEditable_(False)
        lbl_prompt.setSelectable_(False)
        lbl_prompt.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin)
        cv.addSubview_(lbl_prompt)
        y -= 24

        # ── Textfeld ──────────────────────────────────────────────────────────
        text_scroll = AppKit.NSScrollView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, BOTTOM_H, W, y - BOTTOM_H)
        )
        text_scroll.setHasVerticalScroller_(True)
        text_scroll.setAutohidesScrollers_(True)
        text_scroll.setBorderType_(AppKit.NSBezelBorder)
        text_scroll.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )

        self._text_view = AppKit.NSTextView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, W, y - BOTTOM_H)
        )
        self._text_view.setFont_(AppKit.NSFont.systemFontOfSize_(13.0))
        self._text_view.setAutomaticQuoteSubstitutionEnabled_(False)
        self._text_view.setAutomaticDashSubstitutionEnabled_(False)
        self._text_view.setAutomaticSpellingCorrectionEnabled_(False)
        self._text_view.setRichText_(False)
        self._text_view.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )
        self._text_view.textContainer().setWidthTracksTextView_(True)
        self._text_view.textContainer().setLineFragmentPadding_(6)
        text_scroll.setDocumentView_(self._text_view)
        cv.addSubview_(text_scroll)

        # Trennlinie über Buttons
        sep3 = AppKit.NSBox.alloc().initWithFrame_(AppKit.NSMakeRect(0, BOTTOM_H - 1, W, 1))
        sep3.setBoxType_(AppKit.NSBoxSeparator)
        sep3.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        cv.addSubview_(sep3)

        # ── Speichern + Schließen ─────────────────────────────────────────────
        btn_close = AppKit.NSButton.alloc().initWithFrame_(
            AppKit.NSMakeRect(W - 112, 10, 104, 28)
        )
        btn_close.setTitle_("Schließen")
        btn_close.setBezelStyle_(AppKit.NSBezelStyleRounded)
        btn_close.setTarget_(self)
        btn_close.setAction_("onClose:")
        btn_close.setAutoresizingMask_(AppKit.NSViewMinXMargin)
        cv.addSubview_(btn_close)

        btn_save = AppKit.NSButton.alloc().initWithFrame_(
            AppKit.NSMakeRect(W - 224, 10, 104, 28)
        )
        btn_save.setTitle_("Speichern")
        btn_save.setBezelStyle_(AppKit.NSBezelStyleRounded)
        btn_save.setKeyEquivalent_("\r")
        btn_save.setTarget_(self)
        btn_save.setAction_("onSave:")
        btn_save.setAutoresizingMask_(AppKit.NSViewMinXMargin)
        cv.addSubview_(btn_save)

        self._win = win

    # ── Popup-Verwaltung ──────────────────────────────────────────────────────

    def _refresh_popup(self, select_active: bool = False):
        prompts     = self._get_prompts()
        cur_idx     = self._popup.indexOfSelectedItem() if not select_active else -1
        active_idx  = 0

        self._popup.removeAllItems()
        for i, p in enumerate(prompts):
            marker = "● " if p.get("active") else "   "
            self._popup.addItemWithTitle_(f"{marker}{p['name']}")
            if p.get("active"):
                active_idx = i

        select_idx = active_idx if select_active else max(0, min(cur_idx, len(prompts) - 1))
        self._popup.selectItemAtIndex_(select_idx)
        if 0 <= select_idx < len(prompts):
            self._load_into_fields(prompts[select_idx])

    def _load_into_fields(self, p: dict):
        self._cur_id   = p.get("id")
        self._new_mode = False
        self._name_field.setStringValue_(p.get("name", ""))
        self._text_view.setString_(p.get("prompt", ""))

    # ── Button-Aktionen ───────────────────────────────────────────────────────

    def onPopupChanged_(self, sender):
        idx     = self._popup.indexOfSelectedItem()
        prompts = self._get_prompts()
        if 0 <= idx < len(prompts):
            self._load_into_fields(prompts[idx])

    def onActivate_(self, sender):
        """Aktuell im Popup ausgewählten Prompt als aktiv markieren."""
        idx     = self._popup.indexOfSelectedItem()
        prompts = self._get_prompts()
        if not (0 <= idx < len(prompts)):
            return
        for i, p in enumerate(prompts):
            p["active"] = (i == idx)
        self._write_prompts(prompts)
        self._refresh_popup()
        self._fire_callback(prompts[idx]["prompt"])

    def onNew_(self, sender):
        """Felder für neuen Prompt leeren."""
        self._new_mode = True
        self._cur_id   = str(uuid.uuid4())[:8]
        self._name_field.setStringValue_("Neuer Prompt")
        self._text_view.setString_("")
        self._name_field.selectText_(None)

    def onSave_(self, sender):
        """Speichert den aktuell angezeigten Prompt (neu oder aktualisiert)."""
        name   = str(self._name_field.stringValue()).strip() or "Unbenannt"
        prompt = str(self._text_view.string()).strip()
        if not prompt:
            return

        prompts = self._get_prompts()
        if self._new_mode:
            prompts.append({
                "id": self._cur_id, "name": name,
                "prompt": prompt, "active": False,
            })
            self._new_mode = False
        else:
            for p in prompts:
                if p.get("id") == self._cur_id:
                    p["name"]   = name
                    p["prompt"] = prompt
                    break

        if self._mode == "live":
            enabled = self._checkbox.state() == 1
            _save_raw({"ki_korrektur": enabled, self._settings_key(): prompts})
        else:
            self._write_prompts(prompts)

        self._refresh_popup()

        # Callback nur wenn der gespeicherte Prompt der aktive ist
        active = next((p for p in self._get_prompts() if p.get("active")), None)
        if active and active.get("id") == self._cur_id:
            self._fire_callback(active["prompt"])

    def onDelete_(self, sender):
        """Löscht den aktuell ausgewählten Prompt."""
        idx     = self._popup.indexOfSelectedItem()
        prompts = self._get_prompts()
        if len(prompts) <= 1:
            return
        was_active = prompts[idx].get("active", False)
        prompts.pop(idx)
        if was_active and prompts:
            prompts[0]["active"] = True
        self._write_prompts(prompts)
        self._refresh_popup(select_active=True)
        active = next((p for p in prompts if p.get("active")), prompts[0])
        self._fire_callback(active["prompt"])

    def onClose_(self, sender):
        if self._mode == "live":
            enabled = self._checkbox.state() == 1
            _save_raw({"ki_korrektur": enabled})
            prompts = self._get_prompts()
            active  = next((p for p in prompts if p.get("active")), prompts[0])
            self._fire_callback(active["prompt"], enabled=enabled)
        self._win.orderOut_(None)

    # ── Callback ──────────────────────────────────────────────────────────────

    def _fire_callback(self, prompt: str, enabled=None):
        if not self._on_save:
            return
        if self._mode == "live":
            e = enabled if enabled is not None else (self._checkbox.state() == 1)
            self._on_save(e, prompt)
        else:
            self._on_save(prompt)
