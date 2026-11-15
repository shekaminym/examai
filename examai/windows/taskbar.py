"""Remove/restore a Windows taskbar button without hiding the Qt window."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
from uuid import UUID


CLSID_TASKBAR_LIST = UUID("56fdf344-fd6d-11d0-958a-006097c9a090")
IID_TASKBAR_LIST = UUID("56fdf342-fd6d-11d0-958a-006097c9a090")
CLSCTX_INPROC_SERVER = 1
COINIT_APARTMENTTHREADED = 2
RPC_E_CHANGED_MODE = -2147417850
E_NOTIMPL = -2147467263


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


def _guid(value: UUID) -> GUID:
    return GUID.from_buffer_copy(value.bytes_le)


def _method(pointer, index, *argtypes):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    return prototype(table[index])


def set_taskbar_hidden(window, hidden: bool) -> bool:
    """Use the Shell's taskbar list; leave HWND, visibility and capture intact."""
    if sys.platform != "win32":
        return False
    hwnd = int(window.winId())
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    if not user32.IsWindow(hwnd):
        return False

    ole32 = ctypes.OleDLL("ole32")
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p,
                                       wintypes.DWORD, ctypes.POINTER(GUID),
                                       ctypes.POINTER(ctypes.c_void_p)]
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    initialized = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    if initialized not in (0, 1, RPC_E_CHANGED_MODE):
        return False
    instance = ctypes.c_void_p()
    try:
        if ole32.CoCreateInstance(ctypes.byref(_guid(CLSID_TASKBAR_LIST)), None,
                                  CLSCTX_INPROC_SERVER, ctypes.byref(_guid(IID_TASKBAR_LIST)),
                                  ctypes.byref(instance)) < 0:
            return False
        # Windows 10's Shell can return E_NOTIMPL here while DeleteTab and
        # AddTab still succeed. Check the actual requested method's HRESULT.
        if _method(instance, 3)(instance) not in (0, E_NOTIMPL):
            return False
        method = _method(instance, 5 if hidden else 4, wintypes.HWND)
        return method(instance, hwnd) >= 0  # DeleteTab or AddTab
    finally:
        if instance.value:
            _method(instance, 2)(instance)  # IUnknown::Release
        if initialized in (0, 1):
            ole32.CoUninitialize()
