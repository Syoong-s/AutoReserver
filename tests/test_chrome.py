"""Deterministic window gesture tests independent of compositor latency."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from autoreserve.desktop_chrome import WindowChrome


# ==========================================
# Function: Verify all client-drawn move/resize gestures at screen and minimum-size boundaries.
# Method: Exercise the actual geometry controller with a deterministic root and pointer events.
# ==========================================
class ChromeGestureTests(unittest.TestCase):
    # ==========================================
    # Function: Provide a managed root with known screen coordinates and minimum size.
    # Method: Mock only the window manager calls, leaving all geometry calculations real.
    # ==========================================
    def setUp(self):
        self.chrome = WindowChrome.__new__(WindowChrome)
        self.root = self.chrome.root = Mock()
        self.root.attributes.return_value = False
        self.root.winfo_x.return_value, self.root.winfo_y.return_value = 100, 100
        self.root.winfo_width.return_value, self.root.winfo_height.return_value = 1360, 930
        self.root.minsize.return_value = (1120, 780)
        self.root.after_idle.return_value = 'idle-gesture'
        self.chrome.drag, self.chrome.pending, self.chrome.job = None, None, None
        self.widget = Mock()

    # ==========================================
    # Function: Verify every resize edge and corner preserves the opposite anchor.
    # Method: Apply a positive pointer displacement and compare the exact output geometry.
    # ==========================================
    def test_all_eight_resize_directions(self):
        expected = {'n': '1360x900+100+130', 's': '1360x960+100+100',
                    'w': '1320x930+140+100', 'e': '1400x930+100+100',
                    'nw': '1320x900+140+130', 'ne': '1400x900+100+130',
                    'sw': '1320x960+140+100', 'se': '1400x960+100+100'}
        for edge, geometry in expected.items():
            with self.subTest(edge=edge):
                self.chrome.begin(SimpleNamespace(widget=self.widget, x_root=300, y_root=300), edge)
                self.chrome.motion(SimpleNamespace(x_root=340, y_root=330))
                self.chrome.flush()
                self.root.geometry.assert_called_with(geometry)

    # ==========================================
    # Function: Prevent the west/north drag from shrinking below the usable minimum.
    # Method: Drag beyond both limits and verify the far corner stays anchored.
    # ==========================================
    def test_minimum_size_keeps_opposite_corner_anchored(self):
        self.chrome.begin(SimpleNamespace(widget=self.widget, x_root=300, y_root=300), 'nw')
        self.chrome.motion(SimpleNamespace(x_root=2000, y_root=2000))
        self.chrome.flush()
        self.root.geometry.assert_called_with('1120x780+340+250')

    # ==========================================
    # Function: Preserve absolute negative screen coordinates during a title-bar drag.
    # Method: Use Tk's plus-negative notation rather than interpreting a negative as right anchoring.
    # ==========================================
    def test_move_supports_negative_screen_coordinates_and_release(self):
        self.chrome.begin(SimpleNamespace(widget=self.widget, x_root=300, y_root=300), 'move')
        event = SimpleNamespace(widget=self.widget, x_root=150, y_root=180)
        self.chrome.end(event)
        self.root.geometry.assert_called_with('1360x930+-50+-20')
        self.widget.grab_set.assert_called_once()
        self.widget.grab_release.assert_called_once()
        self.assertIsNone(self.chrome.drag)
        self.assertIsNone(self.chrome.job)

    # ==========================================
    # Function: Coalesce rapid motion without losing the final release position.
    # Method: Queue several moves and verify a single pending idle callback and final geometry.
    # ==========================================
    def test_motion_coalesces_and_release_flushes(self):
        self.chrome.begin(SimpleNamespace(widget=self.widget, x_root=300, y_root=300), 'se')
        for offset in range(10):
            self.chrome.motion(SimpleNamespace(x_root=300+offset, y_root=300+offset))
        self.root.after_idle.assert_called_once()
        self.chrome.end(SimpleNamespace(widget=self.widget, x_root=380, y_root=350))
        self.root.geometry.assert_called_once_with('1440x980+100+100')
