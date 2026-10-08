"""截圖 + 畫面變化偵測。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
from pathlib import Path

import numpy as np
from PIL import Image, ImageGrab

THUMB_SIZE = (192, 108)
PIXEL_DELTA = 0.08  # 灰階差超過 8% 才算「這個像素變了」
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


def thumb(img: Image.Image) -> np.ndarray:
    small = img.convert("L").resize(THUMB_SIZE, Image.BILINEAR)
    return np.asarray(small, dtype=np.float32) / 255.0


def changed_ratio(a: np.ndarray | None, b: np.ndarray | None) -> float:
    """兩張縮圖間有變化的像素比例（0~1）。"""
    if a is None or b is None or a.shape != b.shape:
        return 1.0
    return float(np.mean(np.abs(a - b) > PIXEL_DELTA))


def is_blank(img: Image.Image) -> bool:
    """全黑/全單色（例如全螢幕遊戲、被保護的影片）就不存。"""
    lo, hi = img.convert("L").resize((64, 36)).getextrema()
    return hi - lo < 6


def save_jpeg(img: Image.Image, path: Path, max_width: int, quality: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = img.convert("RGB")
    if out.width > max_width:
        out = out.resize((max_width, round(out.height * max_width / out.width)), Image.LANCZOS)
    out.save(path, "JPEG", quality=quality, optimize=True)
    return path
