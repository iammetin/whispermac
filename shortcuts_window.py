"""
WhisperMac – Kürzel-Fenster (überarbeitetes Design)
"""
import json
import AppKit
import objc
from shortcuts import load_shortcuts, save_shortcuts


class _TableDS(AppKit.NSObject):

    def init(self):
        self = objc.super(_TableDS, self).init()
        if self is None:
            return None
        self._all_rows = []      # [[wort, ersetzung], …]
        self._filtered = []      # aktuell angezeigte Zeilen
        return self

    def reload(self):
        d = load_shortcuts()
        self._all_rows = [[k, v] for k, v in d.items()]
        self._filtered = list(self._all_rows)

    def setFilter_(self, text):
        t = text.lower().strip()
        if t:
            self._filtered = [
                r for r in self._all_rows
                if t in r[0].lower() or t in r[1].lower()
            ]
        else:
            self._filtered = list(self._all_rows)

    # ── NSTableViewDataSource ──────────────────────────────────────────────

    def numberOfRowsInTableView_(self, tv):
        return len(self._filtered)

    def tableView_objectValueForTableColumn_row_(self, tv, col, row):
        return self._filtered[row][0 if str(col.identifier()) == "word" else 1]

    def tableView_setObjectValue_forTableColumn_row_(self, tv, val, col, row):
        # _filtered enthält dieselben Listenobjekte wie _all_rows → in-place update reicht
        self._filtered[row][0 if str(col.identifier()) == "word" else 1] = val or ""
        self._save()

    # ── intern ─────────────────────────────────────────────────────────────

    def _save(self):
        save_shortcuts({r[0]: r[1] for r in self._all_rows if r[0].strip()})


class ShortcutsWindowController(AppKit.NSObject):

    def init(self):
        self = objc.super(ShortcutsWindowController, self).init()
        if self is None:
            return None
        self._win          = None
        self._table        = None
        self._ds           = None
        self._search_field = None
        return self

    # ── öffentliche API ───────────────────────────────────────────────────

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

    # ── Suche (NSTextFieldDelegate) ───────────────────────────────────────

    def controlTextDidChange_(self, notification):
        if self._search_field and notification.object() is self._search_field:
            self._ds.setFilter_(str(self._search_field.stringValue()))
            self._table.reloadData()

    # ── Fenster aufbauen ──────────────────────────────────────────────────

    def _build(self):
        W, H = 540, 420
        TOP_H    = 52   # Suchfeld
        BOTTOM_H = 44   # Toolbar mit Buttons

        win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, W, H),
            AppKit.NSWindowStyleMaskTitled |
            AppKit.NSWindowStyleMaskClosable |
            AppKit.NSWindowStyleMaskResizable |
            AppKit.NSWindowStyleMaskMiniaturizable,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        win.setTitle_("Textkürzel")
        win.setReleasedWhenClosed_(False)
        win.setMinSize_(AppKit.NSMakeSize(360, 280))
        win.center()
        cv = win.contentView()

        # ── Suchfeld (oben) ────────────────────────────────────────────────
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

        # Trennlinie unter Suchfeld
        sep_top = AppKit.NSBox.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, H - TOP_H, W, 1)
        )
        sep_top.setBoxType_(AppKit.NSBoxSeparator)
        sep_top.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin
        )
        cv.addSubview_(sep_top)

        # ── Tabelle (Mitte) ────────────────────────────────────────────────
        scroll = AppKit.NSScrollView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, BOTTOM_H, W, H - TOP_H - BOTTOM_H)
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

        col1 = AppKit.NSTableColumn.alloc().initWithIdentifier_("word")
        col1.headerCell().setTitle_("Wort / Phrase")
        col1.setWidth_(220)
        col1.setEditable_(True)
        col1.setResizingMask_(AppKit.NSTableColumnUserResizingMask)
        table.addTableColumn_(col1)

        col2 = AppKit.NSTableColumn.alloc().initWithIdentifier_("replacement")
        col2.headerCell().setTitle_("Ersetzung")
        col2.setEditable_(True)
        col2.setResizingMask_(
            AppKit.NSTableColumnUserResizingMask |
            AppKit.NSTableColumnAutoresizingMask
        )
        table.addTableColumn_(col2)

        ds = _TableDS.alloc().init()
        ds.reload()
        table.setDataSource_(ds)

        scroll.setDocumentView_(table)
        cv.addSubview_(scroll)

        # ── Trennlinie über Toolbar ────────────────────────────────────────
        sep_bot = AppKit.NSBox.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, BOTTOM_H - 1, W, 1)
        )
        sep_bot.setBoxType_(AppKit.NSBoxSeparator)
        sep_bot.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        cv.addSubview_(sep_bot)

        # ── + / − als NSSegmentedControl (nativer macOS-Stil) ─────────────
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

        # Hinweis-Text
        hint = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(82, 13, W - 294, 18)
        )
        hint.setStringValue_("Doppelklick auf eine Zelle zum Bearbeiten")
        hint.setFont_(AppKit.NSFont.systemFontOfSize_(11.0))
        hint.setTextColor_(AppKit.NSColor.tertiaryLabelColor())
        hint.setBezeled_(False)
        hint.setDrawsBackground_(False)
        hint.setEditable_(False)
        hint.setSelectable_(False)
        hint.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        cv.addSubview_(hint)

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

    # ── Button-Aktionen ───────────────────────────────────────────────────

    def onSegment_(self, sender):
        if sender.selectedSegment() == 0:
            self._add_row()
        else:
            self._delete_row()

    def _add_row(self):
        # Suche zurücksetzen damit neue Zeile sichtbar ist
        if self._search_field:
            self._search_field.setStringValue_("")
        self._ds.setFilter_("")
        self._ds._all_rows.append(["", ""])
        self._ds._filtered = list(self._ds._all_rows)
        self._table.reloadData()
        new_row = len(self._ds._filtered) - 1
        self._table.selectRowIndexes_byExtendingSelection_(
            AppKit.NSIndexSet.indexSetWithIndex_(new_row), False
        )
        self._table.scrollRowToVisible_(new_row)
        self._table.editColumn_row_withEvent_select_(0, new_row, None, True)

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
        panel.setTitle_("Kürzel exportieren")
        panel.setAllowedFileTypes_(["json"])
        panel.setNameFieldStringValue_("whispermac_shortcuts.json")
        if panel.runModal() == AppKit.NSModalResponseOK:
            path = panel.URL().path()
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(load_shortcuts(), f, ensure_ascii=False, indent=2)
            except Exception as e:
                alert = AppKit.NSAlert.alloc().init()
                alert.setMessageText_("Fehler beim Exportieren")
                alert.setInformativeText_(str(e))
                alert.runModal()

    def onImport_(self, sender):
        panel = AppKit.NSOpenPanel.openPanel()
        panel.setTitle_("Kürzel importieren")
        panel.setAllowedFileTypes_(["json"])
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        if panel.runModal() == AppKit.NSModalResponseOK:
            path = panel.URL().path()
            try:
                with open(path, encoding="utf-8") as f:
                    imported = json.load(f)
                if not isinstance(imported, dict):
                    raise ValueError("Ungültiges Dateiformat – erwartet ein Kürzel-Objekt.")
                existing = load_shortcuts()
                existing.update(imported)
                save_shortcuts(existing)
                self._ds.reload()
                self._table.reloadData()
            except Exception as e:
                alert = AppKit.NSAlert.alloc().init()
                alert.setMessageText_("Fehler beim Importieren")
                alert.setInformativeText_(str(e))
                alert.runModal()
