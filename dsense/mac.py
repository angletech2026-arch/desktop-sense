"""macOS：前景 App／視窗、視窗截圖、閒置時間、單一實例鎖、Chrome 系無痕偵測。

需要「螢幕錄製」權限（系統設定 → 隱私權與安全性 → 螢幕錄製）。沒有權限時：
- 視窗標題拿不到 → 用 App 名稱代替（時間線照常，只是看不到是哪個分頁／檔案）
- 截圖拿不到 → 不存截圖（錯誤偵測與 AI 分析就沒有畫面可用）

App 一律用 bundle id 表示（例：com.google.Chrome），不會因為系統語言變成「終端機」「訊息」而對不上規則。
macOS 的框架（Quartz、AppKit、Vision）只在真的用到時才 import：純邏輯在 Windows 上也能測試。
"""
from __future__ import annotations

import os
import subprocess
import time

from PIL import Image

from .config import DATA

_LOCK_FD: int | None = None


def _quartz():
    import Quartz  # pyobjc-framework-Quartz
    return Quartz


def set_dpi_aware() -> None:
    """Windows 才有的概念；macOS 的座標本來就一致。"""
    return None


def has_capture_permission() -> bool:
    try:
        return bool(_quartz().CGPreflightScreenCaptureAccess())
    except (ImportError, AttributeError):
        return False


def ensure_capture_permission(log=print) -> bool:
    """沒有螢幕錄製權限就請系統跳出授權視窗（只會跳一次；之後要使用者自己到系統設定打開）。"""
    if has_capture_permission():
        return True
    try:
        _quartz().CGRequestScreenCaptureAccess()
    except (ImportError, AttributeError):
        pass
    log("macOS screen recording permission is missing: titles fall back to app names and no screenshots are taken. "
        "Enable it in System Settings > Privacy & Security > Screen Recording, then run `ds restart`.")
    return False


# ---------------- 前景視窗 ----------------
def pick_front_window(windows, pid: int):
    """CGWindowList 依前後順序排列：取這個 App 最上面那個一般（layer 0）、夠大的視窗。"""
    for w in windows or []:
        try:
            if int(w.get("kCGWindowOwnerPID", -1)) != int(pid) or int(w.get("kCGWindowLayer", 1)) != 0:
                continue
            b = w.get("kCGWindowBounds") or {}
            if float(b.get("Width", 0)) < 40 or float(b.get("Height", 0)) < 40:
                continue
            return w
        except (TypeError, ValueError):
            continue
    return None


def bounds_to_rect(b) -> tuple[int, int, int, int] | None:
    try:
        x, y, w, h = (float(b.get(k, 0)) for k in ("X", "Y", "Width", "Height"))
    except (TypeError, ValueError, AttributeError):
        return None
    if w < 40 or h < 40:
        return None
    return int(x), int(y), int(x + w), int(y + h)


def foreground() -> dict | None:
    from AppKit import NSWorkspace  # pyobjc-framework-Cocoa
    q = _quartz()
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    pid = int(app.processIdentifier())
    bundle = str(app.bundleIdentifier() or app.localizedName() or "unknown")
    label = str(app.localizedName() or bundle)
    opts = q.kCGWindowListOptionOnScreenOnly | q.kCGWindowListExcludeDesktopElements
    win = pick_front_window(q.CGWindowListCopyWindowInfo(opts, q.kCGNullWindowID), pid)
    title = str(win.get("kCGWindowName") or "") if win else ""
    return {
        "hwnd": int(win["kCGWindowNumber"]) if win else 0,
        "pid": pid,
        "app": bundle,
        "title": title or label,  # 沒有螢幕錄製權限時拿不到視窗標題：用 App 名稱
        "cls": "",
        "minimized": win is None,
    }


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    q = _quartz()
    info = q.CGWindowListCopyWindowInfo(q.kCGWindowListOptionIncludingWindow, int(hwnd)) or []
    return bounds_to_rect(info[0].get("kCGWindowBounds")) if info else None


