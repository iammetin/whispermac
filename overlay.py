"""
Modernes Wellenform-Overlay für WhisperMac.
Pill-Shape mit Frosted-Glass-Effekt, organischer Wellenform und Aufnahme-Indikator.
Das Fenster wird beim Start vorgebaut und nur noch gezeigt/versteckt.
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
        self._level     = 0.0
        self._phase     = 0.0
        self._dot_phase = 0.0
        return self

    def setLevel_(self, v):    self._level = v
    def setPhase_(self, v):    self._phase = v
    def setDotPhase_(self, v): self._dot_phase = v

    def isFlipped(self): return False

    def drawRect_(self, rect):
        NSColor.clearColor().set()
        AppKit.NSRectFill(rect)

        w     = rect.size.width
        h     = rect.size.height
        mid_y = h / 2.0

        # ── Pulsierender Aufnahme-Punkt ───────────────────────────────────
        pulse   = math.sin(self._dot_phase) * 0.25 + 0.75
        dot_r   = DOT_RADIUS * pulse
        dot_x   = 18.0
        NSColor.systemRedColor().colorWithAlphaComponent_(0.92).set()
        dot_rect = NSMakeRect(dot_x - dot_r, mid_y - dot_r, dot_r * 2, dot_r * 2)
        NSBezierPath.bezierPathWithOvalInRect_(dot_rect).fill()

        # ── Wellenform-Balken ─────────────────────────────────────────────
        bar_start = dot_x + DOT_RADIUS + 14.0
        bar_area  = w - bar_start - 10.0
        step      = bar_area / BAR_COUNT

        NSColor.colorWithRed_green_blue_alpha_(1.0, 1.0, 1.0, 0.88).set()

        for i in range(BAR_COUNT):
            x = bar_start + i * step + (step - BAR_W) / 2.0

            t  = self._phase
            w1 = math.sin(t * 1.3 + i * 0.55)
            w2 = math.sin(t * 0.7 + i * 0.30) * 0.6
            w3 = math.sin(t * 2.1 + i * 0.80) * 0.3
            wave = ((w1 + w2 + w3) / 1.9) * 0.5 + 0.5

            effective = max(self._level, 0.06)
            bar_h     = 2.5 + wave * effective * (h * 0.78 - 2.5)

            bar_rect = NSMakeRect(x, mid_y - bar_h / 2.0, BAR_W, bar_h)
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                bar_rect, BAR_W / 2.0, BAR_W / 2.0
            )
            path.fill()


class RecordingOverlay:

    def __init__(self):
        self._window    = None
        self._waveview  = None
        self._running   = False
        self._phase     = 0.0
        self._dot_phase = 0.0
        self._level     = 0.0
        self._get_level = None

    # ── Vorbau beim App-Start (Main-Thread) ───────────────────────────────

    def prebuild(self):
        """Fenster einmalig erstellen und versteckt halten – kein Aufbau-Delay beim Drücken."""
        AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(self._build_window)

    def _screen_with_cursor(self):
        """Gibt den Bildschirm zurück, auf dem der Cursor gerade ist."""
        mouse = AppKit.NSEvent.mouseLocation()
        for screen in NSScreen.screens():
            if AppKit.NSPointInRect(mouse, screen.frame()):
                return screen
        return NSScreen.mainScreen()

    def _build_window(self):
        screen = self._screen_with_cursor()
        sw = screen.frame().size.width
        sx = screen.frame().origin.x
        x  = sx + (sw - OVERLAY_WIDTH) / 2.0
        y  = screen.frame().origin.y + 32.0

        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, OVERLAY_WIDTH, OVERLAY_HEIGHT),
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

        radius = OVERLAY_HEIGHT / 2.0
        fx = AppKit.NSVisualEffectView.alloc().initWithFrame_(
            NSMakeRect(0, 0, OVERLAY_WIDTH, OVERLAY_HEIGHT)
        )
        fx.setMaterial_(13)   # HUDWindow – dunkles Glas
        fx.setBlendingMode_(0)
        fx.setState_(1)
        fx.setWantsLayer_(True)
        fx.layer().setCornerRadius_(radius)
        fx.layer().setMasksToBounds_(True)
        win.setContentView_(fx)

        wv = WaveformView.alloc().initWithFrame_(
            NSMakeRect(0, 0, OVERLAY_WIDTH, OVERLAY_HEIGHT)
        )
        fx.addSubview_(wv)

        self._window  = win
        self._waveview = wv
        # Fenster bleibt versteckt bis show() aufgerufen wird

    # ── public API (immer vom Main-Thread aufrufen) ───────────────────────

    def show(self, get_level_fn):
        """Sofortige Anzeige – kein Dispatch, kein Window-Aufbau."""
        if self._running:
            return
        self._running   = True
        self._get_level = get_level_fn

        # Fenster positionieren und anzeigen (sofort, kein Dispatch nötig)
        if self._window is None:
            self._build_window()   # Fallback falls prebuild nicht aufgerufen wurde

        screen = self._screen_with_cursor()
        sw = screen.frame().size.width
        sx = screen.frame().origin.x
        x  = sx + (sw - OVERLAY_WIDTH) / 2.0
        y  = screen.frame().origin.y + 32.0
        self._window.setFrameOrigin_(AppKit.NSMakePoint(x, y))
        self._window.orderFront_(None)

        threading.Thread(target=self._animate_loop, daemon=True).start()

    def hide(self):
        """Sofortiges Verstecken – Fenster bleibt im Speicher für nächste Nutzung."""
        self._running = False
        if self._window:
            self._window.orderOut_(None)

    # ── Animation-Loop (Hintergrund-Thread) ──────────────────────────────

    def _animate_loop(self):
        while self._running:
            if self._get_level:
                self._level = self._get_level()
            self._phase     += 0.14
            self._dot_phase += 0.12

            lv = self._level
            ph = self._phase
            dp = self._dot_phase

            def _redraw(l=lv, p=ph, d=dp):
                if self._waveview:
                    self._waveview.setLevel_(l)
                    self._waveview.setPhase_(p)
                    self._waveview.setDotPhase_(d)
                    self._waveview.setNeedsDisplay_(True)

            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(_redraw)
            time.sleep(0.04)
