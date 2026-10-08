"""讀終端機歷史（PowerShell PSReadLine、zsh、bash），偵測「同一指令連續失敗般地重複」。只讀自己機器上的本機檔。"""
from __future__ import annotations

import re
from pathlib import Path

from .config import expand

_WS = re.compile(r"\s+")
_ZSH_EXT = re.compile(r"^: \d+:\d+;")  # zsh EXTENDED_HISTORY：「: 1696000000:0;git push」
_BASH_TS = re.compile(r"^#\d{9,}$")    # bash HISTTIMEFORMAT 的時間戳行


def unmetafy_zsh(data: bytes) -> bytes:
    """zsh 存歷史時會把 0x83–0x9F 這些位元組「metafy」（前面加 0x83、本身 XOR 0x20）。
    不還原的話中文（UTF-8 多位元組）會變亂碼，「病歷」這類隱私規則就比對不到。"""
    out = bytearray()
    it = iter(data)
    for c in it:
        out.append((next(it, 0x20) ^ 0x20) if c == 0x83 else c)
    return bytes(out)


def parse_history(text: str) -> list[str]:
    """把歷史檔新增的內容拆成一條條指令（處理續行與 zsh 的時間戳前綴）。"""
    # PSReadLine 用反引號 ` 續行；zsh / bash 用反斜線
    text = text.replace("`\n", " ").replace("\\\n", " ")
    out = []
    for raw in text.splitlines():
        if _BASH_TS.match(raw.strip()):
            continue
        c = _norm(_ZSH_EXT.sub("", raw))
        if c:
            out.append(c)
    return out


def _norm(cmd: str) -> str:
    return _WS.sub(" ", cmd.strip())


class HistoryTail:
    """記住每個歷史檔上次讀到的位置與大小，只回傳新增的指令行。"""

    def __init__(self, files: list[str]) -> None:
        self.files = [Path(expand(f)) for f in files]
        self._pos: dict[Path, int] = {}
        for p in self.files:
            try:
                self._pos[p] = p.stat().st_size
            except OSError:
                self._pos[p] = 0

    def new_commands(self) -> list[str]:
        cmds: list[str] = []
        for p in self.files:
            try:
                size = p.stat().st_size
            except OSError:
                continue
            start = self._pos.get(p, 0)
            if size < start:  # 檔案被截斷/重建
                start = 0
            if size == start:
                continue
            try:
                with open(p, "rb") as f:
                    f.seek(start)
                    data = f.read()
                self._pos[p] = size
            except OSError:
                continue
            if "zsh" in p.name:
                data = unmetafy_zsh(data)
            cmds += parse_history(data.decode("utf-8", errors="replace"))
        return cmds


def repeated(commands: list[str], threshold: int) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for c in commands:
        counts[_norm(c)] = counts.get(_norm(c), 0) + 1
    return [(c, n) for c, n in counts.items() if n >= threshold]
