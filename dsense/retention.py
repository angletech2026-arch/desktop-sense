"""清舊資料：截圖、事件、insight 的保留期與總容量上限，daemon.log 輪替。daemon 每小時呼叫一次。"""
from __future__ import annotations

import json
import os
import time

from .store import Store


def sweep(store: Store, cfg: dict) -> dict:
    r = cfg["retention"]
    now = time.time()
    removed = {"shots": 0, "events": 0, "bytes": 0}

    # 截圖：先一次列出（大小、時間），過期的刪、再從最舊刪到容量以下。單一檔案刪不掉就跳過，不中斷整輪。
    files = []
    for p in store.shots_dir.rglob("*.*"):
        try:
            st = p.stat()
            files.append((st.st_mtime, st.st_size, p))
        except OSError:
            continue
    files.sort()
    total = sum(sz for _, sz, _ in files)
    limit = r["max_shots_mb"] * 1024 * 1024
    cutoff = now - r["shots_hours"] * 3600
    for mt, sz, p in files:
        if mt >= cutoff and total <= limit:
            break
        try:
            p.unlink()
        except OSError:
            continue
        total -= sz
        removed["shots"] += 1
        removed["bytes"] += sz
    for d in sorted((p for p in store.shots_dir.glob("*") if p.is_dir()), reverse=True):
        try:
            d.rmdir()  # 只有空資料夾會成功
        except OSError:
            pass

    ev_cut = now - r["events_days"] * 86400
    for p in store.events_dir.glob("*.jsonl"):
        try:
            day = time.mktime(time.strptime(p.stem, "%Y-%m-%d"))
        except ValueError:
            continue
        if day < ev_cut - 86400:
            try:
                p.unlink()
                removed["events"] += 1
            except OSError:
                pass

    # insights.jsonl / research.jsonl：只有真的有過期資料才重寫
    ins_cut = now - r["insights_days"] * 86400
    for path in (store.insights_path, store.research_path):
        _prune_jsonl(path, ins_cut)

    # daemon.log 超過 2MB 只留後 1MB
    try:
        if store.log_path.exists() and store.log_path.stat().st_size > 2 * 1024 * 1024:
            data = store.log_path.read_bytes()[-1024 * 1024:]
            cut = data.find(b"\n") + 1  # 從第一個完整行開始
            store.log_path.write_bytes(data[cut:])
    except OSError:
        pass
    return removed


def _prune_jsonl(path, cut: float) -> None:
    if path.exists():
        keep, dropped = [], 0
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    dropped += 1
                    continue
                if isinstance(row, dict) and row.get("ts", 0) >= cut:
                    keep.append(line)
                else:
                    dropped += 1
        if dropped:
            tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp")
            tmp.write_text("".join(keep), encoding="utf-8")
            os.replace(tmp, path)
