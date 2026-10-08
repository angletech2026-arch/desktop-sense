"""截圖 + 畫面變化偵測（共用部分）。實際抓視窗畫面的程式依平台在 capture_win.py / mac.py。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

THUMB_SIZE = (192, 108)
PIXEL_DELTA = 0.08  # 灰階差超過 8% 才算「這個像素變了」


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


def grab_window(hwnd: int, rect: tuple[int, int, int, int]) -> Image.Image | None:
    """只截目標視窗本身。失敗就回傳 None（不退回整個螢幕，免得拍到蓋在上面的別的視窗）。"""
    if sys.platform == "darwin":
        from .mac import grab_window as _grab
    else:
        from .capture_win import grab_window as _grab
    return _grab(hwnd, rect)
