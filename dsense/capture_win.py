"""Windows 截圖：PrintWindow 只渲染目標視窗本身（通知、彈窗、置頂視窗不會入鏡）。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt

from PIL import Image, ImageGrab

from .capture import is_blank

PW_RENDERFULLCONTENT = 0x2

_user32 = ctypes.WinDLL("user32")
_gdi32 = ctypes.WinDLL("gdi32")
_user32.GetDC.argtypes = [wt.HWND]
_user32.GetDC.restype = wt.HDC
_user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
_user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
_user32.PrintWindow.restype = wt.BOOL
_user32.GetWindowRect.argtypes = [wt.HWND, ctypes.c_void_p]
_user32.IsHungAppWindow.argtypes = [wt.HWND]
_user32.IsHungAppWindow.restype = wt.BOOL
_gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
_gdi32.CreateCompatibleDC.restype = wt.HDC
_gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
_gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
_gdi32.SelectObject.restype = wt.HGDIOBJ
_gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
_gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wt.HDC]


class _RECT(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG), ("right", wt.LONG), ("bottom", wt.LONG)]


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


def grab(rect: tuple[int, int, int, int]) -> Image.Image | None:
    """螢幕區域截圖（會包含蓋在上面的其他視窗）。只當 grab_window 失敗時的備案。"""
    try:
        return ImageGrab.grab(bbox=rect, all_screens=True)
    except OSError:  # 鎖定畫面 / UAC 安全桌面時 BitBlt 會失敗
        return None


def _print_window(hwnd: int) -> tuple[Image.Image, tuple[int, int]] | None:
    wr = _RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(wr)):
        return None
    w, h = wr.right - wr.left, wr.bottom - wr.top
    if w <= 0 or h <= 0 or w * h > 80_000_000:
        return None
    screen_dc = _user32.GetDC(None)
    mem_dc = _gdi32.CreateCompatibleDC(screen_dc)
    bmp = _gdi32.CreateCompatibleBitmap(screen_dc, w, h)
    old = _gdi32.SelectObject(mem_dc, bmp)
    try:
        if not _user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT):
            return None
        bih = _BIH()
        bih.biSize = ctypes.sizeof(_BIH)
        bih.biWidth, bih.biHeight = w, -h  # 負高度 = 由上往下
        bih.biPlanes, bih.biBitCount = 1, 32
        buf = ctypes.create_string_buffer(w * h * 4)
        if not _gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bih), 0):
            return None
        return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1).copy(), (wr.left, wr.top)
    finally:
        _gdi32.SelectObject(mem_dc, old)
        _gdi32.DeleteObject(bmp)
        _gdi32.DeleteDC(mem_dc)
        _user32.ReleaseDC(None, screen_dc)


def grab_window(hwnd: int, rect: tuple[int, int, int, int]) -> Image.Image | None:
    """只渲染目標視窗本身（PrintWindow），通知、彈窗、置頂視窗不會入鏡。

    失敗（或畫出來是全黑）就不截，不退回螢幕區域截圖——那樣會把蓋在上面的別的視窗一起拍進去。
    沒回應的視窗直接跳過：對它 PrintWindow 會卡住。呼叫端仍應放在有逾時的執行緒裡跑。
    """
    if _user32.IsHungAppWindow(hwnd):
        return None
    try:
        got = _print_window(hwnd)
    except OSError:
        return None
    if got is None:
        return None
    img, (ox, oy) = got
    l, t, r, b = rect
    crop = img.crop((l - ox, t - oy, r - ox, b - oy))
    return None if is_blank(crop) else crop
