"""
WhisperMac – Workflows-Fenster
"""
import json
import AppKit
import objc
from workflows import load_workflows, save_workflows

_HELP = """\
Du sagst: Trigger-Wort + dein Text.   Aktion läuft VOR deinem Text,  Danach läuft DAHINTER.

  Trigger       Aktion                          Danach    →  du sagst z.B. „bullet Milch kaufen"
  bullet        enter,text:•                              →  neue Zeile + • Milch kaufen
  neue zeile    enter                                     →  Zeilenumbruch, dann: Milch kaufen
  fett          html:<b>|</b>                             →  Milch kaufen  (fett, in Word/Browser)
  liste         html:<ul><li>|</li></ul>                  →  echtes Listen-Element in Word/Browser
  klammer       text:(                         text:)     →  (Milch kaufen)\
"""


class _WorkflowTableDS(AppKit.NSObject):

    def init(self):
        self = objc.super(_WorkflowTableDS, self).init()
        if self is None:
            return None
        self._all_rows = []    # [{"trigger":…, "action":…}, …]
        self._filtered  = []
        return self

    def reload(self):
        self._all_rows = list(load_workflows())
        self._filtered  = list(self._all_rows)

    def setFilter_(self, text):
        t = text.lower().strip()
        if t:
            self._filtered = [
                r for r in self._all_rows
                if t in r.get("trigger", "").lower()
                or t in r.get("action", "").lower()
            ]
        else:
            self._filtered = list(self._all_rows)

    # ── NSTableViewDataSource ──────────────────────────────────────────────

    def numberOfRowsInTableView_(self, tv):
        return len(self._filtered)

    def tableView_objectValueForTableColumn_row_(self, tv, col, row):
        ident = str(col.identifier())
        key = "trigger" if ident == "trigger" else ("after" if ident == "after" else "action")
        return self._filtered[row].get(key, "")

    def tableView_setObjectValue_forTableColumn_row_(self, tv, val, col, row):
        ident = str(col.identifier())
        key = "trigger" if ident == "trigger" else ("after" if ident == "after" else "action")
        self._filtered[row][key] = val or ""
        self._save()

    def _save(self):
        save_workflows([r for r in self._all_rows if r.get("trigger", "").strip()])


