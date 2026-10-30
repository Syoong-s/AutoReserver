"""Bounded ownership and failure cleanup for task-local venue tabs."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from autoreserve.venue_pages import VenuePages


# ==========================================
# Function: Verify tab reuse never grows browser resources without bound.
# Method: Substitute page handles while testing production ownership, LRU and observer cleanup.
# ==========================================
class VenuePagesTests(unittest.TestCase):
    # ==========================================
    # Function: Create independent browser-page handles without starting a browser.
    # Method: Supply the exact lifecycle and timeout methods used by the cache.
    # ==========================================
    def setUp(self):
        self.context = Mock()
        self.original = self.make_page()
        self.context.new_page.side_effect = self.make_page
        self.pacer = Mock()
        self.pool = VenuePages(self.original, SimpleNamespace(action_timeout_ms=1000), self.pacer, limit=3)

    # ==========================================
    # Function: Make an open page belonging to the same task browser context.
    # Method: Track close and response subscription calls through mocks.
    # ==========================================
    def make_page(self):
        page = Mock()
        page.context = self.context
        page.is_closed.return_value = False
        return page

    # ==========================================
    # Function: Preserve adapter and calendar state when returning to an initialized venue.
    # Method: Alternate two contexts and assert object identity without another new page.
    # ==========================================
    def test_reuses_independent_page_state(self):
        first = self.pool.get('A')
        first.needs_load, first.calendar_at = False, 42
        second = self.pool.get('B')
        self.assertIs(first.page, self.original)
        self.assertIsNot(first.page, second.page)
        self.assertIs(self.pool.get('A'), first)
        self.assertFalse(first.needs_load)
        self.assertEqual(first.calendar_at, 42)
        self.assertTrue(second.needs_load)
        self.context.new_page.assert_called_once()
        second.page.on.assert_called_once_with('response', self.pacer.observe)

    # ==========================================
    # Function: Bound resources and detach adapters when venue configurations exceed the cache.
    # Method: Make B most recent and require C eviction while preserving the original account page.
    # ==========================================
    def test_lru_eviction_and_cleanup(self):
        first, second, third = [self.pool.get(key) for key in ('A', 'B', 'C')]
        for entry in (first, second, third):
            entry.site = Mock()
        self.pool.get('B')
        fourth = self.pool.get('D')
        self.assertEqual(set(self.pool.entries), {'A', 'B', 'D'})
        third.site.close.assert_called_once()
        third.page.close.assert_called_once()
        third.page.remove_listener.assert_called_once_with('response', self.pacer.observe)
        self.assertIsNot(fourth.page, self.original)
        self.pool.close()
        first.site.close.assert_called_once()
        second.site.close.assert_called_once()
        second.page.close.assert_called_once()
        fourth.page.close.assert_called_once()
        self.original.close.assert_not_called()
        self.assertEqual(self.pool.entries, {})

    # ==========================================
    # Function: Recover a manually closed venue tab without losing other initialized tabs.
    # Method: Close B's handle, request it again and require a fresh page and freshness flags.
    # ==========================================
    def test_closed_tab_is_recreated(self):
        first = self.pool.get('A')
        second = self.pool.get('B')
        second.site = Mock()
        second.page.is_closed.return_value = True
        replacement = self.pool.get('B')
        second.site.close.assert_called_once()
        self.assertIsNot(second.page, replacement.page)
        self.assertTrue(replacement.needs_load)
        self.assertIs(self.pool.get('A'), first)
