"""Small native workspace controls with clear selection and bounded scrolling."""

import tkinter as tk
from tkinter import ttk

BLUE, PALE, INK, MUTED = "#2563eb", "#eff6ff", "#202124", "#737780"


# ==========================================
# Function: Keep long editors usable at any supported window height.
# Method: Size an inner frame to a canvas viewport and scope wheel events to its descendants.
# ==========================================
class ScrollPanel(tk.Frame):
    # ==========================================
    # Function: Create an independently scrollable native content area.
    # Method: Link a canvas window and a slim scrollbar without global wheel bindings.
    # ==========================================
    def __init__(self, parent, bg="white", **kwargs):
        super().__init__(parent, bg=bg, **kwargs)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.scroll = ttk.Scrollbar(self, command=self.canvas.yview)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.body = tk.Frame(self.canvas, bg=bg)
        self.window = self.canvas.create_window(0, 0, anchor="nw", window=self.body)
        self.body.bind("<Configure>", self.region)
        self.canvas.bind("<Configure>", self.fit)
        self.wheel_tag = f"ScrollPanel{str(self)}"
        self.bind_class(self.wheel_tag, "<Button-4>", lambda e: self.wheel(-1))
        self.bind_class(self.wheel_tag, "<Button-5>", lambda e: self.wheel(1))
        self.bind_class(self.wheel_tag, "<MouseWheel>", lambda e: self.wheel(-int(e.delta / 120)))

    # ==========================================
    # Function: Update scroll bounds after content grows or shrinks.
    # Method: Use actual child bounds and install scoped wheel tags on new descendants.
    # ==========================================
    def region(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.tag_children(self.body)

    # ==========================================
    # Function: Allow wheel scrolling over labels, fields, and selectable cards.
    # Method: Attach one private binding tag while leaving nested scroll panels independent.
    # ==========================================
    def tag_children(self, widget):
        for child in widget.winfo_children():
            if isinstance(child, ScrollPanel):
                continue
            if not isinstance(child, (tk.Listbox, ttk.Treeview)) and self.wheel_tag not in child.bindtags():
                child.bindtags((*child.bindtags(), self.wheel_tag))
            self.tag_children(child)

    # ==========================================
    # Function: Keep content within the current viewport width.
    # Method: Resize the canvas window, preserving vertical scroll position.
    # ==========================================
    def fit(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)

    # ==========================================
    # Function: Scroll only when the content exceeds available height.
    # Method: Prevent empty overscroll and consume the wheel event locally.
    # ==========================================
    def wheel(self, units):
        if self.body.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(units, "units")
        return "break"


# ==========================================
# Function: Present courts and slots as accessible multi-select tiles.
# Method: Preserve the small Listbox editing API used by the model and regression tests.
# ==========================================
class ChoiceGrid(ScrollPanel):
    # ==========================================
    # Function: Build a fixed-height wrapping selection area.
    # Method: Store values and selections independently from the native tile widgets.
    # ==========================================
    def __init__(self, parent, columns=2, height=4):
        super().__init__(parent, bg="white", height=height * 36)
        self.pack_propagate(False)
        self.columns, self.values, self.selected, self.tiles = columns, [], set(), []
        self.disabled = False
        self.body.bind("<Configure>", self.region)
        for column in range(columns):
            self.body.columnconfigure(column, weight=1)
        self.canvas.bind("<Button-4>", lambda e: self.wheel(-1))
        self.canvas.bind("<Button-5>", lambda e: self.wheel(1))

    # ==========================================
    # Function: Render a new available court or time slot.
    # Method: Create a keyboard-focusable tile and place it in a wrapping grid.
    # ==========================================
    def insert(self, index, *values):
        for value in values:
            i = len(self.values)
            self.values.append(value)
            tile = tk.Label(self.body, text=value, font=("sans", 10), padx=8, pady=9,
                            relief="flat", bd=0, highlightthickness=1, takefocus=True, cursor="hand2")
            tile.grid(row=i // self.columns, column=i % self.columns, sticky="nsew", padx=3, pady=3)
            tile.bind("<Button-1>", lambda e, n=i: self.toggle(n))
            tile.bind("<space>", lambda e, n=i: self.toggle(n))
            tile.bind("<Return>", lambda e, n=i: self.toggle(n))
            tile.bind("<FocusIn>", lambda e, n=i: self.paint(n, True))
            tile.bind("<FocusOut>", lambda e, n=i: self.paint(n))
            self.tiles.append(tile)
            self.paint(i)

    # ==========================================
    # Function: Give selected tiles a strong blue fill and explicit check mark.
    # Method: Update only a changed tile, retaining focus and disabled feedback.
    # ==========================================
    def paint(self, index, focus=False):
        active = index in self.selected
        self.tiles[index].configure(text=("✓  " if active else "") + self.values[index],
                                    bg=BLUE if active else "#f5f6f8", fg="#9ca3af" if self.disabled else "white" if active else INK,
                                    highlightbackground=INK if focus else BLUE if active else "#e5e7eb",
                                    cursor="arrow" if self.disabled else "hand2")

    # ==========================================
    # Function: Toggle one tile with pointer or keyboard input.
    # Method: Prevent wildcard and specific-court selections from being combined.
    # ==========================================
    def toggle(self, index):
        if self.disabled:
            return "break"
        from .catalog import ANY_COURT
        if index in self.selected:
            self.selected.remove(index)
        else:
            if self.values and self.values[0] == ANY_COURT:
                previous = set(self.selected)
                if index == 0:
                    self.selected.clear()
                else:
                    self.selected.discard(0)
                for n in previous:
                    self.paint(n)
            self.selected.add(index)
        self.tiles[index].focus_set()
        self.paint(index, True)
        self.event_generate("<<ListboxSelect>>")
        return "break"

    # ==========================================
    # Function: Resolve list-style indices consistently.
    # Method: Interpret end as the final item and clamp integer indices.
    # ==========================================
    def endpoint(self, value):
        return len(self.values) - 1 if value == "end" else int(value)

    # ==========================================
    # Function: Return one value or a bounded range of values.
    # Method: Match Listbox get semantics for shared editor logic.
    # ==========================================
    def get(self, first, last=None):
        return self.values[self.endpoint(first)] if last is None else tuple(self.values[self.endpoint(first):self.endpoint(last)+1])

    # ==========================================
    # Function: Expose selected indices in stable displayed order.
    # Method: Sort the selection set without reading any widget state.
    # ==========================================
    def curselection(self):
        return tuple(sorted(self.selected))

    # ==========================================
    # Function: Apply a bounded range selection.
    # Method: Repaint changed tiles and notify selection summaries.
    # ==========================================
    def selection_set(self, first, last=None):
        for index in range(self.endpoint(first), self.endpoint(last if last is not None else first)+1):
            if 0 <= index < len(self.values):
                self.selected.add(index)
                self.paint(index)
        self.event_generate("<<ListboxSelect>>")

    # ==========================================
    # Function: Clear all or part of the selected range.
    # Method: Remove only selected indices within the requested bounds.
    # ==========================================
    def selection_clear(self, first, last=None):
        for index in list(self.selected):
            if self.endpoint(first) <= index <= self.endpoint(last if last is not None else first):
                self.selected.remove(index)
                self.paint(index)
        self.event_generate("<<ListboxSelect>>")

    # ==========================================
    # Function: Replace available choices after a project change.
    # Method: Destroy the old bounded tile set and reset value/selection state.
    # ==========================================
    def delete(self, first, last=None):
        for tile in self.tiles:
            tile.destroy()
        self.values, self.tiles, self.selected = [], [], set()
        self.canvas.yview_moveto(0)

    # ==========================================
    # Function: Reveal the default or requested choice.
    # Method: Scroll its grid row to the visible region after geometry settles.
    # ==========================================
    def see(self, index):
        if self.values:
            self.after_idle(lambda: self.reveal(int(index)))

    # ==========================================
    # Function: Reveal a selected tile after the initial geometry has settled.
    # Method: Refresh scroll bounds before moving to the requested grid row.
    # ==========================================
    def reveal(self, index):
        if index < len(self.tiles):
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
            self.canvas.yview_moveto(max(0, (index // self.columns - 1) / max(1, (len(self.values)+self.columns-1)//self.columns)))

    # ==========================================
    # Function: Support shared resize and busy-state updates.
    # Method: Translate list height/state options to frame sizing and local tile colors.
    # ==========================================
    def configure(self, cnf=None, **kwargs):
        state = kwargs.pop("state", None)
        kwargs.pop("yscrollcommand", None)
        if "height" in kwargs:
            kwargs["height"] *= 36
        super().configure(cnf, **kwargs)
        if state is not None:
            self.disabled = state == "disabled"
            for index in range(len(self.tiles)):
                self.paint(index)


# ==========================================
# Function: Add real per-goal actions to a scrollable sidebar.
# Method: Keep a Treeview-compatible row API while rendering native cards and buttons.
# ==========================================
class GoalList(ScrollPanel):
    # ==========================================
    # Function: Initialize retained goal rows and explicit row actions.
    # Method: Keep order, text, and selection separate from widget identities.
    # ==========================================
    def __init__(self, parent, copy, delete):
        super().__init__(parent, bg="#f5f5f4")
        self.rows, self.order, self.chosen = {}, [], None
        self.copy_action, self.delete_action = copy, delete

    # ==========================================
    # Function: Add a goal card with next-day and delete controls.
    # Method: Bind selection only to the card text so actions never trigger accidental selection.
    # ==========================================
    def insert(self, parent, index, iid, text, values):
        frame = tk.Frame(self.body, bg="#f5f5f4", padx=8, pady=8, highlightthickness=1)
        actions = tk.Frame(frame, bg="#f5f5f4")
        actions.pack(side="right", anchor="center")
        buttons = []
        for caption, command in [("+", self.copy_action), ("×", self.delete_action)]:
            button = tk.Label(actions, text=caption, font=("sans", 14), width=2, bg="#f5f5f4", fg=MUTED, cursor="hand2", takefocus=True)
            button.pack(side="left")
            button.bind("<Button-1>", lambda e, fn=command, key=iid: fn(key))
            button.bind("<Return>", lambda e, fn=command, key=iid: fn(key))
            button.bind("<space>", lambda e, fn=command, key=iid: fn(key))
            buttons.append(button)
        label = tk.Label(frame, text=text, font=("sans", 10), justify="left", anchor="w", bg="#f5f5f4", fg=INK, cursor="hand2", wraplength=172, takefocus=True)
        label.pack(side="left", fill="x", expand=True)
        for widget in (frame, label):
            widget.bind("<Button-1>", lambda e, key=iid: self.choose(key))
            widget.bind("<Return>", lambda e, key=iid: self.choose(key))
            widget.bind("<space>", lambda e, key=iid: self.choose(key))
        self.rows[iid] = dict(frame=frame, label=label, actions=actions, buttons=buttons, text=text, values=values)
        self.order.append(iid)
        self.repack()
        self.paint(iid)

    # ==========================================
    # Function: Notify the workspace when a card is chosen.
    # Method: Keep programmatic selection quiet and emit an event for actual user activation.
    # ==========================================
    def choose(self, identity):
        self.selection_set(identity)
        self.event_generate("<<TreeviewSelect>>")

    # ==========================================
    # Function: Highlight the active goal and distinguish recurring templates.
    # Method: Apply a pale blue active card and blue rule marker while retaining readable actions.
    # ==========================================
    def paint(self, identity):
        row = self.rows[identity]
        selected = identity == self.chosen
        bg = "#e6edfa" if selected else "#f5f5f4"
        row["frame"].configure(bg=bg, highlightbackground="#b6cdf6" if selected else bg)
        row["label"].configure(bg=bg, fg=BLUE if identity.startswith("rule:") else INK)
        row["actions"].configure(bg=bg)
        for button in row["buttons"]:
            button.configure(bg=bg, fg=BLUE if selected else MUTED)

    # ==========================================
    # Function: Select one retained row without rebuilding the sidebar.
    # Method: Repaint only the previous and new selections.
    # ==========================================
    def selection_set(self, identity):
        previous, self.chosen = self.chosen, identity
        for key in (previous, identity):
            if key in self.rows:
                self.paint(key)

    # ==========================================
    # Function: Expose current stable row identity.
    # Method: Match the shared Treeview selection contract.
    # ==========================================
    def selection(self):
        return (self.chosen,) if self.chosen in self.rows else ()

    # ==========================================
    # Function: Return visible row identities in priority order.
    # Method: Keep callers independent from Tk widget path names.
    # ==========================================
    def get_children(self):
        return tuple(self.order)

    # ==========================================
    # Function: Test whether a goal card already exists.
    # Method: Address the retained row dictionary by stable identity.
    # ==========================================
    def exists(self, identity):
        return identity in self.rows

    # ==========================================
    # Function: Query or update changed row text and status.
    # Method: Retain child widgets and repaint only updated labels.
    # ==========================================
    def item(self, identity, option=None, **kwargs):
        row = self.rows[identity]
        if kwargs:
            row.update(kwargs)
            row["label"].configure(text=row["text"])
        return row[option] if option else {"text": row["text"], "values": row["values"]}

    # ==========================================
    # Function: Remove a deleted card.
    # Method: Destroy only that row and its inline actions.
    # ==========================================
    def delete(self, identity):
        self.rows.pop(identity)["frame"].destroy()
        self.order.remove(identity)

    # ==========================================
    # Function: Return one goal's displayed priority.
    # Method: Locate its stable identity in the ordered row list.
    # ==========================================
    def index(self, identity):
        return self.order.index(identity)

    # ==========================================
    # Function: Move an existing goal without recreating its controls.
    # Method: Reorder identities and repack retained cards.
    # ==========================================
    def move(self, identity, parent, index):
        self.order.remove(identity)
        self.order.insert(index, identity)
        self.repack()

    # ==========================================
    # Function: Apply card ordering within the scrolling sidebar.
    # Method: Reuse the same row widgets with explicit vertical spacing.
    # ==========================================
    def repack(self):
        for key in self.order:
            self.rows[key]["frame"].pack_forget()
        for key in self.order:
            self.rows[key]["frame"].pack(fill="x", padx=5, pady=3)
