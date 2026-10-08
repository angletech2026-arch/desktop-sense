"""macOS：前景 App／視窗、視窗截圖、閒置時間、單一實例鎖、Chrome 系無痕偵測、通知。

需要「螢幕錄製」權限（系統設定 → 隱私權與安全性 → 螢幕錄製）。macOS 是把權限記在「負責的行程」上：
daemon 一律由 launchd（LaunchAgent）啟動，負責的就是 venv 的 python，授權給 Python 才有用。沒有權限時：
- 視窗標題拿不到 → 用 App 名稱代替（時間線照常，只是看不到是哪個分頁／檔案）
- 截圖拿不到 → 不存截圖（錯誤偵測與 AI 分析就沒有畫面可用）；`ds status` 會顯示權限狀態

App 一律用 bundle id 表示（例：com.google.Chrome），不會因為系統語言變成「終端機」「訊息」而對不上規則。
macOS 的框架（Quartz、AppKit、Vision、ScreenCaptureKit）只在真的用到時才 import：純邏輯在 Windows 上也能測試。
所有呼叫 Cocoa 的地方都包在 autorelease pool 裡（daemon 沒有主 run loop，不包會一直漏記憶體）。
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import threading
import time

from .config import DATA

_LOCK_FD: int | None = None


def _quartz():
    import Quartz  # pyobjc-framework-Quartz
    return Quartz


@contextlib.contextmanager
def _pool():
    try:
        import objc
    except ImportError:  # 不在 macOS：純邏輯測試用
        yield
        return
    with objc.autorelease_pool():
        yield


def set_dpi_aware() -> None:
    """Windows 才有的概念；macOS 的座標本來就一致。"""
    return None


def has_capture_permission() -> bool:
    try:
        return bool(_quartz().CGPreflightScreenCaptureAccess())
    except (ImportError, AttributeError):
        return False


def ensure_capture_permission(log=print) -> bool:
    """沒有螢幕錄製權限就請系統跳出授權視窗（系統只會跳一次；之後要使用者自己到系統設定打開）。"""
    if has_capture_permission():
        return True
    try:
        _quartz().CGRequestScreenCaptureAccess()
    except (ImportError, AttributeError):
        pass
    log("macOS screen recording permission is missing: titles fall back to app names and no screenshots are taken. "
        "Enable Python in System Settings > Privacy & Security > Screen Recording, then run `ds restart`.")
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


def _pump_run_loop() -> None:
    """NSWorkspace 的「最前面的 App」靠 run loop 上的通知更新；daemon 沒有主 run loop，每次問之前先轉一下。"""
    from Foundation import NSDate, NSRunLoop
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.01))


def foreground() -> dict | None:
    from AppKit import NSWorkspace  # pyobjc-framework-Cocoa
    q = _quartz()
    with _pool():
        _pump_run_loop()
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        pid = int(app.processIdentifier())
        bundle = str(app.bundleIdentifier() or app.localizedName() or "unknown")
        label = str(app.localizedName() or bundle)
        opts = q.kCGWindowListOptionOnScreenOnly | q.kCGWindowListExcludeDesktopElements
        win = pick_front_window(q.CGWindowListCopyWindowInfo(opts, q.kCGNullWindowID), pid)
        title = str(win.get("kCGWindowName") or "") if win else ""
        wid = int(win["kCGWindowNumber"]) if win else 0
    return {
        "hwnd": wid,
        "pid": pid,
        "app": bundle,
        "title": title or label,  # 沒有螢幕錄製權限時拿不到視窗標題：用 App 名稱
        "cls": "",
        "minimized": win is None,
    }


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    q = _quartz()
    with _pool():
        info = q.CGWindowListCopyWindowInfo(q.kCGWindowListOptionIncludingWindow, int(hwnd)) or []
        return bounds_to_rect(info[0].get("kCGWindowBounds")) if info else None


# ---------------- 截圖 ----------------
def cgimage_to_pil(ref):
    """CGImage → PIL（32-bit BGRA、byte order little 的視窗截圖格式；其他格式回傳 None）。"""
    from PIL import Image
    q = _quartz()
    w, h = int(q.CGImageGetWidth(ref)), int(q.CGImageGetHeight(ref))
    if w < 40 or h < 40 or int(q.CGImageGetBitsPerPixel(ref)) != 32:
        return None
    data = q.CGDataProviderCopyData(q.CGImageGetDataProvider(ref))
    img = Image.frombuffer("RGBA", (w, h), bytes(data), "raw", "BGRA", int(q.CGImageGetBytesPerRow(ref)), 1)
    return img.convert("RGB")


def _capture_cg(hwnd: int):
    q = _quartz()
    # 用最高解析度（Retina 2x）：OCR 比較準；存檔時 save_jpeg 會縮到 max_width
    ref = q.CGWindowListCreateImage(q.CGRectNull, q.kCGWindowListOptionIncludingWindow, int(hwnd),
                                    q.kCGWindowImageBoundsIgnoreFraming | q.kCGWindowImageBestResolution)
    return cgimage_to_pil(ref) if ref is not None else None


def _capture_sck(hwnd: int, timeout: float = 3.0):
    """macOS 14+ 的 ScreenCaptureKit（CGWindowListCreateImage 被淘汰時的備案）。任何問題都回傳 None。"""
    try:
        import ScreenCaptureKit as SCK  # pyobjc-framework-ScreenCaptureKit
    except ImportError:
        return None
    if not hasattr(SCK, "SCScreenshotManager"):
        return None
    got: dict = {}
    done = threading.Event()

    def on_content(content, err):
        try:
            win = next((w for w in (content.windows() if content else []) if int(w.windowID()) == int(hwnd)), None)
            if win is None:
                done.set()
                return
            flt = SCK.SCContentFilter.alloc().initWithDesktopIndependentWindow_(win)
            cfg = SCK.SCStreamConfiguration.alloc().init()
            frame = win.frame()
            cfg.setWidth_(int(frame.size.width * 2))
            cfg.setHeight_(int(frame.size.height * 2))

            def on_image(image, err2):
                got["img"] = image
                done.set()
            SCK.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(flt, cfg, on_image)
        except Exception:
            done.set()

    SCK.SCShareableContent.getShareableContentWithCompletionHandler_(on_content)
    if not done.wait(timeout) or got.get("img") is None:
        return None
    return cgimage_to_pil(got["img"])


def grab_window(hwnd: int, rect: tuple[int, int, int, int]):
    """只截這一個視窗（不含蓋在上面的別的視窗）。沒有螢幕錄製權限就不截。"""
    from .capture import is_blank
    if not hwnd or not has_capture_permission():
        return None
    with _pool():
        img = None
        try:
            img = _capture_cg(hwnd)
        except Exception:
            img = None
        if img is None:
            img = _capture_sck(hwnd)
    return None if img is None or is_blank(img) else img


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
    """問瀏覽器「最前面的視窗是不是無痕」。

    - 第一次會跳出 macOS 的「自動化」授權詢問：在背景執行緒等使用者回答（不會卡住 daemon、也不會把詢問視窗殺掉）
    - 答案還沒出來、被拒絕、或是根本沒辦法問（Safari、Arc）→ 回傳 None；呼叫端在 macOS 會把 None 當成「只記標題、不截圖」
    - 「是無痕」快取久一點；「不是」只快取 30 秒（AppleScript 的 front window 偶爾會跟目前視窗對不上）
    """

    def __init__(self, log=None, ttl_private_s: float = 1800, ttl_normal_s: float = 30, retry_fail_s: float = 60) -> None:
        self.log = log or (lambda msg: None)
        self.ttl_private_s, self.ttl_normal_s, self.retry_fail_s = ttl_private_s, ttl_normal_s, retry_fail_s
        self._cache: dict[int, tuple[float, bool | None]] = {}
        self._pending: set[int] = set()
        self._lock = threading.Lock()
        self._warned = False

    def is_private(self, hwnd: int, app: str = "") -> bool | None:
        name = CHROMIUM_APPS.get((app or "").lower())
        if not name:
            return None  # Safari、Arc 沒有辦法從外面問；Firefox 的私密視窗標題本身就有 Private Browsing
        now = time.time()
        with self._lock:
            hit = self._cache.get(hwnd)
            if hit is not None:
                age, val = now - hit[0], hit[1]
                ttl = self.ttl_private_s if val else (self.ttl_normal_s if val is False else self.retry_fail_s)
                if age < ttl:
                    return val
            if hwnd in self._pending:
                return None
            self._pending.add(hwnd)
        threading.Thread(target=self._ask, args=(hwnd, name), daemon=True).start()
        return None

    def _ask(self, hwnd: int, name: str) -> None:
        val: bool | None = None
        try:
            out = subprocess.run(["osascript", "-e", mode_script(name)], capture_output=True, text=True, timeout=120)
            if out.returncode == 0:
                val = out.stdout.strip().lower() == "incognito"
            elif not self._warned:
                self._warned = True
                self.log(f"incognito check for {name} failed (Automation permission denied?) - browser windows are "
                         f"title-only until it works: {out.stderr.strip()[:160]}")
        except (OSError, subprocess.SubprocessError) as e:
            self.log(f"incognito check failed: {e!r}")
        with self._lock:
            self._cache[hwnd] = (time.time(), val)
            self._pending.discard(hwnd)
            if len(self._cache) > 500:
                self._cache = dict(list(self._cache.items())[-250:])


# ---------------- 通知 ----------------
def applescript_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ") + '"'


def toast(title: str, body: str) -> None:
    script = f"display notification {applescript_quote(body[:400])} with title {applescript_quote(title[:120])}"
    try:
        subprocess.Popen(["osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass
