"""Bounded, task-local venue tabs with independent calendar and adapter state."""

from dataclasses import dataclass


# ==========================================
# Function: Retain one venue's initialized page between monitoring rounds.
# Method: Keep navigation freshness and selection state with the page that owns them.
# ==========================================
@dataclass
class VenuePage:
    page: object
    site: object = None
    effective: object = None
    needs_load: bool = True
    selected_dirty: bool = False
    calendar_at: float = 0


# ==========================================
# Function: Avoid reloading entire booking applications on each venue switch.
# Method: Reuse a bounded LRU of tabs inside the task's existing browser and read budget.
# ==========================================
class VenuePages:
    # ==========================================
    # Function: Associate the existing task browser with a small tab budget.
    # Method: Preserve its original page for authentication and close only owned additional pages.
    # ==========================================
    def __init__(self, page, config, pacer, limit=4):
        self.original, self.config, self.pacer = page, config, pacer
        self.limit = max(2, limit)
        self.entries = {}

    # ==========================================
    # Function: Activate an initialized venue or create its isolated document.
    # Method: Evict the least recently used extra tab before opening beyond the bound.
    # ==========================================
    def get(self, context):
        entry = self.entries.pop(context, None)
        if entry is not None and entry.page.is_closed():
            self.release(entry)
            entry = None
        if entry is None:
            if len(self.entries) >= self.limit:
                oldest = next(key for key, value in self.entries.items() if value.page is not self.original)
                self.release(self.entries.pop(oldest))
            if not self.entries and not self.original.is_closed():
                page = self.original
            else:
                page = self.original.context.new_page()
                page.set_default_timeout(self.config.action_timeout_ms)
                page.set_default_navigation_timeout(30000)
                page.on('response', self.pacer.observe)
            entry = VenuePage(page)
        self.entries[context] = entry
        return entry

    # ==========================================
    # Function: Release one adapter and any additional browser tab it owns.
    # Method: Remove native read observers before closing a cached document.
    # ==========================================
    def release(self, entry):
        if entry.site is not None:
            entry.site.close()
        if entry.page is not self.original:
            entry.page.remove_listener('response', self.pacer.observe)
            if not entry.page.is_closed():
                entry.page.close()

    # ==========================================
    # Function: Leave no background venue tabs after a task stops or fails.
    # Method: Close every owned adapter and extra tab while preserving the caller's original page.
    # ==========================================
    def close(self):
        for entry in self.entries.values():
            self.release(entry)
        self.entries.clear()