def grab_window(hwnd: int, rect: tuple[int, int, int, int]) -> Image.Image | None:
    """只截這一個視窗（不含蓋在上面的別的視窗）。沒有螢幕錄製權限就不截。"""
    from .capture import is_blank
    if not has_capture_permission():
        return None
    q = _quartz()
    ref = q.CGWindowListCreateImage(q.CGRectNull, q.kCGWindowListOptionIncludingWindow, int(hwnd),
                                    q.kCGWindowImageBoundsIgnoreFraming | q.kCGWindowImageNominalResolution)
    if ref is None:
        return None
    w, h = int(q.CGImageGetWidth(ref)), int(q.CGImageGetHeight(ref))
    if w < 40 or h < 40:
        return None
    data = q.CGDataProviderCopyData(q.CGImageGetDataProvider(ref))
    img = Image.frombuffer("RGBA", (w, h), bytes(data), "raw", "BGRA", int(q.CGImageGetBytesPerRow(ref)), 1)
    img = img.convert("RGB")
    return None if is_blank(img) else img


def idle_seconds() -> float:
    q = _quartz()
    any_event = getattr(q, "kCGAnyInputEventType", 0xFFFFFFFF)
    return float(q.CGEventSourceSecondsSinceLastEventType(q.kCGEventSourceStateHIDSystemState, any_event))


# ---------------- 行程 ----------------
def acquire_mutex(name: str):
    """單一實例鎖（檔案鎖）。拿不到（已有別的在跑）回傳 None。fd 要一直持有到行程結束。"""
    import fcntl
    global _LOCK_FD
    path = DATA / "daemon.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    _LOCK_FD = fd
    return fd


def pid_alive(pid: int, exe_hint: tuple[str, ...] = ("python",)) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False
    try:  # pid 會被重用：確認真的是 python
        comm = subprocess.run(["ps", "-p", str(int(pid)), "-o", "comm="], capture_output=True, text=True,
                              timeout=3).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return True
    return any(h in comm for h in exe_hint)


# ---------------- 無痕視窗 ----------------
# Chromium 系瀏覽器的 AppleScript 有 window 的 mode 屬性（"normal" / "incognito"）
CHROMIUM_APPS = {
    "com.google.chrome": "Google Chrome", "com.google.chrome.beta": "Google Chrome Beta",
    "com.google.chrome.canary": "Google Chrome Canary", "com.brave.browser": "Brave Browser",
    "com.microsoft.edgemac": "Microsoft Edge", "com.vivaldi.vivaldi": "Vivaldi",
    "com.operasoftware.opera": "Opera",
}


def mode_script(app_name: str) -> str:
    return f'tell application "{app_name}" to get mode of front window'


class IncognitoProbe:
    """問瀏覽器「最前面的視窗是不是無痕」。第一次會跳出 macOS 的「自動化」授權詢問；拒絕就退回只看標題。"""

    def __init__(self, log=None, ttl_s: float = 1800) -> None:
        self.log = log or (lambda msg: None)
        self.ttl_s = ttl_s
        self._cache: dict[int, tuple[float, bool]] = {}
        self._warned = False

    def is_private(self, hwnd: int, app: str = "") -> bool | None:
        name = CHROMIUM_APPS.get((app or "").lower())
        if not name:
            return None  # Safari 沒有辦法從外面問；Firefox 的私密視窗標題本身就有 Private Browsing
        now = time.time()
        hit = self._cache.get(hwnd)
        if hit and now - hit[0] < self.ttl_s:
            return hit[1]
        try:
            out = subprocess.run(["osascript", "-e", mode_script(name)], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as e:
            self.log(f"incognito check failed: {e!r}")
            return None
        if out.returncode != 0:
            if not self._warned:
                self._warned = True
                self.log(f"incognito check for {name} failed (Automation permission?): {out.stderr.strip()[:160]}")
            return None
        private = out.stdout.strip().lower() == "incognito"
        self._cache[hwnd] = (now, private)
        return private


# ---------------- 通知 ----------------
def applescript_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ") + '"'


def toast(title: str, body: str) -> None:
    script = f"display notification {applescript_quote(body[:400])} with title {applescript_quote(title[:120])}"
    try:
        subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass
