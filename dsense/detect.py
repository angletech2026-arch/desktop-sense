"""從 OCR 文字裡抓「錯誤訊息」。編輯器裡的程式碼常出現 Error 字樣，所以編輯器只用嚴格規則。"""
from __future__ import annotations

import re

_STRICT = [
    r"Traceback \(most recent call last\)",
    r"\bnpm ERR!",
    r"(?i:\berror\s*ts\s?\d{3,5}\b)",  # OCR 常把 TS2339 讀成 Ts2339
    r"(?i::\d+:\d+\s*-\s*error\b)",     # tsc／eslint 的「檔案:行:欄 - error」
    r"\bFound \d+ errors?\b",          # tsc 最後的總結行
    r"^Type error:",
    r"Failed to compile",
    r"\bBuild (?:failed|error)\b",
    r"Compilation failed|編譯失敗|建置失敗",
    r"Unhandled (?:Runtime Error|Rejection|exception)",
    r"^panic:",
    r"^fatal:",
    r"Segmentation fault",
    r"\bexit(?:ed)? with code [1-9]\d*",
    r"^Exit code:? [1-9]\d*",
    r"^[A-Za-z_.]*(?:Error|Exception): .{3,}",
    r"\bE(?:ADDRINUSE|NOENT|CONNREFUSED|ACCES|PERM|TIMEDOUT)\b",
    r"is not recognized as (?:an internal or external command|the name of a cmdlet)",
    r"無法辨識.{0,20}(?:cmdlet|命令|指令)",
    r"^error(?:\[E\d{4}\])?: ",
]
_EXTRA = [
    r"^(?:FAIL|FAILED)\b",
    r"\b\d+ (?:failed|failing)\b",
    r"Internal Server Error",
    r"Application error: a (?:client|server)-side exception",
    r"\b50[0234] (?:Internal|Bad Gateway|Service Unavailable|Gateway Timeout)",
    r"Access is denied|拒絕存取|Permission denied",
    r"發生錯誤|執行失敗|無法連線|連線逾時",
    r"^ERROR\b|\[ERROR\]",
    r"^[✖❌]\s*\S.{8,}",  # 不含「×」：瀏覽器每個分頁都有關閉鈕 ×
    r"CommandNotFoundException|ParserError|ParameterBindingException",
]

STRICT = [re.compile(p) for p in _STRICT]
FULL = STRICT + [re.compile(p) for p in _EXTRA]


def find_errors(text: str, app: str, editor_apps: set[str], limit: int = 8) -> list[str]:
    patterns = STRICT if (app or "").lower() in editor_apps else FULL
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if len(s) < 4:
            continue
        if any(p.search(s) for p in patterns):
            out.append(s[:240])
            if len(out) >= limit:
                break
    return out


_HEX = re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{8,}\b")
_NUM = re.compile(r"\d+")
_WS = re.compile(r"\s+")


def line_key(line: str) -> str:
    """去掉行號/位址/空白差異，判斷是不是「同一個錯誤」。"""
    s = _HEX.sub("h", line.lower())
    s = _NUM.sub("#", s)
    return _WS.sub(" ", s).strip()[:120]
