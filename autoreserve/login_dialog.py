"""Native transient login window rendering the official page from a memory-only browser surface."""

import ctypes as C
import time

from .gtk_native import P, S, U, I, D, Rectangle
from .login_surface import LoginSurface, LoginFrame

KEYS = {'Return':'Enter', 'ISO_Enter':'Enter', 'KP_Enter':'Enter', 'BackSpace':'Backspace',
        'ISO_Left_Tab':'Tab', 'Page_Up':'PageUp', 'Page_Down':'PageDown',
        'Left':'ArrowLeft', 'Right':'ArrowRight', 'Up':'ArrowUp', 'Down':'ArrowDown'}
SPECIAL = {'Tab', 'Escape', 'Delete', 'Home', 'End', 'Insert', 'PageUp', 'PageDown',
           'Enter', 'Backspace', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'}


# ==========================================
# Function: Display and interact with the real official login webpage within a GTK child window.
# Method: Decode the latest browser frame in memory and forward native pointer, keyboard, IME and paste gestures.
# ==========================================
class LoginDialog:
    # ==========================================
    # Function: Build a close-only parent-owned official login popup sized for its parent's monitor.
    # Method: Start with a compact placeholder, then fit the actual official login content within the monitor work area.
    # ==========================================
    def __init__(self, app):
        self.app, self.g = app, app.g
        g = self.g
        area = Rectangle()
        display = g.gdk_display_get_default()
        parent_window=g.gtk_widget_get_window(app.window)
        monitor=(g.gdk_display_get_monitor_at_window(display,parent_window) if parent_window else None)
        monitor=monitor or g.gdk_display_get_primary_monitor(display) or g.gdk_display_get_monitor(display, 0)
        g.gdk_monitor_get_workarea(monitor, C.byref(area))
        self.max_width, self.max_height = min(1100,max(260,area.width-80)), min(760,max(160,area.height-180))
        self.surface = LoginSurface(1100,760)
        width, height = min(560,self.max_width), min(340,self.max_height)
        self.window, body, _ = app.child_window('官方账号登录', 1, 1)
        g.gtk_window_set_modal(self.window, True)
        self.caption = app.pack(body, app.label('正在打开官方登录页…', 'caption'))
        self.events = g.gtk_event_box_new()
        self.image = g.gtk_image_new()
        g.gtk_widget_set_size_request(self.events, width, height)
        g.gtk_widget_set_sensitive(self.events,False)
        g.gtk_widget_set_halign(self.image, 1)
        g.gtk_widget_set_valign(self.image, 1)
        g.gtk_container_add(self.events, self.image)
        app.pack(body, self.events)
        app.pack(body, app.label('请在官方网页完成登录或扫码；成功后会自动保存。', 'caption'))
        g.gtk_widget_set_can_focus(self.events, True)
        g.gtk_widget_add_events(self.events, (1<<2)|(1<<8)|(1<<9)|(1<<21)|(1<<23))
        self.im = g.gtk_im_multicontext_new()
        self.closed, self.last_move = False, 0
        self.view = (0,0,1,1)
        self.display_size = (width,height)
        self.im_callback = C.CFUNCTYPE(None,P,S,P)(lambda _, text, __: self.surface.send('text', text.decode('utf-8')) if text else None)
        self.im_handler = g.g_signal_connect_data(self.im, b'commit', C.cast(self.im_callback,P), None, None, 0)
        g.connect(self.events, 'button-press-event', lambda _, event, __: self.pointer(event,'down'), count=3, returns=True)
        g.connect(self.events, 'button-release-event', lambda _, event, __: self.pointer(event,'up'), count=3, returns=True)
        g.connect(self.events, 'motion-notify-event', lambda _, event, __: self.motion(event), count=3, returns=True)
        g.connect(self.events, 'scroll-event', lambda _, event, __: self.scroll(event), count=3, returns=True)
        g.connect(self.window, 'key-press-event', lambda _, event, __: self.key(event), count=3, returns=True)
        g.connect(self.window, 'key-release-event', lambda _, event, __: g.gtk_im_context_filter_keypress(self.im,event) if self.im else False, count=3, returns=True)
        g.connect(self.window, 'delete-event', lambda *_: self.close(cancel=True), count=3, returns=True)
        g.connect(self.window, 'destroy', lambda *_: self.dispose())
        g.gtk_widget_show_all(self.window)
        g.gtk_widget_realize(self.events)
        g.gtk_im_context_set_client_window(self.im, g.gtk_widget_get_window(self.events))
        g.gtk_im_context_focus_in(self.im)
        g.gtk_widget_grab_focus(self.events)
        g.gtk_window_present(self.window)

    # ==========================================
    # Function: Forward exact native viewport coordinates and mouse button transitions.
    # Method: Focus the webpage, then send movement before the corresponding press or release.
    # ==========================================
    def pointer(self, event, kind):
        x, y, button = D(), D(), U()
        g = self.g
        if g.gdk_event_get_coords(event,C.byref(x),C.byref(y)) and g.gdk_event_get_button(event,C.byref(button)):
            if button.value in {1,2,3}:
                point=self.page_coordinates(x.value,y.value)
                if point is None:
                    if kind=='up':
                        self.surface.send('up',{1:'left',2:'middle',3:'right'}[button.value])
                    return True
                g.gtk_widget_grab_focus(self.events)
                self.surface.send('move', *point)
                self.surface.send(kind, {1:'left',2:'middle',3:'right'}[button.value])
        return True

    # ==========================================
    # Function: Support hover and drag-based official verification without excessive event backlog.
    # Method: Coalesce native motion to at most thirty updates per second.
    # ==========================================
    def motion(self, event):
        x, y = D(), D()
        now = time.monotonic()
        if now-self.last_move >= .03 and self.g.gdk_event_get_coords(event,C.byref(x),C.byref(y)):
            self.last_move = now
            point=self.page_coordinates(x.value,y.value)
            if point is not None:
                self.surface.send('move', *point)
        return True

    # ==========================================
    # Function: Keep clicks and verification drags accurate after cropping or scaling the login image.
    # Method: Map native image pixels back to the displayed frame's browser viewport, ignoring surrounding padding.
    # ==========================================
    def page_coordinates(self, x, y):
        width,height=self.display_size
        if not (0 <= x < width and 0 <= y < height):
            return None
        left,top,scale_x,scale_y=self.view
        return left+x*scale_x, top+y*scale_y

    # ==========================================
    # Function: Scroll longer official login and verification pages inside the popup.
    # Method: Convert both discrete wheel directions and smooth native deltas to browser viewport movement.
    # ==========================================
    def scroll(self, event):
        x, y, direction = D(), D(), I()
        if self.g.gdk_event_get_scroll_deltas(event,C.byref(x),C.byref(y)):
            self.surface.send('scroll', x.value*60, y.value*60)
        elif self.g.gdk_event_get_scroll_direction(event,C.byref(direction)):
            self.surface.send('scroll', *{0:(0,-120),1:(0,120),2:(-120,0),3:(120,0)}.get(direction.value,(0,0)))
        return True

    # ==========================================
    # Function: Forward text composition, navigation keys and user-initiated clipboard paste.
    # Method: Use GTK's input-method context for text; keep password and pasted text out of app logs and files.
    # ==========================================
    def key(self, event):
        g, keyval, state = self.g, U(), U()
        if not g.gdk_event_get_keyval(event,C.byref(keyval)):
            return False
        g.gdk_event_get_state(event,C.byref(state))
        name = (g.gdk_keyval_name(keyval.value) or b'').decode()
        control, shift, alt = bool(state.value & 4), bool(state.value & 1), bool(state.value & 8)
        if control and name.lower() == 'v':
            clipboard = g.gtk_clipboard_get(g.gdk_atom_intern_static_string(b'CLIPBOARD'))
            text = g.gtk_clipboard_wait_for_text(clipboard)
            if text:
                try:
                    self.surface.send('text', C.cast(text,S).value.decode('utf-8'))
                finally:
                    g.g_free(text)
            return True
        if self.im and g.gtk_im_context_filter_keypress(self.im,event):
            return True
        key = KEYS.get(name,name)
        if control or alt or key in SPECIAL or (key.startswith('F') and key[1:].isdigit()):
            if key in {'Shift_L','Shift_R','Control_L','Control_R','Alt_L','Alt_R','Super_L','Super_R'}:
                return True
            modifiers = (['Control'] if control else []) + (['Alt'] if alt else []) + (['Shift'] if shift else [])
            self.surface.send('key', '+'.join([*modifiers,key]))
        else:
            char = g.gdk_keyval_to_unicode(keyval.value)
            if char >= 32:
                self.surface.send('text', chr(char))
        return True

    # ==========================================
    # Function: Render a newly received official login frame without temporary screenshot files.
    # Method: Fit the cropped content to the work area, synchronize pointer transforms and let GTK measure the matching window.
    # ==========================================
    def refresh(self):
        if self.closed:
            return
        frame = self.surface.take_frame()
        if frame is None:
            return
        data, host = frame[:2]
        g, loader, error = self.g, self.g.gdk_pixbuf_loader_new(), P()
        try:
            buffer = C.create_string_buffer(data)
            if not g.gdk_pixbuf_loader_write(loader,buffer,len(data),C.byref(error)):
                raise RuntimeError('无法显示官方登录画面，请关闭后重试')
            if not g.gdk_pixbuf_loader_close(loader,C.byref(error)):
                raise RuntimeError('官方登录画面未完整加载，请关闭后重试')
            pixbuf=g.gdk_pixbuf_loader_get_pixbuf(loader)
            source_w,source_h=g.gdk_pixbuf_get_width(pixbuf),g.gdk_pixbuf_get_height(pixbuf)
            scale=min(1,self.max_width/source_w,self.max_height/source_h)
            width,height=max(1,int(source_w*scale)),max(1,int(source_h*scale))
            scaled=g.gdk_pixbuf_scale_simple(pixbuf,width,height,2) if (width,height)!=(source_w,source_h) else None
            try:
                g.gtk_image_set_from_pixbuf(self.image,scaled or pixbuf)
            finally:
                if scaled:
                    g.g_object_unref(scaled)
            left,top,content_w,content_h=frame.clip if isinstance(frame,LoginFrame) else (0,0,source_w,source_h)
            self.view=(left,top,content_w/width,content_h/height)
            if self.display_size!=(width,height):
                self.display_size=(width,height)
                g.gtk_widget_set_size_request(self.events,width,height)
                g.gtk_window_resize(self.window,1,1)
            g.gtk_widget_set_sensitive(self.events,True)
            g.label(self.caption,'官方登录 · '+host)
        finally:
            if error:
                g.g_error_free(error)
            g.g_object_unref(loader)

    # ==========================================
    # Function: Close login normally after success or cooperatively cancel it on user dismissal.
    # Method: Signal only the shared login worker and destroy the parent-owned native popup.
    # ==========================================
    def close(self, cancel=False):
        if self.closed:
            return True
        if cancel:
            self.surface.close()
            self.app.controller.stop()
        self.dispose()
        self.g.gtk_widget_destroy(self.window)
        return True

    # ==========================================
    # Function: Dispose input methods and clear sensitive transient login state when the popup is destroyed.
    # Method: Detach input-method focus and client-window hooks before the native child widgets are destroyed.
    # ==========================================
    def dispose(self):
        if self.closed:
            return
        self.closed = True
        self.surface.close()
        self.g.g_signal_handler_disconnect(self.im,self.im_handler)
        self.g.gtk_im_context_focus_out(self.im)
        self.g.gtk_im_context_set_client_window(self.im,None)
        self.g.g_object_unref(self.im)
        self.im = None
