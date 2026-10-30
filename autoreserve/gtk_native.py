"""Finite GTK 3 C-ABI boundary, following TeleMail's Linux native architecture."""

import ctypes as C
from pathlib import Path
import sys

P, I, U, S, D = C.c_void_p, C.c_int, C.c_uint, C.c_char_p, C.c_double


# ==========================================
# Function: Read GLib-owned diagnostic messages safely.
# Method: Match GError's public layout and free it after decoding UTF-8.
# ==========================================
class GError(C.Structure):
    _fields_ = [('domain', U), ('code', I), ('message', S)]


# ==========================================
# Function: Traverse the public GLib list ABI without inspecting GTK internals.
# Method: Copy borrowed widget pointers before releasing list storage.
# ==========================================
class GList(C.Structure):
    pass


GList._fields_ = [('data', P), ('next', C.POINTER(GList)), ('prev', C.POINTER(GList))]


# ==========================================
# Function: Read GDK monitor geometry in application pixels, including negative monitor origins.
# Method: Match the public GdkRectangle ABI for scale-aware work area queries.
# ==========================================
class Rectangle(C.Structure):
    _fields_ = [('x', I), ('y', I), ('width', I), ('height', I)]


# ==========================================
# Function: Expose only the native APIs used by the reservation desktop.
# Method: Declare every function signature and own signal callbacks by widget lifetime.
# ==========================================
class GtkNative:
    # ==========================================
    # Function: Load native GTK/GLib libraries without packages or introspection bindings.
    # Method: Resolve Linux SONAMEs or app-private Windows DLLs and bind a finite typed API table.
    # ==========================================
    def __init__(self):
        from .native_runtime import load_library
        self.libs = {name: load_library(name) for name in ('gtk','gdk','object','glib','gio','pixbuf')}
        self.callbacks, self.sources, self.signal_errors = {}, {}, []
        self.retired = []
        signatures = {
            'gtk': {
                'gtk_init_check': (I,P,P), 'gtk_get_major_version': (U,), 'gtk_get_minor_version': (U,), 'gtk_get_micro_version': (U,),
                'gtk_application_new': (P,S,I), 'gtk_window_set_application': (None,P,P),
                'gtk_window_new': (P,I), 'gtk_window_present': (None,P), 'gtk_window_close': (None,P),
                'gtk_window_set_title': (None,P,S), 'gtk_window_set_default_size': (None,P,I,I),
                'gtk_window_set_icon_from_file': (I,P,S,P), 'gtk_window_get_icon': (P,P),
                'gtk_window_move': (None,P,I,I),
                'gtk_window_set_titlebar': (None,P,P), 'gtk_window_get_titlebar': (P,P),
                'gtk_window_set_transient_for': (None,P,P), 'gtk_window_set_modal': (None,P,I),
                'gtk_window_get_transient_for': (P,P), 'gtk_window_get_modal': (I,P),
                'gtk_window_set_resizable': (None,P,I), 'gtk_window_get_resizable': (I,P),
                'gtk_popover_new': (P,P), 'gtk_popover_popup': (None,P), 'gtk_popover_popdown': (None,P),
                'gtk_popover_set_position': (None,P,I), 'gtk_popover_set_transitions_enabled': (None,P,I),
                'gtk_header_bar_new': (P,), 'gtk_header_bar_set_title': (None,P,S),
                'gtk_header_bar_set_show_close_button': (None,P,I), 'gtk_header_bar_set_decoration_layout': (None,P,S),
                'gtk_header_bar_get_decoration_layout': (S,P),
                'gtk_builder_new': (P,), 'gtk_builder_add_from_string': (U,P,S,C.c_size_t,P), 'gtk_builder_get_object': (P,P,S),
                'gtk_widget_set_no_show_all': (None,P,I), 'gtk_revealer_new': (P,), 'gtk_revealer_set_reveal_child': (None,P,I), 'gtk_revealer_set_transition_duration': (None,P,U), 'gtk_widget_show_all': (None,P), 'gtk_widget_show': (None,P), 'gtk_widget_hide': (None,P),
                'gtk_widget_set_visible': (None,P,I), 'gtk_widget_get_visible': (I,P), 'gtk_widget_get_mapped': (I,P),
                'gtk_widget_destroy': (None,P), 'gtk_widget_set_sensitive': (None,P,I), 'gtk_widget_get_sensitive': (I,P),
                'gtk_widget_get_parent': (P,P), 'gtk_widget_get_window': (P,P), 'gtk_widget_realize': (None,P),
                'gtk_widget_set_name': (None,P,S), 'gtk_widget_get_style_context': (P,P),
                'gtk_widget_get_allocated_width': (I,P), 'gtk_widget_get_allocated_height': (I,P),
                'gtk_widget_set_hexpand': (None,P,I), 'gtk_widget_set_vexpand': (None,P,I),
                'gtk_widget_set_halign': (None,P,I), 'gtk_widget_set_valign': (None,P,I),
                'gtk_widget_set_size_request': (None,P,I,I), 'gtk_widget_set_tooltip_text': (None,P,S),
                'gtk_widget_set_margin_start': (None,P,I), 'gtk_widget_set_margin_end': (None,P,I),
                'gtk_widget_set_margin_top': (None,P,I), 'gtk_widget_set_margin_bottom': (None,P,I),
                'gtk_widget_draw': (None,P,P), 'gtk_widget_event': (I,P,P), 'gtk_widget_grab_focus': (None,P),
                'gtk_widget_set_can_focus': (None,P,I), 'gtk_widget_add_events': (None,P,I),
                'gtk_image_new': (P,), 'gtk_image_set_from_pixbuf': (None,P,P), 'gtk_event_box_new': (P,),
                'gtk_spinner_new': (P,), 'gtk_spinner_start': (None,P), 'gtk_spinner_stop': (None,P),
                'gtk_im_multicontext_new': (P,), 'gtk_im_context_set_client_window': (None,P,P),
                'gtk_im_context_filter_keypress': (I,P,P), 'gtk_im_context_focus_in': (None,P),
                'gtk_im_context_focus_out': (None,P),
                'gtk_clipboard_get': (P,P), 'gtk_clipboard_wait_for_text': (P,P),
                'gtk_style_context_add_class': (None,P,S), 'gtk_style_context_remove_class': (None,P,S),
                'gtk_style_context_has_class': (I,P,S),
                'gtk_container_add': (None,P,P), 'gtk_container_remove': (None,P,P),
                'gtk_container_get_children': (C.POINTER(GList),P), 'gtk_container_set_border_width': (None,P,U),
                'gtk_container_check_resize': (None,P),
                'gtk_box_new': (P,I,I), 'gtk_box_pack_start': (None,P,P,I,I,U), 'gtk_box_pack_end': (None,P,P,I,I,U),
                'gtk_paned_get_position': (I,P), 'gtk_paned_set_position': (None,P,I),
                'gtk_paned_get_handle_window': (P,P), 'gtk_paned_get_wide_handle': (I,P),
                'gtk_expander_new': (P,S), 'gtk_expander_set_expanded': (None,P,I),
                'gtk_expander_get_expanded': (I,P), 'gtk_expander_set_label': (None,P,S),
                'gtk_label_new': (P,S), 'gtk_label_set_text': (None,P,S), 'gtk_label_get_text': (S,P),
                'gtk_label_set_xalign': (None,P,C.c_float), 'gtk_label_set_line_wrap': (None,P,I),
                'gtk_label_set_ellipsize': (None,P,I), 'gtk_label_set_max_width_chars': (None,P,I),
                'gtk_entry_new': (P,), 'gtk_entry_set_text': (None,P,S), 'gtk_entry_get_text': (S,P),
                'gtk_entry_set_placeholder_text': (None,P,S), 'gtk_entry_set_width_chars': (None,P,I),
                'gtk_button_new_with_label': (P,S), 'gtk_button_set_label': (None,P,S), 'gtk_button_clicked': (None,P),
                'gtk_toggle_button_new_with_label': (P,S), 'gtk_toggle_button_set_active': (None,P,I), 'gtk_toggle_button_get_active': (I,P),
                'gtk_check_button_new_with_label': (P,S),
                'gtk_switch_new': (P,), 'gtk_switch_set_active': (None,P,I), 'gtk_switch_get_active': (I,P),
                'gtk_combo_box_text_new': (P,), 'gtk_combo_box_text_append': (None,P,S,S), 'gtk_combo_box_text_remove_all': (None,P),
                'gtk_combo_box_get_active_id': (S,P), 'gtk_combo_box_set_active_id': (I,P,S),
                'gtk_spin_button_new_with_range': (P,D,D,D), 'gtk_spin_button_set_value': (None,P,D), 'gtk_spin_button_get_value_as_int': (I,P),
                'gtk_flow_box_new': (P,), 'gtk_flow_box_set_selection_mode': (None,P,I),
                'gtk_flow_box_set_min_children_per_line': (None,P,U), 'gtk_flow_box_set_max_children_per_line': (None,P,U),
                'gtk_flow_box_set_column_spacing': (None,P,U), 'gtk_flow_box_set_row_spacing': (None,P,U),
                'gtk_list_box_new': (P,), 'gtk_list_box_set_selection_mode': (None,P,I), 'gtk_list_box_row_new': (P,), 'gtk_list_box_row_get_index': (I,P), 'gtk_list_box_get_selected_row': (P,P),
                'gtk_list_box_select_row': (None,P,P), 'gtk_list_box_get_row_at_index': (P,P,I),
                'gtk_scrolled_window_new': (P,P,P), 'gtk_scrolled_window_set_policy': (None,P,I,I),
                'gtk_scrolled_window_set_overlay_scrolling': (None,P,I),
                'gtk_scrolled_window_set_min_content_height': (None,P,I),
                'gtk_scrolled_window_set_max_content_height': (None,P,I),
                'gtk_scrolled_window_set_propagate_natural_height': (None,P,I),
                'gtk_scrolled_window_get_vadjustment': (P,P), 'gtk_adjustment_get_value': (D,P),
                'gtk_adjustment_get_lower': (D,P), 'gtk_adjustment_get_upper': (D,P), 'gtk_adjustment_get_page_size': (D,P),
                'gtk_adjustment_set_value': (None,P,D),
                'gtk_text_view_get_buffer': (P,P), 'gtk_text_buffer_set_text': (None,P,S,I),
                'gtk_text_buffer_get_end_iter': (None,P,P), 'gtk_text_buffer_insert': (None,P,P,S,I),
                'gtk_text_view_scroll_to_iter': (I,P,P,D,I,D,D),
                'gtk_text_buffer_get_insert': (P,P), 'gtk_text_buffer_place_cursor': (None,P,P),
                'gtk_text_view_scroll_to_mark': (None,P,P,D,I,D,D),
                'gtk_grid_new': (P,), 'gtk_grid_attach': (None,P,P,I,I,I,I),
                'gtk_grid_set_row_spacing': (None,P,U), 'gtk_grid_set_column_spacing': (None,P,U),
                'gtk_css_provider_new': (P,), 'gtk_css_provider_load_from_data': (I,P,S,C.c_ssize_t,P),
                'gtk_style_context_add_provider_for_screen': (None,P,P,U),
                'gtk_style_context_remove_provider_for_screen': (None,P,P),
                'gtk_main_iteration_do': (I,I), 'gtk_events_pending': (I,),
                'gtk_window_get_size': (None,P,C.POINTER(I),C.POINTER(I)), 'gtk_window_resize': (None,P,I,I),
            },
            'object': {'g_signal_connect_data': (C.c_ulong,P,S,P,P,P,I), 'g_signal_handler_disconnect': (None,P,C.c_ulong),
                       'g_object_unref': (None,P), 'g_object_ref': (P,P)},
            'glib': {'g_error_free': (None,P), 'g_free': (None,P), 'g_list_free': (None,P), 'g_timeout_add': (U,U,P,P),
                     'g_source_remove': (I,U), 'g_set_prgname': (None,S), 'g_set_application_name': (None,S)},
            'gio': {'g_application_run': (I,P,I,P), 'g_application_quit': (None,P)},
            'gdk': {'gdk_screen_get_default': (P,), 'gdk_pixbuf_get_from_window': (P,P,I,I,I,I),
                    'gdk_event_get_coords': (I,P,C.POINTER(D),C.POINTER(D)),
                    'gdk_event_get_button': (I,P,C.POINTER(U)), 'gdk_event_get_state': (I,P,C.POINTER(U)),
                    'gdk_event_get_keyval': (I,P,C.POINTER(U)), 'gdk_keyval_name': (S,U), 'gdk_keyval_to_unicode': (U,U),
                    'gdk_event_get_scroll_direction': (I,P,C.POINTER(I)),
                    'gdk_event_get_scroll_deltas': (I,P,C.POINTER(D),C.POINTER(D)),
                    'gdk_atom_intern_static_string': (P,S),
                    'gdk_display_get_default': (P,), 'gdk_display_get_primary_monitor': (P,P),
                    'gdk_display_get_monitor_at_window': (P,P,P),
                    'gdk_display_get_monitor': (P,P,I), 'gdk_monitor_get_workarea': (None,P,C.POINTER(Rectangle))},
            'pixbuf': {'gdk_pixbuf_savev': (I,P,S,S,P,P,P), 'gdk_pixbuf_loader_new': (P,),
                       'gdk_pixbuf_get_width': (I,P), 'gdk_pixbuf_get_height': (I,P),
                       'gdk_pixbuf_scale_simple': (P,P,I,I,I),
                       'gdk_pixbuf_loader_write': (I,P,P,C.c_size_t,P), 'gdk_pixbuf_loader_close': (I,P,P),
                       'gdk_pixbuf_loader_get_pixbuf': (P,P)},
        }
        for library, entries in signatures.items():
            for name, signature in entries.items():
                function = getattr(self.libs[library], name)
                function.restype, function.argtypes = signature[0], signature[1:]
                setattr(self, name, function)

    # ==========================================
    # Function: Give the Windows workspace an app icon, system titlebar and visible initial geometry.
    # Method: Configure before realization and use GDK application pixels rather than mixing Win32 device pixels.
    # ==========================================
    def configure_desktop_window(self, window):
        if sys.platform != 'win32':
            return
        self.gtk_window_set_titlebar(window, None)
        error = P()
        icon = Path(__file__).with_name('gtk_ui') / 'icon.ico'
        if not self.gtk_window_set_icon_from_file(window, str(icon).encode('utf-8'), C.byref(error)):
            message = C.cast(error, C.POINTER(GError)).contents.message.decode() if error else 'Cannot load app icon'
            if error:
                self.g_error_free(error)
            raise RuntimeError(message)
        display = self.gdk_display_get_default()
        monitor = self.gdk_display_get_primary_monitor(display) or self.gdk_display_get_monitor(display, 0)
        if monitor:
            area = Rectangle()
            self.gdk_monitor_get_workarea(monitor, C.byref(area))
            width, height = min(1380, max(1, area.width-48)), min(980, max(1, area.height-80))
            self.gtk_widget_set_size_request(window, min(900,width), min(560,height))
            self.gtk_window_set_default_size(window, width, height)
            self.gtk_window_move(window, area.x+(area.width-width)//2, area.y+(area.height-height)//2)

    # ==========================================
    # Function: Parse maintained GtkBuilder layout with useful syntax diagnostics.
    # Method: Retain the builder and free any GLib error allocation on failure.
    # ==========================================
    def builder(self, path):
        builder, error = self.gtk_builder_new(), P()
        data = Path(path).read_bytes()
        if not self.gtk_builder_add_from_string(builder, data, len(data), C.byref(error)):
            message = C.cast(error, C.POINTER(GError)).contents.message.decode() if error else 'Invalid GTK layout'
            if error:
                self.g_error_free(error)
            self.g_object_unref(builder)
            raise RuntimeError(message)
        return builder

    # ==========================================
    # Function: Resolve a declared layout widget before passing it to native APIs.
    # Method: Fail early on a missing builder ID rather than dereferencing null in C.
    # ==========================================
    def object(self, builder, name):
        widget = self.gtk_builder_get_object(builder, name.encode())
        if not widget:
            raise KeyError('GTK widget not found: '+name)
        return widget

    # ==========================================
    # Function: Bind native signals with callbacks owned by their widget lifetime.
    # Method: Disconnect and release trampolines on destroy; contain Python exceptions at the C boundary.
    # ==========================================
    def connect(self, widget, signal, callback, count=2, returns=False, types=None):
        signature = C.CFUNCTYPE(I if returns else None, *(types or [P]*count))

        # ==========================================
        # Function: Dispatch one GTK signal without unwinding a Python exception through C.
        # Method: Record diagnostics and return a safe event-propagation result on failure.
        # ==========================================
        def dispatch(*args):
            try:
                result = callback(*args)
                return int(bool(result)) if returns else None
            except Exception as exc:
                self.signal_errors.append(f'{signal}: {type(exc).__name__}: {exc}')
                print('AutoReserve GTK '+self.signal_errors[-1], file=sys.stderr, flush=True)
                return 0 if returns else None

        if widget not in self.callbacks:
            self.callbacks[widget] = []
            cleanup = C.CFUNCTYPE(None,P,P)(lambda *_: self.release_callbacks(widget))
            identity = self.g_signal_connect_data(widget,b'destroy',C.cast(cleanup,P),None,None,0)
            self.callbacks[widget].append((identity,cleanup))
        wrapped = signature(dispatch)
        identity = self.g_signal_connect_data(widget,signal.encode(),C.cast(wrapped,P),None,None,0)
        self.callbacks[widget].append((identity,wrapped))
        return identity

    # ==========================================
    # Function: Release signal ownership after a widget is destroyed.
    # Method: Defer trampoline disposal until GTK has returned from the destroy signal.
    # ==========================================
    def release_callbacks(self, widget):
        callbacks = self.callbacks.pop(widget, [])
        self.later(1, lambda: bool(callbacks) and False)

    # ==========================================
    # Function: Run a bounded GTK main-thread timeout with strong lifetime ownership.
    # Method: Keep each C trampoline until its GLib source has returned false.
    # ==========================================
    def later(self, milliseconds, callback, repeat=False):
        holder = {}

        # ==========================================
        # Function: Dispatch one GLib source and retire its callback after completion.
        # Method: Keep a local reference during the callback's final return through C.
        # ==========================================
        def dispatch(unused):
            self.retired.clear()
            keep = False
            try:
                result = callback()
                keep = bool(result) if repeat else False
            except Exception as exc:
                self.signal_errors.append(f'timeout: {type(exc).__name__}: {exc}')
                print('AutoReserve GTK '+self.signal_errors[-1],file=sys.stderr,flush=True)
                keep = repeat
            if not keep:
                self.retired.append(self.sources.pop(holder['id'], None))
            return int(keep)

        wrapped = C.CFUNCTYPE(I,P)(dispatch)
        identity = self.g_timeout_add(milliseconds,C.cast(wrapped,P),None)
        holder['id'], self.sources[identity] = identity, wrapped
        return identity

    # ==========================================
    # Function: Cancel a main-thread source before shutdown.
    # Method: Remove the GLib registration before releasing its retained trampoline.
    # ==========================================
    def cancel(self, identity):
        if identity in self.sources:
            self.g_source_remove(identity)
            self.sources.pop(identity)

    # ==========================================
    # Function: Enumerate native children for incremental views and test inspection.
    # Method: Copy borrowed widget pointers and free GList storage.
    # ==========================================
    def children(self, widget):
        head = cursor = self.gtk_container_get_children(widget)
        result = []
        while cursor:
            result.append(cursor.contents.data)
            cursor = cursor.contents.next
        self.g_list_free(head)
        return result

    # ==========================================
    # Function: Remove a dynamic widget set and its associated signals.
    # Method: Let GTK own destruction instead of manually rebinding pointer or scroll behavior.
    # ==========================================
    def clear(self, widget):
        for child in self.children(widget):
            self.gtk_widget_destroy(child)

    # ==========================================
    # Function: Apply the process-local desktop stylesheet.
    # Method: Load a GTK CSS provider and report parse errors before mapping windows.
    # ==========================================
    def css(self, path):
        provider, error = self.gtk_css_provider_new(), P()
        data = Path(path).read_bytes()
        if not self.gtk_css_provider_load_from_data(provider,data,len(data),C.byref(error)):
            message = C.cast(error,C.POINTER(GError)).contents.message.decode() if error else 'Invalid GTK CSS'
            if error:
                self.g_error_free(error)
            raise RuntimeError(message)
        self.gtk_style_context_add_provider_for_screen(self.gdk_screen_get_default(),provider,600)
        self.g_object_unref(provider)

    # ==========================================
    # Function: Update an owned CSS provider for one desktop's scoped display preferences.
    # Method: Validate UTF-8 CSS before registering a new provider and let the caller release its reference.
    # ==========================================
    def css_text(self, text, provider=None):
        created = provider is None
        provider = self.gtk_css_provider_new() if created else provider
        error, data = P(), text.encode('utf-8')
        if not self.gtk_css_provider_load_from_data(provider,data,len(data),C.byref(error)):
            message = C.cast(error,C.POINTER(GError)).contents.message.decode() if error else 'Invalid GTK CSS'
            if error:
                self.g_error_free(error)
            if created:
                self.g_object_unref(provider)
            raise RuntimeError(message)
        if created:
            self.gtk_style_context_add_provider_for_screen(self.gdk_screen_get_default(),provider,800)
        return provider

    # ==========================================
    # Function: Set a class without replacing widget identities or selection state.
    # Method: Use the native GTK style context.
    # ==========================================
    def style(self, widget, name, active=True):
        context = self.gtk_widget_get_style_context(widget)
        (self.gtk_style_context_add_class if active else self.gtk_style_context_remove_class)(context,name.encode())

    # ==========================================
    # Function: Read or replace one UTF-8 native text entry.
    # Method: Use GTK-owned strings without enabling markup or shell interpretation.
    # ==========================================
    def text(self, widget, value=None):
        if value is not None:
            self.gtk_entry_set_text(widget,str(value).encode())
        return (self.gtk_entry_get_text(widget) or b'').decode()

    # ==========================================
    # Function: Set a plain label caption.
    # Method: Keep user-supplied names out of GTK markup.
    # ==========================================
    def label(self, widget, value):
        self.gtk_label_set_text(widget,str(value).encode())

    # ==========================================
    # Function: Capture the actual allocated GTK widget tree for visual review.
    # Method: Draw to a Cairo image surface so previews work with both Wayland and X11.
    # ==========================================
    def capture(self, window, path):
        width,height=self.gtk_widget_get_allocated_width(window),self.gtk_widget_get_allocated_height(window)
        from .native_runtime import load_library
        cairo=load_library('cairo')
        signatures={'cairo_image_surface_create':(P,I,I,I),'cairo_create':(P,P),
                    'cairo_surface_write_to_png':(I,P,S),'cairo_destroy':(None,P),'cairo_surface_destroy':(None,P)}
        for name,signature in signatures.items():
            function=getattr(cairo,name)
            function.restype,function.argtypes=signature[0],signature[1:]
        surface=cairo.cairo_image_surface_create(0,width,height)
        context=cairo.cairo_create(surface)
        try:
            self.gtk_widget_draw(window,context)
            if cairo.cairo_surface_write_to_png(surface,str(path).encode()):
                raise RuntimeError('无法保存 GTK 窗口截图')
        finally:
            cairo.cairo_destroy(context)
            cairo.cairo_surface_destroy(surface)
