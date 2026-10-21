"""Local opt-in activity sensing. Never captures window titles or screenshots."""
import ctypes
from ctypes import wintypes
from datetime import datetime
import sys
from .models import Environment


def perceive(enabled=False, focused=False):
    result = Environment(hour=datetime.now().hour, focused=focused)
    if not enabled or sys.platform != "win32":
        return result
    class LastInput(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]
    info = LastInput()
    info.cbSize = ctypes.sizeof(info)
    if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        tick = ctypes.windll.kernel32.GetTickCount()
        result.idle_seconds = ((tick - info.dwTime) & 0xFFFFFFFF) / 1000
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    hwnd = user32.GetForegroundWindow()
    rect = wintypes.RECT()
    if hwnd and user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        result.focused = focused or (rect.left <= 0 and rect.top <= 0 and
            rect.right >= user32.GetSystemMetrics(0) and rect.bottom >= user32.GetSystemMetrics(1))
    return result
