"""隱私規則：視窗分級、敏感畫面判斷、遮蔽、文字清理。

分級（classify）：
  blocked  只留 App 名稱
  self     Claude 自己的視窗：留標題，不截圖
  nocap    只留標題，不截圖（遊戲、聊天）
  ok       標題 + 截圖 + OCR（OCR 再過一次敏感判斷與遮蔽）
"""
from __future__ import annotations

import re
import unicodedata

from .i18n import L

_LEADING_MARKS = re.compile(r"^(?:[⠀-⣿✳✻✽✶✢·◐◓◑◒◴◵◶◷⏺●○•]+\s*)+")
_NOTIFY_COUNT = re.compile(r"^\(\d+\+?\)\s*")


def normalize_title(title: str) -> str:
    """去掉會跳動的前綴（轉圈符號、未存檔 ●、通知數 (3)），同一視窗才不會被當成一直切換。"""
    t = (title or "").strip()
    for _ in range(3):
        t2 = _NOTIFY_COUNT.sub("", _LEADING_MARKS.sub("", t))
        if t2 == t:
            break
        t = t2
    return t.strip()


_URL = re.compile(r"https?://([^\s/]+)\S*", re.IGNORECASE)
_STRIP = re.compile(r"[`<>|{}\\]")
_WS = re.compile(r"\s+")
# 可以執行或讀本機檔的連結形式：中和掉，免得被當成可點的連結
_SCHEME = re.compile(r"\b(?:javascript|vbscript|file):|\bdata:(?=[\w.+-]+/[\w.+-]+[;,])", re.IGNORECASE)


def visible(text: str) -> str:
    """去掉看不見的字元：控制字元、格式字元（零寬、雙向覆寫、Unicode tag 區 U+E0000–E007F）、私用區、未指派碼位。
    這些字可以把「肉眼看不到的指令」藏進網頁標題，再被當成文字送進 AI。"""
    return "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf", "Co", "Cn"))


def sanitize(text: str, limit: int = 160) -> str:
    """把螢幕來的字變成單行純文字：網址只留網域、去掉反引號/角括號/管線等符號、截斷長度。"""
    s = visible(text or "")
    s = _URL.sub(lambda m: L(f"[網址:{m.group(1)}]", f"[url:{m.group(1)}]"), s)
    s = _SCHEME.sub(L("[連結]:", "[link]:"), s)
    s = _STRIP.sub("", s)
    s = _WS.sub(" ", s).strip()
    return s[:limit]


class Privacy:
    def __init__(self, cfg: dict) -> None:
        p = cfg["privacy"]
        self.errors: list[str] = []
        self.blocked_apps = self._names(p.get("blocked_apps", []))
        self.nocap_apps = self._names(p.get("no_capture_apps", []))
        self.self_apps = self._names(p.get("self_apps", []))
        self.blocked_title = self._compile(p.get("blocked_title_regex", []))
        self.nocap_title = self._compile(p.get("no_capture_title_regex", []))
        self.self_title = self._compile(p.get("self_title_regex", []))
        self.sensitive_text = self._compile(p.get("sensitive_text_regex", []))
        self.keywords = sorted(self._names(p.get("sensitive_keywords", [])))
        self.keyword_min = int(p.get("sensitive_keyword_min", 2))
        self.blocked_path = self._compile(p.get("blocked_path_regex", []))
        self.terminal_apps = self._names(cfg.get("apps", {}).get("terminal", []))
        self.redactors: list[tuple[re.Pattern, str, bool]] = []
        for r in p.get("redact", []):
            try:
                repl = r.get("repl", L("[遮蔽]", "[redacted]"))
                if not isinstance(repl, str):
                    raise TypeError("repl must be a string")
                self.redactors.append((re.compile(r["pattern"]), repl, bool(r.get("private"))))
            except (re.error, KeyError, TypeError, AttributeError) as e:
                self.errors.append(L(f"redact 規則無效：{r!r}（{e}）", f"invalid redact rule: {r!r} ({e})"))

    def _names(self, items) -> set[str]:
        out = set()
        for a in items if isinstance(items, list) else []:
            if isinstance(a, str):
                out.add(a.lower())
            else:
                self.errors.append(L(f"清單項目必須是文字：{a!r}", f"list items must be strings: {a!r}"))
        return out

    def _compile(self, patterns: list[str]) -> list[re.Pattern]:
        out = []
        for pat in patterns if isinstance(patterns, list) else []:
            try:
                out.append(re.compile(pat, re.IGNORECASE))
            except (re.error, TypeError) as e:
                self.errors.append(L(f"規則無效：{pat!r}（{e}）", f"invalid rule: {pat!r} ({e})"))
        return out

    def classify(self, app: str, raw_title: str) -> str:
        a = (app or "").lower()
        t = raw_title or ""
        if a in self.blocked_apps or any(r.search(t) for r in self.blocked_title):
            return "blocked"
        if a in self.self_apps:
            return "self"
        # 轉圈符號前綴只在終端機視窗才算 Claude Code
        if a in self.terminal_apps and any(r.search(t) for r in self.self_title):
            return "self"
        if a in self.nocap_apps or any(r.search(t) for r in self.nocap_title):
            return "nocap"
        return "ok"

    def text_sensitive(self, text: str) -> tuple[bool, str]:
        for r in self.sensitive_text:
            if r.search(text):
                return True, "rule"
        if self.keyword_min > 0:
            low = text.lower()
            hits = {k for k in self.keywords if k in low}
            if len(hits) >= self.keyword_min:
                return True, "keywords"
        for rx, _repl, private in self.redactors:
            if private and rx.search(text):
                return True, "secret"
        return False, ""

    def reclean(self, text: str) -> str | None:
        """讀取舊紀錄時用「現行」規則再過一次：命中遮蔽規則 → None（整段不要）；否則套用遮蔽。"""
        if not text:
            return text
        if self.text_blocked(text):
            return None
        return self.redact(text)

    def redact(self, text: str) -> str:
        for rx, repl, _private in self.redactors:
            text = rx.sub(repl, text)
        return text

    def text_blocked(self, text: str) -> bool:
        """指令、檔案路徑：命中遮蔽標題規則或路徑規則就整筆不記。"""
        return any(r.search(text) for r in self.blocked_title) or any(r.search(text) for r in self.blocked_path)

    def path_blocked(self, path: str) -> bool:
        return self.text_blocked(path)
