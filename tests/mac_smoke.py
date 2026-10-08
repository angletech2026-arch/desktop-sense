"""macOS 實機煙霧測試：真的呼叫 Apple 框架（CI 的 macOS 機器上跑，不在 unittest discover 範圍內）。

  python tests/mac_smoke.py

CI 機器沒有「螢幕錄製」權限：視窗標題應該退回 App 名稱、截圖應該安全地回傳 None（不能當掉）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from dsense import osapi  # noqa: E402
from dsense.ocr import Ocr  # noqa: E402

assert osapi.IS_MAC, "run this on macOS"
fails = 0


def check(name, ok, detail=""):
    global fails
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    fails += 0 if ok else 1


# 1. Apple Vision OCR on a synthetic image
img = Image.new("RGB", (1400, 220), "white")
draw = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)
except OSError:
    font = ImageFont.load_default()
draw.text((30, 70), "TypeError: Cannot read properties of undefined", fill="black", font=font)
lines = Ocr("").recognize(img)
check("vision ocr", any("TypeError" in ln for ln in lines), repr(lines)[:120])
lines_zh = Ocr("zh-Hant-TW").recognize(img)
check("vision ocr with language list", any("TypeError" in ln for ln in lines_zh), repr(lines_zh)[:120])

# 2. Frontmost app / window
fg = osapi.foreground()
check("foreground() returns a dict", fg is None or isinstance(fg, dict), repr(fg)[:160])
if fg:
    check("foreground app is a bundle id or name", bool(fg["app"]), fg["app"])
    check("title never empty (falls back to app name)", fg["minimized"] or bool(fg["title"]), fg["title"])

# 3. Idle time
idle = osapi.idle_seconds()
check("idle_seconds() >= 0", isinstance(idle, float) and idle >= 0, str(idle))

# 4. Capture without permission must not crash
from dsense import capture  # noqa: E402
from dsense.mac import has_capture_permission  # noqa: E402
perm = has_capture_permission()
print("screen recording permission:", perm)
if fg and fg["hwnd"]:
    rect = osapi.window_rect(fg["hwnd"])
    shot = capture.grab_window(fg["hwnd"], rect) if rect else None
    check("grab_window is safe", shot is None or shot.size[0] > 0, f"perm={perm} shot={'yes' if shot else 'no'}")

# 5. Single-instance lock
first = osapi.acquire_mutex("x")
check("first process takes the lock", first is not None)
import subprocess  # noqa: E402
code = ("import sys; sys.path.insert(0, %r); from dsense import osapi; "
        "print('second:', osapi.acquire_mutex('x') is None)" % str(Path(__file__).resolve().parent.parent))
out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout
check("second process can't take the lock", "second: True" in out, out.strip())

# 6. Notifications and incognito probe don't crash
from dsense.mac import IncognitoProbe, toast  # noqa: E402
toast("desktop-sense smoke test", "hello from CI")
check("incognito probe on non-browser returns None", IncognitoProbe().is_private(1, "com.apple.Terminal") is None)

# 7. CLI entry points
cli = subprocess.run([sys.executable, str(Path(__file__).resolve().parent.parent / "ds.py"), "--help"],
                     capture_output=True, text=True)
check("ds --help", cli.returncode == 0 and "ds" in cli.stdout, cli.stderr[-200:])

print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
