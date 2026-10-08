"""讀 PSReadLine 終端機歷史，偵測「同一指令連續失敗般地重複」。只讀自己機器上的本機檔。"""
from __future__ import annotations

import re
from pathlib import Path

from .config import expand

_WS = re.compile(r"\s+")


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
            text = data.decode("utf-8", errors="replace")
            # PSReadLine 用反引號 ` 續行；合併成一條
            for raw in text.replace("`\n", " ").splitlines():
                c = _norm(raw)
                if c:
                    cmds.append(c)
        return cmds


def repeated(commands: list[str], threshold: int) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for c in commands:
        counts[_norm(c)] = counts.get(_norm(c), 0) + 1
    return [(c, n) for c, n in counts.items() if n >= threshold]
