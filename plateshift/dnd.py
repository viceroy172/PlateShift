"""Accept files dropped from Explorer onto a Tk window (Windows only, no extra packages)."""

from __future__ import annotations

import sys

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4

_keep = []  # window procs must outlive the window


def enable(root, on_drop) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user32.GetParent.restype = wintypes.HWND
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.CallWindowProcW.restype = LRESULT
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell32.DragFinish.argtypes = [ctypes.c_void_p]
    shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]

    root.update_idletasks()
    hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
    old = None
    # tk can't be called from inside the window proc, so park the files and poll
    dropped: list[list[str]] = []

    def poll():
        while dropped:
            on_drop(dropped.pop(0))
        root.after(150, poll)

    def proc(h, msg, wparam, lparam):
        if msg == WM_DROPFILES:
            count = shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
            files = []
            for i in range(count):
                size = shell32.DragQueryFileW(wparam, i, None, 0) + 1
                buf = ctypes.create_unicode_buffer(size)
                shell32.DragQueryFileW(wparam, i, buf, size)
                files.append(buf.value)
            shell32.DragFinish(wparam)
            dropped.append(files)
            return 0
        return user32.CallWindowProcW(old, h, msg, wparam, lparam)

    callback = WNDPROC(proc)
    _keep.append(callback)
    old = user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, ctypes.cast(callback, ctypes.c_void_p))
    shell32.DragAcceptFiles(hwnd, True)
    poll()
    return True
