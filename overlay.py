"""
Modernes Wellenform-Overlay für WhisperMac.
Pill-Shape mit Frosted-Glass-Effekt, organischer Wellenform und Aufnahme-Indikator.
"""
import math
import threading
import time

import AppKit
import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSMakeRect,
    NSScreen,
    NSView,
    NSWindow,
    NSWindowStyleMaskBorderless,
)

OVERLAY_WIDTH  = 320
OVERLAY_HEIGHT = 56
BAR_COUNT      = 32
BAR_W          = 2.5
DOT_RADIUS     = 4.5


class WaveformView(NSView):

    def initWithFrame_(self, frame):
        self = objc.super(WaveformView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._level = 0.0
        self._phase = 0.0
        self._dot_phase = 0.0
        return self

    def setLevel_(self, v):   self._level = v
    def setPhase_(self, v):   self._phase = v
    def setDotPhase_(self, v): self._dot_phase = v

    def isFlipped(self): return False

    def drawRect_(self, rect):
        NSColor.clearColor().set()
        AppKit.NSRectFill(rect)

        w      = rect.size.width
        h      = rect.size.height
        mid_y  = h / 2.0

        # ── Aufnahme-Punkt (pulsierender roter Kreis) ─────────────────────
        pulse    = math.sin(self._dot_phase) * 0.25 + 0.75   # 0.5 … 1.0
        dot_r    = DOT_RADIUS * pulse
        dot_x    = 18.0
        dot_col  = NSColor.systemRedColor().colorWithAlphaComponent_(0.92)
        dot_col.set()
        dot_rect = NSMakeRect(dot_x - dot_r, mid_y - dot_r, dot_r * 2, dot_r * 2)
        NSBezierPath.bezierPathWithOvalInRect_(dot_rect).fill()

        # ── Wellenform-Balken ─────────────────────────────────────────────
        bar_start = dot_x + DOT_RADIUS + 14.0
        bar_area  = w - bar_start - 10.0
        step      = bar_area / BAR_COUNT

        # Farbe: weiß, leicht transparent
        NSColor.colorWithRed_green_blue_alpha_(1.0, 1.0, 1.0, 0.88).set()

        for i in range(BAR_COUNT):
            x = bar_start + i * step + (step - BAR_W) / 2.0

            # Organische Welle aus 3 überlagerten Sinuswellen
            t  = self._phase
            w1 = math.sin(t * 1.3 + i * 0.55)
            w2 = math.sin(t * 0.7 + i * 0.30) * 0.6
            w3 = math.sin(t * 2.1 + i * 0.80) * 0.3
            wave = ((w1 + w2 + w3) / 1.9) * 0.5 + 0.5   # 0 … 1

            min_h = 2.5
            max_h = h * 0.78
            # Begrenzter Mindestpegel damit es nie komplett flach wird
            effective = max(self._level, 0.06)
            bar_h = min_h + wave * effective * (max_h - min_h)

            bar_rect = NSMakeRect(x, mid_y - bar_h / 2.0, BAR_W, bar_h)
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                bar_rect, BAR_W / 2.0, BAR_W / 2.0
            )
            path.fill()


class RecordingOverlay:

    def __init__(self):
        self._window   = None
        self._waveview = None
        self._running  = False
        self._phase    = 0.0
        self._dot_phase = 0.0
        self._level    = 0.0
        self._get_level = None

    # ── public API ────────────────────────────────────────────────────────

    def show(self, get_level_fn):
        if self._running:
            return
        self._running    = True
        self._get_level  = get_level_fn
        self._dispatch(self._show_impl)
        threading.Thread(target=self._animate_loop, daemon=True).start()

    def hide(self):
        self._running = False
        self._dispatch(self._hide_impl)

    # ── main-thread helpers ───────────────────────────────────────────────

    def _dispatch(self, fn):
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(fn)

    def _show_impl(self):
        screen = NSScreen.mainScreen()
        sw = screen.frame().size.width

        x     = (sw - OVERLAY_WIDTH) / 2.0
        y     = 32.0
        frame = NSMakeRect(x, y, OVERLAY_WIDTH, OVERLAY_HEIGHT)

        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        win.setLevel_(AppKit.NSStatusWindowLevel + 2)
        win.setOpaque_(False)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setIgnoresMouseEvents_(True)
        win.setHasShadow_(True)
        win.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
            AppKit.NSWindowCollectionBehaviorStationary
        )

        # ── Frosted-Glass Hintergrund (NSVisualEffectView) ────────────────
        radius = OVERLAY_HEIGHT / 2.0
        fx = AppKit.NSVisualEffectView.alloc().initWithFrame_(
            NSMakeRect(0, 0, OVERLAY_WIDTH, OVERLAY_HEIGHT)
        )
        fx.setMaterial_(13)   # NSVisualEffectMaterialHUDWindow  → dunkles Glas
        fx.setBlendingMode_(0)  # BehindWindow
        fx.setState_(1)         # Active
        fx.setWantsLayer_(True)
        fx.layer().setCornerRadius_(radius)
        fx.layer().setMasksToBounds_(True)

        win.setContentView_(fx)

        # ── Wellenform-View ───────────────────────────────────────────────
        wv = WaveformView.alloc().initWithFrame_(
            NSMakeRect(0, 0, OVERLAY_WIDTH, OVERLAY_HEIGHT)
        )
        fx.addSubview_(wv)

        self._window   = win
        self._waveview = wv
        win.orderFront_(None)

    def _hide_impl(self):
        if self._window:
            self._window.orderOut_(None)
            self._window   = None
            self._waveview = None

    def _redraw_impl(self):
        if self._waveview:
            self._waveview.setLevel_(self._level)
            self._waveview.setPhase_(self._phase)
            self._waveview.setDotPhase_(self._dot_phase)
            self._waveview.setNeedsDisplay_(True)

    # ── Animation-Loop (Hintergrund-Thread) ───────────────────────────────

    def _animate_loop(self):
        while self._running:
            if self._get_level:
                self._level = self._get_level()
            self._phase     += 0.14
            self._dot_phase += 0.12
            self._dispatch(self._redraw_impl)
            time.sleep(0.04)   # ~25 fps – flüssig aber sparsam
