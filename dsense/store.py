"""本機資料存放：data/events/YYYY-MM-DD.jsonl、shots/、insights.jsonl、state.json、control.json。"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from .config import DATA


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def hm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def hms(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def dur(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h{m % 60:02d}m"


def _replace_retry(tmp: Path, dst: Path) -> None:
    # 別的行程剛好在讀 dst 時，Windows 會擋 os.replace，稍等重試
    for i in range(10):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            time.sleep(0.02 * (i + 1))
    os.replace(tmp, dst)


class Store:
    def __init__(self, root: Path = DATA) -> None:
        self.root = Path(root)
        self.events_dir = self.root / "events"
        self.shots_dir = self.root / "shots"
        self.reports_dir = self.root / "reports"
        for d in (self.events_dir, self.shots_dir, self.reports_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.control_path = self.root / "control.json"
        self.insights_path = self.root / "insights.jsonl"
        self.research_path = self.root / "research.jsonl"
        self.hook_sessions_path = self.root / "hook_sessions.json"
        self.pid_path = self.root / "daemon.pid"
        self.log_path = self.root / "daemon.log"
        self._lock = threading.Lock()

    # ---------- events ----------
    def events_file(self, ts: float | None = None) -> Path:
        day = time.strftime("%Y-%m-%d", time.localtime(ts or time.time()))
        return self.events_dir / f"{day}.jsonl"

    def append_event(self, ev: dict) -> dict:
        ts = ev.setdefault("ts", round(time.time(), 3))
        ev.setdefault("t", iso(ts))
        line = json.dumps(ev, ensure_ascii=False) + "\n"
        with self._lock:
            with open(self.events_file(ts), "a", encoding="utf-8") as f:
                f.write(line)
        return ev

    def read_events(self, since_ts: float, until_ts: float | None = None) -> list[dict]:
        until_ts = until_ts or time.time()
        out: list[dict] = []
        day = datetime.fromtimestamp(since_ts).date()
        last = datetime.fromtimestamp(until_ts).date()
        while day <= last:
            path = self.events_dir / f"{day.isoformat()}.jsonl"
            if path.exists():
                with open(path, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        try:
                            ev = json.loads(line)
                        except ValueError:
                            continue
                        if since_ts <= ev.get("ts", 0) <= until_ts:
                            out.append(ev)
            day += timedelta(days=1)
        out.sort(key=lambda e: e.get("ts", 0))
        return out

    def read_window(self, start_ts: float, end_ts: float | None = None, lookback_s: float = 6 * 3600) -> list[dict]:
        """讀 [start - lookback, end] 的事件：多讀一段，才知道 start 那一刻停在哪個視窗。"""
        return self.read_events(start_ts - lookback_s, end_ts)

    # ---------- insights ----------
    def append_insight(self, ins: dict) -> None:
        self._append_jsonl(self.insights_path, ins)

    def read_insights(self, limit: int = 20, since_ts: float = 0) -> list[dict]:
        return self._read_jsonl(self.insights_path, limit, since_ts)

    # ---------- research（自動搜尋 GitHub / Reddit 的結果） ----------
    def append_research(self, res: dict) -> None:
        self._append_jsonl(self.research_path, res)

    def read_research(self, limit: int = 20, since_ts: float = 0) -> list[dict]:
        return self._read_jsonl(self.research_path, limit, since_ts)

    def _append_jsonl(self, path: Path, obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        with self._lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)

    def _read_jsonl(self, path: Path, limit: int, since_ts: float) -> list[dict]:
        if not path.exists():
            return []
        size = path.stat().st_size
        chunk = 512 * 1024
        with open(path, "rb") as f:
            if size > chunk and since_ts == 0:
                f.seek(size - chunk)
                f.readline()  # 丟掉被切一半的那行
            data = f.read().decode("utf-8", errors="replace")
        out = []
        for line in data.splitlines():
            try:
                ins = json.loads(line)
            except ValueError:
                continue
            if ins.get("ts", 0) >= since_ts:
                out.append(ins)
        return out[-limit:] if limit else out

    # ---------- json files ----------
    def write_json(self, path: Path, obj: dict) -> None:
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        _replace_retry(tmp, path)

    @staticmethod
    def read_json(path: Path) -> dict | None:
        for _ in range(3):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            except (OSError, ValueError):
                time.sleep(0.02)
        return None

    def read_state(self) -> dict | None:
        return self.read_json(self.state_path)

    def read_control(self) -> dict:
        return self.read_json(self.control_path) or {}

    def update_control(self, **changes) -> dict:
        ctl = self.read_control()
        ctl.update(changes)
        self.write_json(self.control_path, ctl)
        return ctl

    def shot_abspath(self, rel: str) -> Path:
        return self.root / rel
