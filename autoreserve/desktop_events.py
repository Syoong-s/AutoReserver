"""Wake Tk through a Unix pipe; workers never call the Tcl interpreter."""

import os


# ==========================================
# Function: Deliver worker events immediately without a fast permanent polling timer.
# Method: Register a nonblocking pipe with Tk's Unix file-event loop and release it on destroy.
# ==========================================
class EventWakeup:
    # ==========================================
    # Function: Attach one lightweight native event source to the root window.
    # Method: Let worker writes wake the main-thread callback without cross-thread Tk calls.
    # ==========================================
    def __init__(self, root, callback):
        self.root, self.callback = root, callback
        self.reader, self.writer = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
        root.createfilehandler(self.reader, 2, self.ready)
        root.bind("<Destroy>", self.destroyed, add="+")

    # ==========================================
    # Function: Signal queued work without blocking the emitting worker.
    # Method: Coalesce notifications naturally when the small pipe buffer is full.
    # ==========================================
    def notify(self):
        try:
            os.write(self.writer, b"x")
        except (BlockingIOError, OSError):
            pass

    # ==========================================
    # Function: Consume all wake bytes and process one bounded GUI event batch.
    # Method: Drain the nonblocking descriptor before dispatching on the Tk thread.
    # ==========================================
    def ready(self, descriptor, mask):
        try:
            while os.read(self.reader, 4096):
                pass
        except BlockingIOError:
            pass
        self.callback()

    # ==========================================
    # Function: Release event resources exactly when this root is destroyed.
    # Method: Ignore child destruction and tolerate late worker notifications.
    # ==========================================
    def destroyed(self, event):
        if event.widget is self.root:
            self.root.deletefilehandler(self.reader)
            os.close(self.reader)
            os.close(self.writer)
            self.reader = self.writer = -1
