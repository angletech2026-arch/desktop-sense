"""輕量檔案變更偵測：掃設定的 roots，用 mtime 找最近被改的原始碼檔。不裝 watchdog，純輪詢（背景執行緒）。"""
from __future__ import annotations

import os
import time
from pathlib import Path

CODE_EXT = {".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".cs", ".rs", ".c", ".h", ".cpp", ".lua",
            ".sql", ".json", ".md", ".css", ".html", ".sh", ".ps1", ".toml", ".yaml", ".yml"}


class FileWatch:
    def __init__(self, cfg: dict, privacy, log=print) -> None:
        fw = cfg["file_watch"]
        self.roots = [Path(r) for r in fw["roots"]]
        self.exclude = {d.lower() for d in fw["exclude_dirs"]}
        self.debounce = fw["debounce_s"]
        self.max_per_scan = max(1, int(fw["max_per_minute"]))
        self.privacy = privacy
        self.log = log
        self._seen: dict[str, float] = {}
        self._baseline = time.time()  # 啟動前就改過的檔不算

    def _walk(self, root: Path):
        stack = [str(root)]
        while stack:
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        name = e.name
                        try:
                            if e.is_dir(follow_symlinks=False):
                                if name.lower() not in self.exclude and not name.startswith("."):
                                    stack.append(e.path)
                            elif os.path.splitext(name)[1].lower() in CODE_EXT:
                                yield e.path, e.stat(follow_symlinks=False).st_mtime
                        except OSError:
                            continue
            except OSError:
                continue

    def poll(self) -> list[dict]:
        now = time.time()
        hits: list[dict] = []
        for root in self.roots:
            if len(hits) >= self.max_per_scan or not root.exists():
                continue
            for full, mt in self._walk(root):
                if mt <= self._baseline or mt <= self._seen.get(full, 0) + self.debounce:
                    continue
                self._seen[full] = mt
                if self.privacy.path_blocked(full):
                    continue
                hits.append({"path": self._rel(full, root), "ts": round(mt, 3)})
                if len(hits) >= self.max_per_scan:  # 一次大量變更（npm install、git checkout）只報前面幾筆
                    break
        if len(self._seen) > 20000:
            cutoff = now - 3600
            self._seen = {k: v for k, v in self._seen.items() if v > cutoff}
            self._baseline = max(self._baseline, cutoff)  # 被丟掉的舊紀錄不能讓它們再被當成「新改的」
        return hits

    @staticmethod
    def _rel(full: str, root: Path) -> str:
        try:
            return f"{root.name}/{os.path.relpath(full, root)}".replace("\\", "/")
        except ValueError:
            return full.replace("\\", "/")
