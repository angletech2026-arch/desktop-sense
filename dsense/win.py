"""Win32 (ctypes)：前景視窗、標題、視窗範圍、閒置時間、行程名稱。

最小權限：行程名稱用 Toolhelp32 快照取得，不開任何行程 handle；閒置時間只用 GetLastInputInfo，不讀按鍵內容。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    dwmapi = ctypes.WinDLL("dwmapi")
except OSError:  # pragma: no cover
    dwmapi = None


class RECT(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG), ("right", wt.LONG), ("bottom", wt.LONG)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wt.DWORD)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.IsIconic.argtypes = [wt.HWND]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(RECT)]
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.MonitorFromWindow.restype = wt.HMONITOR
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
user32.EnumChildWindows.argtypes = [wt.HWND, WNDENUMPROC, wt.LPARAM]
kernel32.GetTickCount.restype = wt.DWORD
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE
if dwmapi is not None:
    dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
TH32CS_SNAPPROCESS = 0x2
DWMWA_EXTENDED_FRAME_BOUNDS = 9
MONITOR_DEFAULTTONEAREST = 2
ERROR_ALREADY_EXISTS = 183


def set_dpi_aware() -> None:
    """座標一律用實體像素（多螢幕 + 縮放時截圖範圍才會對）。"""
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def snapshot_processes() -> dict[int, tuple[str, int]]:
    """pid -> (exe 名稱, 父 pid)。Toolhelp 快照不需要開任何行程 handle。"""
    out: dict[int, tuple[str, int]] = {}
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return out
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out[int(entry.th32ProcessID)] = (entry.szExeFile, int(entry.th32ParentProcessID))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return out


class ProcessNames:
    """pid -> exe 名稱快取；遇到沒看過的 pid 才重拍快照（最多每秒一次）。"""

    def __init__(self) -> None:
        self._map: dict[int, tuple[str, int]] = {}
        self._last = 0.0

    def name(self, pid: int) -> str | None:
        hit = self._map.get(pid)
        stale = time.monotonic() - self._last > 30.0  # PID 會被重用，快取不能永久有效
        if (hit is None and time.monotonic() - self._last > 1.0) or stale:
            self._map = snapshot_processes()
            self._last = time.monotonic()
            hit = self._map.get(pid)
        return hit[0] if hit else None

    def alive(self, pid: int) -> bool:
        return pid in snapshot_processes()


_names = ProcessNames()


def process_name(pid: int) -> str | None:
    return _names.name(pid)


def window_text(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _uwp_child_pid(hwnd, host_pid: int) -> int | None:
    """UWP App 的頂層視窗屬於 ApplicationFrameHost，真正的 App 在子視窗。"""
    found: list[int] = []

    @WNDENUMPROC
    def cb(child, _lp):
        pid = window_pid(child)
        if pid and pid != host_pid:
            found.append(pid)
            return False
        return True

    user32.EnumChildWindows(hwnd, cb, 0)
    return found[0] if found else None


def foreground() -> dict | None:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = window_pid(hwnd)
    app = process_name(pid) or "unknown"
    if app.lower() == "applicationframehost.exe":
        child = _uwp_child_pid(hwnd, pid)
        if child:
            pid = child
            app = process_name(child) or app
    return {
        "hwnd": int(hwnd),
        "pid": pid,
        "app": app,
        "title": window_text(hwnd),
        "cls": class_name(hwnd),
        "minimized": bool(user32.IsIconic(hwnd)),
    }


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """視窗實際可見範圍（去掉陰影），並裁到所在螢幕內。"""
    r = RECT()
    ok = False
    if dwmapi is not None:
        ok = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) == 0
    if not ok and not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    mon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if mon:
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
            m = mi.rcMonitor
            r.left, r.top = max(r.left, m.left), max(r.top, m.top)
            r.right, r.bottom = min(r.right, m.right), min(r.bottom, m.bottom)
    if r.right - r.left < 40 or r.bottom - r.top < 40:
        return None
    return (r.left, r.top, r.right, r.bottom)


def idle_seconds() -> float:
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0.0
    return ((kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF) / 1000.0


def acquire_mutex(name: str):
    """單一實例鎖。拿不到（已有別的在跑）回傳 None。handle 要一直持有到行程結束。"""
    h = kernel32.CreateMutexW(None, False, name)
    if not h:
        return None
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(h)
        return None
    return h


def pid_alive(pid: int, exe_hint: tuple[str, ...] = ("python.exe", "pythonw.exe")) -> bool:
    entry = snapshot_processes().get(int(pid))
    return bool(entry) and entry[0].lower() in exe_hint
