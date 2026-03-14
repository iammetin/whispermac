"""
WhisperMac – Workflows-Fenster
"""
import AppKit
import objc
from workflows import load_workflows, save_workflows

_HELP = (
    "Aktions-Format:  enter  ·  tab  ·  cmd+b  ·  cmd+shift+k  ·  enter,enter\n"
    "Modifier: cmd  shift  opt  ctrl      Tasten: enter tab space delete a-z 0-9 left right up down"
)


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
        key = "trigger" if str(col.identifier()) == "trigger" else "action"
        return self._filtered[row].get(key, "")

    def tableView_setObjectValue_forTableColumn_row_(self, tv, val, col, row):
        key = "trigger" if str(col.identifier()) == "trigger" else "action"
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
        W, H     = 600, 460
        TOP_H    = 52
        HELP_H   = 48
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
        col1.headerCell().setTitle_("Trigger-Phrase (was du sagst)")
        col1.setWidth_(260)
        col1.setEditable_(True)
        col1.setResizingMask_(AppKit.NSTableColumnUserResizingMask)
        table.addTableColumn_(col1)

        col2 = AppKit.NSTableColumn.alloc().initWithIdentifier_("action")
        col2.headerCell().setTitle_("Aktion")
        col2.setEditable_(True)
        col2.setResizingMask_(
            AppKit.NSTableColumnUserResizingMask |
            AppKit.NSTableColumnAutoresizingMask
        )
        table.addTableColumn_(col2)

        ds = _WorkflowTableDS.alloc().init()
        ds.reload()
        table.setDataSource_(ds)
        scroll.setDocumentView_(table)
        cv.addSubview_(scroll)

        # ── Hilfe-Box ──────────────────────────────────────────────────────
        _sep(cv, 0, BOTTOM_H + HELP_H - 1, W)

        help_box = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(12, BOTTOM_H + 6, W - 24, HELP_H - 10)
        )
        help_box.setStringValue_(_HELP)
        help_box.setFont_(AppKit.NSFont.monospacedSystemFontOfSize_weight_(10.5, 0))
        help_box.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        help_box.setBezeled_(False)
        help_box.setDrawsBackground_(False)
        help_box.setEditable_(False)
        help_box.setSelectable_(True)
        help_box.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        cv.addSubview_(help_box)

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


# ── Hilfsfunktion ─────────────────────────────────────────────────────────

def _sep(parent, x, y, w):
    box = AppKit.NSBox.alloc().initWithFrame_(AppKit.NSMakeRect(x, y, w, 1))
    box.setBoxType_(AppKit.NSBoxSeparator)
    parent.addSubview_(box)
    return box
