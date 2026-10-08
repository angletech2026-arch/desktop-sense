"""介面語言：L("中文", "English") 依設定/系統語言回傳其中一個。

語言來源（先到先用）：環境變數 DSENSE_LANG → config.json 的 "language" → Windows 介面語言。
值只有 "zh" / "en"；auto 時系統是中文介面就用 zh，其餘一律 en。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_CONFIG = Path(__file__).resolve().parent.parent / "config.json"


def _system_lang() -> str:
    import sys
    if sys.platform == "darwin":
        try:
            import subprocess
            out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True,
                                 timeout=3).stdout
            first = out.replace("(", " ").replace('"', " ").replace(",", " ").split()
            return "zh" if first and first[0].lower().startswith("zh") else "en"
        except Exception:
            pass
    try:
        import ctypes
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return "zh" if (lid & 0x3FF) == 0x04 else "en"  # 0x04 = LANG_CHINESE
    except Exception:
        import locale
        code = (locale.getlocale()[0] or "").lower()
        return "zh" if code.startswith(("zh", "chinese")) else "en"


def _detect() -> str:
    v = os.environ.get("DSENSE_LANG", "").strip().lower()
    if not v:
        try:
            v = str(json.loads(_CONFIG.read_text(encoding="utf-8")).get("language", "")).strip().lower()
        except (OSError, ValueError, AttributeError):
            v = ""
    if v.startswith("zh"):
        return "zh"
    if v.startswith("en"):
        return "en"
    return _system_lang()


LANG = _detect()


def L(zh: str, en: str) -> str:
    return zh if LANG == "zh" else en