class WorkflowsWindowController(AppKit.NSObject):

    def init(self):
        self = objc.super(WorkflowsWindowController, self).init()
        if self is None:
            return None
        self._win          = None
        self._table        = None
        self._ds           = None
        self._search_field = None
        return self

    def is_open(self):
        return self._win is not None and self._win.isVisible()

    def close(self):
        if self._win is not None:
            self._win.orderOut_(None)

    def show(self):
        if self._win is None:
            self._build()
        self._ds.reload()
        self._table.reloadData()
        self._win.makeKeyAndOrderFront_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    # ── Suche ─────────────────────────────────────────────────────────────

    def controlTextDidChange_(self, notification):
        if self._search_field and notification.object() is self._search_field:
            self._ds.setFilter_(str(self._search_field.stringValue()))
            self._table.reloadData()

    # ── Fenster aufbauen ──────────────────────────────────────────────────

    def _build(self):
        W, H     = 760, 520
        TOP_H    = 52
        HELP_H   = 108
        BOTTOM_H = 44

        win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, W, H),
            AppKit.NSWindowStyleMaskTitled |
            AppKit.NSWindowStyleMaskClosable |
            AppKit.NSWindowStyleMaskResizable |
            AppKit.NSWindowStyleMaskMiniaturizable,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        win.setTitle_("Workflows")
        win.setReleasedWhenClosed_(False)
        win.setMinSize_(AppKit.NSMakeSize(440, 320))
        win.center()
        cv = win.contentView()

        # ── Suchfeld ──────────────────────────────────────────────────────
        search = AppKit.NSSearchField.alloc().initWithFrame_(
            AppKit.NSMakeRect(12, H - TOP_H + 12, W - 24, 28)
        )
        search.setPlaceholderString_("Suchen …")
        search.setDelegate_(self)
        search.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin
        )
        cv.addSubview_(search)
        self._search_field = search

        _sep(cv, 0, H - TOP_H, W).setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin
        )

        # ── Tabelle ────────────────────────────────────────────────────────
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, BOTTOM_H + HELP_H, W, H - TOP_H - BOTTOM_H - HELP_H)
        )
        scroll.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(AppKit.NSNoBorder)

        table = AppKit.NSTableView.alloc().init()
        table.setUsesAlternatingRowBackgroundColors_(True)
        table.setRowHeight_(24.0)
        table.setGridStyleMask_(AppKit.NSTableViewSolidVerticalGridLineMask)
        table.setColumnAutoresizingStyle_(
            AppKit.NSTableViewLastColumnOnlyAutoresizingStyle
        )

        col1 = AppKit.NSTableColumn.alloc().initWithIdentifier_("trigger")
        col1.headerCell().setTitle_("Trigger (was du sagst)")
        col1.setWidth_(220)
        col1.setEditable_(True)
        col1.setResizingMask_(AppKit.NSTableColumnUserResizingMask)
        table.addTableColumn_(col1)

        col2 = AppKit.NSTableColumn.alloc().initWithIdentifier_("action")
        col2.headerCell().setTitle_("Aktion (vor dem Text)")
        col2.setWidth_(220)
        col2.setEditable_(True)
        col2.setResizingMask_(AppKit.NSTableColumnUserResizingMask)
        table.addTableColumn_(col2)

        col3 = AppKit.NSTableColumn.alloc().initWithIdentifier_("after")
        col3.headerCell().setTitle_("Danach (nach dem Text)")
        col3.setEditable_(True)
        col3.setResizingMask_(
            AppKit.NSTableColumnUserResizingMask |
            AppKit.NSTableColumnAutoresizingMask
        )
        table.addTableColumn_(col3)

        ds = _WorkflowTableDS.alloc().init()
        ds.reload()
        table.setDataSource_(ds)
        scroll.setDocumentView_(table)
        cv.addSubview_(scroll)

        # ── Hilfe-Box (scrollbar) ──────────────────────────────────────────
        _sep(cv, 0, BOTTOM_H + HELP_H - 1, W)

        help_scroll = AppKit.NSScrollView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, BOTTOM_H, W, HELP_H - 1)
        )
        help_scroll.setHasVerticalScroller_(True)
        help_scroll.setAutohidesScrollers_(True)
        help_scroll.setBorderType_(AppKit.NSNoBorder)
        help_scroll.setDrawsBackground_(False)
        help_scroll.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin
        )

        font = AppKit.NSFont.monospacedSystemFontOfSize_weight_(11.0, 0)
        attrs = {
            AppKit.NSFontAttributeName:            font,
            AppKit.NSForegroundColorAttributeName: AppKit.NSColor.secondaryLabelColor(),
        }
        astr = AppKit.NSAttributedString.alloc().initWithString_attributes_(
            _HELP, attrs
        )

        help_tv = AppKit.NSTextView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, W, HELP_H - 1)
        )
        help_tv.textStorage().setAttributedString_(astr)
        help_tv.setEditable_(False)
        help_tv.setSelectable_(True)
        help_tv.setDrawsBackground_(False)
        help_tv.textContainer().setLineFragmentPadding_(8)
        help_tv.setAutoresizingMask_(AppKit.NSViewWidthSizable)

        help_scroll.setDocumentView_(help_tv)
        cv.addSubview_(help_scroll)

        # ── Toolbar (unten) ────────────────────────────────────────────────
        _sep(cv, 0, BOTTOM_H - 1, W)

        seg = AppKit.NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            ["+", "−"],
            AppKit.NSSegmentSwitchTrackingMomentary,
            self,
            "onSegment:",
        )
        seg.setFrame_(AppKit.NSMakeRect(8, 9, 64, 26))
        seg.setAutoresizingMask_(
            AppKit.NSViewMaxXMargin | AppKit.NSViewMaxYMargin
        )
        cv.addSubview_(seg)

        # Export-Button
        btn_export = AppKit.NSButton.alloc().initWithFrame_(
            AppKit.NSMakeRect(W - 196, 9, 88, 26)
        )
        btn_export.setTitle_("Exportieren")
        btn_export.setBezelStyle_(AppKit.NSBezelStyleRounded)
        btn_export.setTarget_(self)
        btn_export.setAction_("onExport:")
        btn_export.setAutoresizingMask_(AppKit.NSViewMinXMargin)
        cv.addSubview_(btn_export)

        # Import-Button
        btn_import = AppKit.NSButton.alloc().initWithFrame_(
            AppKit.NSMakeRect(W - 100, 9, 88, 26)
        )
        btn_import.setTitle_("Importieren")
        btn_import.setBezelStyle_(AppKit.NSBezelStyleRounded)
        btn_import.setTarget_(self)
        btn_import.setAction_("onImport:")
        btn_import.setAutoresizingMask_(AppKit.NSViewMinXMargin)
        cv.addSubview_(btn_import)

        self._win   = win
        self._table = table
        self._ds    = ds

    # ── Aktionen ──────────────────────────────────────────────────────────

    def onSegment_(self, sender):
        if sender.selectedSegment() == 0:
            self._add_row()
        else:
            self._delete_row()

    def _add_row(self):
        if self._search_field:
            self._search_field.setStringValue_("")
        self._ds.setFilter_("")
        new = {"trigger": "", "action": ""}
        self._ds._all_rows.append(new)
        self._ds._filtered = list(self._ds._all_rows)
        self._table.reloadData()
        row = len(self._ds._filtered) - 1
        self._table.selectRowIndexes_byExtendingSelection_(
            AppKit.NSIndexSet.indexSetWithIndex_(row), False
        )
        self._table.scrollRowToVisible_(row)
        self._table.editColumn_row_withEvent_select_(0, row, None, True)

    def _delete_row(self):
        row = self._table.selectedRow()
        if row < 0:
            return
        target = self._ds._filtered[row]
        if target in self._ds._all_rows:
            self._ds._all_rows.remove(target)
        del self._ds._filtered[row]
        self._table.reloadData()
        self._ds._save()

    def onExport_(self, sender):
        panel = AppKit.NSSavePanel.savePanel()
        panel.setTitle_("Workflows exportieren")
        panel.setAllowedFileTypes_(["json"])
        panel.setNameFieldStringValue_("whispermac_workflows.json")
        if panel.runModal() == AppKit.NSModalResponseOK:
            path = panel.URL().path()
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(load_workflows(), f, ensure_ascii=False, indent=2)
            except Exception as e:
                alert = AppKit.NSAlert.alloc().init()
                alert.setMessageText_("Fehler beim Exportieren")
                alert.setInformativeText_(str(e))
                alert.runModal()

    def onImport_(self, sender):
        panel = AppKit.NSOpenPanel.openPanel()
        panel.setTitle_("Workflows importieren")
        panel.setAllowedFileTypes_(["json"])
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        if panel.runModal() == AppKit.NSModalResponseOK:
            path = panel.URL().path()
            try:
                with open(path, encoding="utf-8") as f:
                    imported = json.load(f)
                if not isinstance(imported, list):
                    raise ValueError("Ungültiges Dateiformat – erwartet eine Workflow-Liste.")
                existing = load_workflows()
                existing_triggers = {w.get("trigger", "").lower(): i for i, w in enumerate(existing)}
                for wf in imported:
                    trigger = wf.get("trigger", "").lower()
                    if trigger in existing_triggers:
                        existing[existing_triggers[trigger]] = wf
                    else:
                        existing.append(wf)
                save_workflows(existing)
                self._ds.reload()
                self._table.reloadData()
            except Exception as e:
                alert = AppKit.NSAlert.alloc().init()
                alert.setMessageText_("Fehler beim Importieren")
                alert.setInformativeText_(str(e))
                alert.runModal()


# ── Hilfsfunktion ─────────────────────────────────────────────────────────

def _sep(parent, x, y, w):
    box = AppKit.NSBox.alloc().initWithFrame_(AppKit.NSMakeRect(x, y, w, 1))
    box.setBoxType_(AppKit.NSBoxSeparator)
    parent.addSubview_(box)
    return box
