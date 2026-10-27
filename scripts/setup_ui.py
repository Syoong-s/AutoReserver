"""Small native first-run progress window; remains independent of browser dependencies."""

import queue
import threading


# ==========================================
# Function: Run local initialization with a responsive GTK progress/error window when available.
# Method: Keep dependency work on a worker thread and all native widget changes on the main thread.
# ==========================================
def with_progress(operation):
    try:
        from autoreserve.gtk_native import GtkNative, P
        g = GtkNative()
        if not g.gtk_init_check(None, None):
            raise RuntimeError('No graphical session')
    except (OSError, RuntimeError):
        return operation(print)
    events = queue.SimpleQueue()
    result = {}
    window = g.gtk_window_new(0)
    g.gtk_window_set_title(window, b'AutoReserve setup')
    g.gtk_window_set_default_size(window, 480, 180)
    g.gtk_window_set_resizable(window, False)
    header = g.gtk_header_bar_new()
    g.gtk_header_bar_set_title(header, b'Preparing AutoReserve')
    g.gtk_header_bar_set_show_close_button(header, False)
    g.gtk_window_set_titlebar(window, header)
    body = g.gtk_box_new(1, 16)
    g.gtk_container_set_border_width(body, 24)
    g.gtk_container_add(window, body)
    caption = g.gtk_label_new(b'Checking the local runtime...')
    g.gtk_label_set_line_wrap(caption, True)
    g.gtk_box_pack_start(body, caption, True, True, 0)
    spinner_new = g.libs['gtk'].gtk_spinner_new
    spinner_new.argtypes, spinner_new.restype = [], P
    spinner_start = g.libs['gtk'].gtk_spinner_start
    spinner_start.argtypes, spinner_start.restype = [P], None
    spinner = spinner_new()
    spinner_start(spinner)
    g.gtk_box_pack_start(body, spinner, False, True, 0)
    note = g.gtk_label_new(b'Dependencies stay in this application folder.\nThe first download can take several minutes.')
    g.gtk_label_set_line_wrap(note, True)
    g.gtk_box_pack_start(body, note, False, True, 0)
    shown = False

    # ==========================================
    # Function: Finish dependency work without touching GTK from the worker.
    # Method: Send stage strings and one terminal result through a thread-safe queue.
    # ==========================================
    def work():
        try:
            events.put(('result', operation(lambda message: events.put(('stage', message)))))
        except Exception as error:
            events.put(('error', error))

    # ==========================================
    # Function: Ignore closing while downloads are writing local files, and allow it after failure.
    # Method: Keep the first-run worker alive until it has produced a durable result.
    # ==========================================
    def close(*unused):
        if 'error' in result:
            result['dismissed'] = True
        return True

    g.connect(window, 'delete-event', close, count=3, returns=True)
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    while 'value' not in result and not result.get('dismissed'):
        while g.gtk_events_pending():
            g.gtk_main_iteration_do(False)
        try:
            kind, value = events.get(timeout=.025)
        except queue.Empty:
            continue
        if kind == 'stage':
            g.label(caption, value)
            if value != '检查运行环境…' and not shown:
                g.gtk_widget_show_all(window)
                g.gtk_window_present(window)
                shown = True
        elif kind == 'result':
            result['value'] = value
        else:
            result['error'] = value
            g.label(caption, 'Setup could not finish. Close this window and retry.\n\n' + str(value))
            if not shown:
                g.gtk_widget_show_all(window)
                g.gtk_window_present(window)
                shown = True
            g.gtk_widget_hide(spinner)
            g.gtk_header_bar_set_show_close_button(header, True)
            g.gtk_header_bar_set_decoration_layout(header, b':close')
    g.gtk_widget_destroy(window)
    if 'error' in result:
        raise result['error']
    return result['value']
