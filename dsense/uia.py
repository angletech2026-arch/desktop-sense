"""找出瀏覽器的無痕／私密視窗。

Chrome（以及 Brave、Opera、Vivaldi 這些 Chromium 系）的無痕視窗，視窗標題跟一般視窗一模一樣
（實測：「Example Domain - Google Chrome」），光看標題擋不住。但它們的無障礙樹（UI Automation）裡
會有「…Google Chrome (無痕模式)」「無痕視窗」這類名稱。每個瀏覽器視窗第一次出現時查一次（約 30ms），
結果依視窗 handle 快取。

查不到（沒裝 comtypes、UIA 失敗）就回傳 None：呼叫端照原本的標題規則處理，不會因此當掉。
"""
from __future__ import annotations

import re
import time

# 無障礙名稱裡代表「這是無痕／私密視窗」的字（各語系 Chrome / Edge / Brave / Opera / Vivaldi / Firefox）
MARKERS = re.compile(
    r"\((?:Incognito|無痕模式|无痕模式|シークレット(?: モード)?|시크릿(?: 모드)?|Inkognito|incógnito|anonimo"
    r"|Navigation privée|InPrivate)\)"
    r"|^Incognito(?: window)?$|無痕視窗|无痕窗口|シークレット ウィンドウ|시크릿 창|\[InPrivate\]|Private Browsing",
    re.IGNORECASE)


class IncognitoProbe:
    def __init__(self, log=None, max_nodes: int = 400, max_depth: int = 10, ttl_s: float = 1800) -> None:
        self.log = log or (lambda msg: None)
        self.max_nodes, self.max_depth, self.ttl_s = max_nodes, max_depth, ttl_s
        self._cache: dict[int, tuple[float, bool]] = {}
        self._uia = None
        self._walker = None
        self._broken = False

    def _init(self) -> bool:
        if self._uia is not None:
            return True
        if self._broken:
            return False
        try:
            import comtypes.client
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation  # type: ignore
            self._uia = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
            self._walker = self._uia.ControlViewWalker
            return True
        except Exception as e:  # 沒裝 comtypes、COM 失敗：退回只看標題
            self._broken = True
            self.log(f"UI Automation unavailable; incognito detection falls back to window titles: {e!r}")
            return False

    def is_private(self, hwnd: int) -> bool | None:
        now = time.time()
        hit = self._cache.get(hwnd)
        if hit and now - hit[0] < self.ttl_s:
            return hit[1]
        if not self._init():
            return None
        try:
            found = self._scan(hwnd)
        except Exception as e:
            self.log(f"UI Automation scan failed: {e!r}")
            return None
        self._cache[hwnd] = (now, found)
        if len(self._cache) > 500:
            self._cache = {h: v for h, v in self._cache.items() if now - v[0] < self.ttl_s}
        return found

    def _scan(self, hwnd: int) -> bool:
        root = self._uia.ElementFromHandle(hwnd)
        queue = [(root, 0)]
        seen = 0
        while queue and seen < self.max_nodes:
            el, depth = queue.pop(0)
            seen += 1
            if MARKERS.search(el.CurrentName or ""):
                return True
            if depth >= self.max_depth:
                continue
            try:
                child = self._walker.GetFirstChildElement(el)
            except Exception:
                child = None
            while child:
                queue.append((child, depth + 1))
                try:
                    child = self._walker.GetNextSiblingElement(child)
                except Exception:
                    child = None
        return False
