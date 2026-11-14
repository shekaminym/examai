"""Windows user-bound data protection based on DPAPI."""
import base64
import ctypes as c
import sys


class Blob(c.Structure):
    _fields_ = [("size", c.c_uint32), ("data", c.POINTER(c.c_ubyte))]


def transform(data, decrypt=False):
    if sys.platform != "win32":
        raise OSError("Windows DPAPI is required")
    source = (c.c_ubyte * len(data)).from_buffer_copy(data)
    result = Blob()
    blob = Blob(len(data), source)
    api = c.windll.crypt32
    method = api.CryptUnprotectData if decrypt else api.CryptProtectData
    if not method(c.byref(blob), None, None, None, None, 1, c.byref(result)):
        raise OSError("DPAPI operation failed")
    try:
        return c.string_at(result.data, result.size)
    finally:
        c.windll.kernel32.LocalFree(c.cast(result.data, c.c_void_p))


def protect(text):
    return base64.b64encode(transform(text.encode())).decode() if text else ""


def reveal(text):
    return transform(base64.b64decode(text, validate=True), decrypt=True).decode() if text else ""
