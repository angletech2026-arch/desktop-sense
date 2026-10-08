"""主迴圈：輪詢前景視窗 → 分段、依隱私分級擷取畫面 + OCR + 找錯誤 → 寫事件 / state.json，必要時叫分析器。

全程本機。只有「ok 等級、且 OCR 未命中敏感規則」的畫面才會送進分析器（= Claude API）。
"""
from __future__ import annotations

import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
import time
import traceback

from . import capture
from .analyzer import Analyzer
from .config import load
from .detect import find_errors, line_key
from .digest import build_prompt, scrub_events, segments
from .filewatch import FileWatch
from .history import HistoryTail, repeated
from .i18n import L
from .notify import toast
from .ocr import Ocr
from .privacy import Privacy, normalize_title
from .research import build_research_prompt
from .osapi import (IS_MAC, IncognitoProbe, acquire_mutex, ensure_capture_permission, foreground, idle_seconds,
                    set_dpi_aware, window_rect)
from .store import Store

MUTEX = "Local\\desktop_sense_daemon_v1"
MASKED_CMD = L("[隱私遮蔽的指令]", "[private command hidden]")


class Daemon:
    def __init__(self) -> None:
        self.cfg = load()
        self.store = Store()
        self.privacy = Privacy(self.cfg)
        self.log_file = open(self.store.log_path, "a", encoding="utf-8", buffering=1)
        self.ocr: Ocr | None = None
        self.analyzer = Analyzer(self.cfg, self.store, self.log)

        self.cur: dict | None = None          # 目前區段 {app,title,raw,kind,since,hwnd,pid}
        self.segs: list[dict] = []             # 已結束的近期區段（給 state.json）
        self.idle = False
        self.idle_since = 0.0
        self.last_screen: dict | None = None   # 最近一次「ok」截圖資訊（state 用）
        self.last_alert: dict | None = None
        self.last_insight: dict | None = None

        self._last_thumb = None
        self._last_cap_check = 0.0
        self._last_cap_ts = 0.0
        self._last_minor_ts = 0.0
        self._dwell_shot_done = False
        self._seen_errs: dict[str, float] = {}
        self._last_err_call = 0.0
        self._last_periodic = time.time()
        # 重啟後沿用最近一小時的呼叫次數，避免重啟就把每小時上限歸零
        self._call_times: list[float] = [r.get("ts", 0) for r in self.store.read_insights(0, since_ts=time.time() - 3600)]
        recent = self.store.read_insights(1)
        self.last_insight = recent[-1] if recent else None
        self._last_hist = 0.0
        self._file_hits: "queue.Queue[dict]" = queue.Queue()
        self._sensitive_ctx: set[tuple[str, str]] = set()
        self._paused_until = 0.0
        self._recent_cmds: list[tuple[float, str]] = []
        self._was_paused = False
        # PrintWindow 對沒回應的視窗會卡住 → 放到單獨執行緒、主迴圈只等有限時間
        self._cap_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='capture')
        self._cap_future = None
        self._repeat_alerted: dict[str, float] = {}
        # 自動搜尋：錯誤出現的時間點、各錯誤/主題上次搜的時間、今天搜了幾次
        self._err_hits: dict[str, list[float]] = {}
        self._win_err_counts: dict[tuple, dict[str, int]] = {}  # 各視窗上一張截圖裡，每個錯誤出現幾次
        self._incognito = IncognitoProbe(self.log)
        self._browsers = {a.lower() for a in self.cfg["apps"]["browser"]}
        self._research_pending: set[str] = set()
        self._researched: dict[str, float] = {}
        day_start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        for r in self.store.read_research(0, since_ts=time.time() - 7 * 86400):
            if r.get("key"):
                self._researched[r["key"]] = max(self._researched.get(r["key"], 0), r.get("ts", 0))
        self._research_times: list[float] = [r.get("ts", 0) for r in self.store.read_research(0, since_ts=day_start)]

        self.hist = HistoryTail([p for p in self.cfg["terminal_history"]["files"]]) \
            if self.cfg["terminal_history"]["enabled"] else None
        self.fw = FileWatch(self.cfg, self.privacy, self.log) if self.cfg["file_watch"]["enabled"] else None

        self._jobs: "queue.Queue[dict]" = queue.Queue()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._analyze_loop, daemon=True)

    def log(self, msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        try:
            self.log_file.write(line + "\n")
        except OSError:
            pass

    # ---------------- 生命週期 ----------------
    def run(self) -> None:
        self._mutex = acquire_mutex(MUTEX)
        if self._mutex is None:
            print(L("daemon 已經在跑了（mutex 被占用）", "daemon is already running (mutex held)"))
            return
        set_dpi_aware()
        if IS_MAC:
            ensure_capture_permission(self.log)
        for err in self.privacy.errors:
            self.log(L("⚠ 隱私", "⚠ privacy: ") + err)
        if self.cfg.get("_config_error"):
            self.log("⚠ " + self.cfg["_config_error"])
        if self.cfg["capture"]["enabled"] and self.cfg["capture"]["ocr"]:
            try:
                self.ocr = Ocr(self.cfg["capture"]["ocr_language"])
            except Exception as e:  # 含 ImportError（winrt 沒裝）
                self.log(L(f"OCR 初始化失敗，停用（不留截圖）：{e!r}",
                           f"OCR init failed; disabled (no screenshots will be kept): {e!r}"))
        self.store.pid_path.write_text(str(__import__("os").getpid()), encoding="utf-8")
        # 重啟後從事件檔重建最近 2 小時的區段，hook 的時間線才不會斷
        t = time.time()
        for s in segments(scrub_events(self.store.read_events(t - 7200, t), self.privacy), t - 7200, t):
            self._append_seg({k: s[k] for k in ("app", "title", "kind", "start", "end")})
        self.store.append_event({"type": "start"})
        self.store.update_control(stop=False, analyze_now=False)
        self._worker.start()
        if self.fw:
            threading.Thread(target=self._file_loop, daemon=True).start()
        pid, on = __import__('os').getpid(), 'on' if self.analyzer.available() else 'off'
        self.log(L(f"daemon 啟動 pid={pid} analyzer={on}", f"daemon started pid={pid} analyzer={on}"))
        interval = float(self.cfg["poll_interval_s"])
        try:
            while not self._stop.is_set():
                t0 = time.time()
                try:
                    self.tick(t0)
                except Exception as e:  # 單次 tick 掛掉不能讓整個 daemon 死
                    self.log(L("tick 例外：", "tick exception: ")
                             + "".join(traceback.format_exception_only(type(e), e)).strip())
                time.sleep(max(0.1, interval - (time.time() - t0)))
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        now = time.time()
        self._close_segment(now)
        self.store.append_event({"type": "stop"})
        self.write_state(now)
        self._stop.set()
        self._cap_pool.shutdown(wait=False, cancel_futures=True)
        try:
            self.store.pid_path.unlink()
        except OSError:
            pass
        self.log(L("daemon 結束", "daemon stopped"))

    # ---------------- 每一拍 ----------------
    def tick(self, now: float) -> None:
        ctl = self.store.read_control()
        if ctl.get("stop"):
            self.store.update_control(stop=False)
            self._stop.set()
            return
        paused_until = ctl.get("paused_until", 0)
        paused = paused_until > now
        self._paused_until = paused_until
        if ctl.get("analyze_now"):
            self.store.update_control(analyze_now=False)
            self._enqueue(now, L("使用者手動要求分析", "analysis requested by user"), force=True)

        idle_thr = self.cfg["idle_threshold_s"]
        is_idle = idle_seconds() >= idle_thr
        if is_idle and not self.idle:
            self.idle = True
            self.idle_since = now - idle_seconds()
            self._close_segment(self.idle_since)
            self.store.append_event({"type": "idle", "since": round(self.idle_since, 3)})
        elif not is_idle and self.idle:
            self.idle = False
            back = now - idle_seconds()
            if back - self.idle_since >= 1:
                self._append_seg({"app": "", "title": "", "kind": "idle", "start": self.idle_since, "end": back})
            self.store.append_event({"type": "active"})

        if paused and not self._was_paused:
            self._close_segment(now)
            self.store.append_event({"type": "pause", "until": round(paused_until, 3)})
        elif not paused and self._was_paused:
            self.store.append_event({"type": "resume"})
        self._was_paused = paused
        if paused:
            # 暫停期間的指令/檔案變更直接丟掉，恢復後才不會補記
            if self.hist:
                self.hist.new_commands()
            self._drain_files(discard=True)
            self.write_state(now, paused_until=paused_until)
            return
        if self.idle:
            self.write_state(now)
            self._maybe_periodic(now)
            return

        fg = foreground()
        if fg and not fg["minimized"] and fg["title"] != "":
            self._on_foreground(fg, now)
        self._maybe_capture(now, fg)

        if self.hist and now - self._last_hist >= 3:
            self._last_hist = now
            self._poll_history(now)
        if self.fw:
            self._drain_files()

        self.write_state(now)
        self._maybe_periodic(now)

    def _on_foreground(self, fg: dict, now: float) -> None:
        raw = fg["title"]
        kind = self.privacy.classify(fg["app"], raw)
        # Chrome 系的無痕視窗標題看不出來：問無障礙樹（每個視窗只查一次）
        if kind == "ok" and fg["app"].lower() in self._browsers and self._incognito.is_private(fg["hwnd"], fg["app"]):
            kind = "blocked"
        title, norm = self._title_key(raw)  # 標題一律先遮蔽才留（email、金鑰…）
        if kind == "ok" and (fg["app"], norm) in self._sensitive_ctx:
            kind = "blocked"  # 這個視窗之前被 OCR 判定為敏感，回來時直接遮蔽
        if kind == "blocked":
            title, norm = "", ""
        same = (self.cur and self.cur["app"] == fg["app"] and self.cur["kind"] == kind
                and self.cur["norm"] == norm)
        if same:
            self.cur["title"] = title
            self.cur["hwnd"] = fg["hwnd"]
            return
        self._close_segment(now)
        self.cur = {"app": fg["app"], "title": title, "norm": norm,
                    "kind": kind, "since": now, "hwnd": fg["hwnd"], "pid": fg["pid"]}
        self._dwell_shot_done = False
        self._last_thumb = None
        self.store.append_event({"type": "focus", "app": fg["app"], "kind": kind, "title": title})

    def _title_key(self, raw: str) -> tuple[str, str]:
        title = self.privacy.redact(raw or "")[:200]
        return title, normalize_title(title)

    def _close_segment(self, at: float) -> None:
        if not self.cur:
            return
        seg = {"app": self.cur["app"], "title": self.cur["title"], "kind": self.cur["kind"],
               "start": self.cur["since"], "end": at}
        if seg["end"] - seg["start"] >= 1:
            self._append_seg(seg)
        self.cur = None

    def _append_seg(self, seg: dict) -> None:
        prev = self.segs[-1] if self.segs else None
        key = lambda s: (s["app"], normalize_title(s["title"]), s["kind"])  # noqa: E731
        if prev and key(prev) == key(seg) and seg["start"] - prev["end"] < 60:
            prev["end"] = max(prev["end"], seg["end"])  # 同一視窗/同一段閒置中間只斷一下（重啟等）就接起來
        else:
            self.segs.append(seg)
            self.segs = self.segs[-80:]

    # ---------------- 擷取畫面 ----------------
    def _maybe_capture(self, now: float, fg: dict | None) -> None:
        cc = self.cfg["capture"]
        if not cc["enabled"] or not self.cur or not fg:
            return
        if self.cur["kind"] in ("blocked", "self", "nocap"):
            return
        if now - self._last_cap_check < cc["check_interval_s"]:
            return
        self._last_cap_check = now
        dwell = now - self.cur["since"]
        if dwell < cc["dwell_before_capture_s"]:
            return
        rect = window_rect(self.cur["hwnd"])
        if not rect:
            return
        if self._cap_future is not None and not self._cap_future.done():
            return  # 上一次截圖還卡著（視窗沒回應），先不拍
        self._cap_future = self._cap_pool.submit(capture.grab_window, self.cur["hwnd"], rect)
        try:
            img = self._cap_future.result(timeout=3.0)
        except FutureTimeout:
            self.log(L(f"截圖逾時（{self.cur['app']} 可能沒回應），略過",
                       f"screenshot timed out ({self.cur['app']} may be unresponsive); skipped"))
            return
        except Exception as e:
            self.log(L(f"截圖失敗：{e!r}", f"screenshot failed: {e!r}"))
            return
        if img is None:
            return
        # 截圖前後前景視窗/分頁有變（同一個 hwnd 換分頁也算）→ 這張可能不是我們以為的那個畫面，丟掉
        after = foreground()
        if (not after or after["hwnd"] != self.cur["hwnd"]
                or self.privacy.classify(after["app"], after["title"]) != "ok"
                or self._title_key(after["title"])[1] != self.cur["norm"]):
            return
        thumb = capture.thumb(img)
        ratio = capture.changed_ratio(self._last_thumb, thumb)
        due_major = ratio >= cc["major_change_ratio"] and now - self._last_cap_ts >= cc["major_min_interval_s"]
        due_minor = ratio >= cc["minor_change_ratio"] and now - self._last_cap_ts >= cc["minor_interval_s"]
        due_first = not self._dwell_shot_done
        due_refresh = self._last_cap_ts and now - self._last_cap_ts >= cc["refresh_s"] and ratio > 0
        if not (due_first or due_major or due_minor or due_refresh):
            return
        self._last_thumb = thumb
        self._dwell_shot_done = True
        self._last_cap_ts = now
        self._store_capture(img, now, reason="first" if due_first else "major" if due_major else "minor")

    def _store_capture(self, img, now: float, reason: str) -> None:
        if self.cur is None:  # 截圖途中視窗區段被關掉（閒置、暫停）：這張不要
            return
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        base = f"{time.strftime('%H%M%S', time.localtime(now))}_{int(now*1000)%1000:03d}"
        rel = f"shots/{day}/{base}.jpg"
        abspath = self.store.shot_abspath(rel)
        cc = self.cfg["capture"]

        lines: list[str] = []
        ocr_text = ""
        private = False
        if self.ocr is None:
            return  # 沒有 OCR 就無法檢查畫面內容 → 隱私優先，不留截圖
        try:
            lines = self.ocr.recognize(img)
        except OSError as e:
            self.log(L(f"OCR 失敗，這張不留：{e}", f"OCR failed; screenshot discarded: {e}"))
            return
        joined = "\n".join(lines)
        if joined:
            sensitive, why = self.privacy.text_sensitive(joined)
            if sensitive:
                private = True
                self.log(L(f"畫面判定敏感（{why}），不留存：{self.cur['app']}",
                           f"screen flagged sensitive ({why}); not kept: {self.cur['app']}"))
        if private:
            # 整個視窗改成遮蔽：標題從時間線拿掉；之後回到這個視窗也直接遮蔽
            app, hwnd, pid = self.cur["app"], self.cur["hwnd"], self.cur["pid"]
            self._sensitive_ctx.add((app, self.cur["norm"]))
            self.cur["title"], self.cur["kind"] = "", "blocked"  # 已結束的這段也不能留標題
            self._close_segment(now)
            self.cur = {"app": app, "title": "", "norm": "", "kind": "blocked", "since": now, "hwnd": hwnd, "pid": pid}
            self.store.append_event({"type": "focus", "app": app, "kind": "blocked", "title": ""})
            self.store.append_event({"type": "screen", "app": app, "private": True, "reason": reason})
            self.last_screen = {"ts": now, "app": app, "private": True}
            return

        ocr_text = self.privacy.redact(joined)[: cc["ocr_max_chars"]]
        if len(ocr_text.strip()) < 20:
            return  # 幾乎沒字（照片/影片）：內容沒辦法檢查，不存
        errs = find_errors(ocr_text, self.cur["app"], {a.lower() for a in self.cfg["apps"]["editor"]})
        try:
            capture.save_jpeg(img, abspath, cc["max_width"], cc["jpeg_quality"])
            if ocr_text:
                abspath.with_suffix(".txt").write_text(ocr_text, encoding="utf-8")
        except OSError as e:
            self.log(L(f"存檔失敗：{e}", f"failed to save screenshot: {e}"))
            return

        ev = {"type": "screen", "app": self.cur["app"], "title": self.cur["title"][:120],
              "shot": rel, "reason": reason, "has_ocr": bool(ocr_text)}
        if errs:
            ev["err"] = errs[:5]
        self.store.append_event(ev)
        self.last_screen = {"ts": now, "app": self.cur["app"], "title": self.cur["title"][:120],
                            "shot": rel, "shot_abs": str(abspath), "err": errs[:3], "private": False}
        # 「重新出現」的錯誤（給自動搜尋計次）：同一個視窗裡，這個錯誤在畫面上出現的次數比上一張多
        # （清掉畫面重跑又失敗、或往下又印了一次）。按視窗分開算，切去別的視窗再切回來不算。
        wkey = (self.cur["app"], self.cur.get("norm", ""))
        counts = self._err_counts(ocr_text, errs)
        prev = self._win_err_counts.pop(wkey, {})
        appeared = [e for e in errs if counts.get(line_key(e), 0) > prev.get(line_key(e), 0)]
        self._win_err_counts[wkey] = counts
        if len(self._win_err_counts) > 200:
            self._win_err_counts.pop(next(iter(self._win_err_counts)))
        if errs:
            self._on_errors(now, errs, rel, appeared)

    @staticmethod
    def _err_counts(ocr_text: str, errs: list[str]) -> dict[str, int]:
        keys = {line_key(e) for e in errs}
        counts: dict[str, int] = {}
        if not keys:
            return counts
        for ln in ocr_text.splitlines():
            k = line_key(ln.strip())
            if k in keys:
                counts[k] = counts.get(k, 0) + 1
        return counts

    # ---------------- 錯誤偵測 ----------------
    def _on_errors(self, now: float, errs: list[str], shot_rel: str, appeared: list[str] | None = None) -> None:
        if self.cur is None:
            return
        fresh = []
        for e in errs:
            k = line_key(e)
            if now - self._seen_errs.get(k, 0) > 90:
                fresh.append(e)
            self._seen_errs[k] = now
        if fresh:
            self.last_alert = {"ts": now, "app": self.cur["app"], "lines": fresh[:4], "shot": shot_rel}
            self.store.append_event({"type": "alert", "app": self.cur["app"], "lines": fresh[:4], "shot": shot_rel})
            self.log(L("偵測到錯誤：", "error detected: ") + " / ".join(fresh[:2])[:160])
        # 自動搜尋的「同一個錯誤第 N 次」看的是重新出現次數，不受上面 90 秒去重影響（重跑又失敗也要算）。
        # 只算終端機 / 編輯器：瀏覽器裡的「錯誤」可能是網頁故意顯示的，不能讓網頁觸發上網搜尋。
        dev_apps = {a.lower() for a in self.cfg["apps"]["terminal"] + self.cfg["apps"]["editor"]}
        if self.cur["app"].lower() in dev_apps:
            self._count_error_hits(now, errs if appeared is None else appeared)
        if not fresh:
            return
        if self.cfg["analyzer"]["enabled"] and self.analyzer.available() \
                and now - self._last_err_call >= self.cfg["analyzer"]["error_min_gap_s"]:
            self._last_err_call = now
            self._enqueue(now, L(f"畫面出現錯誤：{fresh[0][:120]}", f"error on screen: {fresh[0][:120]}"),
                          alert=self.last_alert)

    def _count_error_hits(self, now: float, appeared: list[str]) -> None:
        rc = self.cfg["research"]
        if not (rc["enabled"] and rc["on_repeated_error"]) or not appeared:
            return
        for e in appeared:
            k = line_key(e)
            hits = [t for t in self._err_hits.get(k, []) if now - t < rc["error_window_s"]] + [now]
            self._err_hits[k] = hits
            if len(hits) >= rc["error_repeats"] and self._enqueue_research(
                    now, "error", "err:" + k, e, L(f"同一個錯誤出現 {len(hits)} 次", f"same error seen {len(hits)} times")):
                break
        if len(self._err_hits) > 300:
            self._err_hits = {k: v for k, v in self._err_hits.items() if now - v[-1] < rc["error_window_s"]}

    # ---------------- 分析排程 ----------------
    def _maybe_periodic(self, now: float) -> None:
        a = self.cfg["analyzer"]
        if not a["enabled"] or not self.analyzer.available():
            return
        if now - self._last_periodic >= a["periodic_minutes"] * 60:
            self._last_periodic = now
            if not self.idle:
                self._enqueue(now, L("定期回顧", "periodic review"))

    def _rate_ok(self, now: float) -> bool:
        self._call_times = [t for t in self._call_times if now - t < 3600]
        return len(self._call_times) < self.cfg["analyzer"]["max_calls_per_hour"]

    def _enqueue(self, now: float, trigger: str, alert: dict | None = None, force: bool = False) -> None:
        if not force and not self.cfg["analyzer"]["enabled"]:
            return  # 使用者關掉背景分析：只有手動（ds analyze）才送
        if not force and not self._rate_ok(now):
            self.log(L(f"略過分析（每小時上限）：{trigger}", f"analysis skipped (hourly limit): {trigger}"))
            return
        self._call_times.append(now)
        self._last_periodic = now
        model = self.cfg["analyzer"]["deep_model"] if alert else self.cfg["analyzer"]["model"]
        self._jobs.put({"ts": now, "trigger": trigger, "alert": alert, "model": model})

    def _analyze_loop(self) -> None:
        last_sweep = 0.0
        while not self._stop.is_set():
            if time.time() - last_sweep >= 3600:  # 每小時清一次過期截圖/事件
                last_sweep = time.time()
                try:
                    from .retention import sweep
                    r = sweep(self.store, self.cfg)
                    if r["shots"] or r["events"]:
                        self.log(L(f"清理：截圖 {r['shots']} 張（{r['bytes']/1e6:.0f} MB）、事件檔 {r['events']} 個",
                                   f"cleanup: {r['shots']} screenshot(s) ({r['bytes']/1e6:.0f} MB), "
                                   f"{r['events']} event file(s)"))
                except Exception as e:
                    self.log(L("清理失敗：", "cleanup failed: ")
                             + "".join(traceback.format_exception_only(type(e), e)).strip())
            try:
                job = self._jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if job.get("type") == "research":
                    self._run_research(job)
                else:
                    self._run_analysis(job)
            except Exception as e:
                self.log(L("分析例外：", "analysis exception: ")
                         + "".join(traceback.format_exception_only(type(e), e)).strip())

    def _run_analysis(self, job: dict) -> None:
        now = job["ts"]
        if self._paused_until > time.time():
            self.log(L(f"暫停中，丟棄排隊的分析：{job['trigger'][:40]}",
                       f"paused; dropping queued analysis: {job['trigger'][:40]}"))
            return
        events = self.store.read_window(now - self.cfg["analyzer"]["periodic_minutes"] * 60, now)
        prompt, img = build_prompt(self.store, events, now, self.cfg["analyzer"]["periodic_minutes"],
                                   job["trigger"], alert=job["alert"], last_insight=self.last_insight,
                                   privacy=self.privacy)
        res = self.analyzer.analyze(prompt, img, model=job["model"])
        if not res:
            return
        if res.get("_error"):
            self.log(L("分析失敗：", "analysis failed: ") + str(res["_error"]))
            return
        res["ts"] = round(now, 3)
        res["trigger"] = job["trigger"]
        self.last_insight = res
        self.store.append_insight(res)
        sug = L("；", "; ").join(res.get("suggestions", [])[:3])
        self.log(L(f"分析[{res.get('_model')}] {res.get('state')}｜{res.get('doing','')}｜{sug}"
                   f"（{res.get('_elapsed')}s cr={res.get('_cr')}）",
                   f"analysis[{res.get('_model')}] {res.get('state')} | {res.get('doing','')} | {sug}"
                   f" ({res.get('_elapsed')}s cr={res.get('_cr')})"))
        if self.cfg["analyzer"].get("toast") and (job["alert"] or res.get("state") in ("stuck", "error")) and sug:
            toast(L(f"desktop-sense：{res.get('doing','')[:60]}", f"desktop-sense: {res.get('doing','')[:60]}"), sug[:240])
        rc = self.cfg["research"]
        if rc["enabled"]:
            la = self.last_alert
            if rc["on_stuck"] and res.get("state") == "stuck" and la and now - la.get("ts", 0) < 900 \
                    and la.get("lines"):
                self._enqueue_research(time.time(), "error", "err:" + line_key(la["lines"][0]), la["lines"][0],
                                       L("分析判斷卡住", "analysis says stuck"), ctx=res.get("doing", ""))
            topic = (res.get("research_topic") or "").strip()
            if rc["similar_projects"] and topic:
                key = "topic:" + " ".join(sorted(set(topic.lower().split())))[:120]
                self._enqueue_research(time.time(), "similar", key, topic, L("新專案/功能", "new project/feature"),
                                       ctx=res.get("context", "") or res.get("doing", ""))

    # ---------------- 自動搜尋 ----------------
    def _enqueue_research(self, now: float, kind: str, key: str, text: str, why: str, ctx: str = "") -> bool:
        """排一次自動搜尋；有排進去才回傳 True。冷卻從「成功搜到」才開始算，失敗 30 分鐘後可以再試。"""
        rc = self.cfg["research"]
        if not rc["enabled"] or not self.analyzer.research_available() or self._paused_until > now:
            return False
        if key in self._research_pending or now - self._researched.get(key, 0) < rc["cooldown_hours"] * 3600:
            return False
        day_start = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
        self._research_times = [t for t in self._research_times if t >= day_start]
        if len(self._research_times) + len(self._research_pending) >= rc["max_per_day"]:
            self.log(L(f"略過自動搜尋（今日上限）：{text[:60]}", f"auto-search skipped (daily limit): {text[:60]}"))
            return False
        prompt = build_research_prompt(kind, text, ctx, self.privacy)
        if prompt is None:
            self.log(L("略過自動搜尋：內容命中隱私規則", "auto-search skipped: text matches a privacy rule"))
            return False
        self._research_pending.add(key)
        self.log(L(f"排入自動搜尋（{why}）：{text[:80]}", f"auto-search queued ({why}): {text[:80]}"))
        self._jobs.put({"type": "research", "ts": now, "kind": kind, "key": key, "prompt": prompt, "why": why})
        return True

    def _run_research(self, job: dict) -> None:
        key = job["key"]
        try:
            if self._paused_until > time.time():
                return
            rc = self.cfg["research"]
            res = self.analyzer.research(job["prompt"], rc["model"], float(rc["timeout_s"]), float(rc["max_budget_usd"]))
            self._research_times.append(time.time())  # 失敗也算一次（可能已經花了錢），每日上限才擋得住
            if res.get("_error"):
                self._researched[key] = time.time() - rc["cooldown_hours"] * 3600 + 1800
                self.log(L("自動搜尋失敗：", "auto-search failed: ") + str(res["_error"]))
                return
            self._researched[key] = time.time()
        finally:
            self._research_pending.discard(key)
        res.update({"ts": round(time.time(), 3), "kind": job["kind"], "key": job["key"], "trigger": job["why"]})
        self.store.append_research(res)
        self.log(L(f"自動搜尋[{job['kind']}] {len(res.get('findings', []))} 筆｜{res.get('summary', '')}",
                   f"auto-search[{job['kind']}] {len(res.get('findings', []))} result(s) | {res.get('summary', '')}"))
        if rc.get("toast") and res.get("findings"):
            title = L("desktop-sense 找到可能的修法", "desktop-sense found a likely fix") if job["kind"] == "error" \
                else L("desktop-sense 找到類似的專案", "desktop-sense found similar projects")
            toast(title, res.get("summary", "")[:240])

    # ---------------- 其他來源 ----------------
    def _poll_history(self, now: float) -> None:
        if self.hist is None:
            return
        masked = [MASKED_CMD if self.privacy.text_blocked(c) else self.privacy.redact(c)[:300]
                  for c in self.hist.new_commands()]
        for c in masked:
            self.store.append_event({"type": "cmd", "cmd": c})
        th = self.cfg["terminal_history"]
        window = th["repeat_window_s"]
        self._recent_cmds = [(t, c) for t, c in self._recent_cmds if now - t < window]
        self._recent_cmds += [(now, c) for c in masked if c != MASKED_CMD]
        for cmd, n in repeated([c for _, c in self._recent_cmds], th["repeat_threshold"]):
            if cmd in {c for c in masked} and now - self._repeat_alerted.get(cmd, 0) >= window:
                self._repeat_alerted[cmd] = now
                self.store.append_event({"type": "alert", "app": "terminal",
                                         "lines": [L(f"{window // 60} 分鐘內同一指令跑了 {n} 次：{cmd[:160]}",
                                                     f"same command run {n} time{'' if n == 1 else 's'} "
                                                     f"in {window // 60} min: {cmd[:160]}")]})

    def _file_loop(self) -> None:
        """掃描很慢（十幾萬個檔），放背景執行緒，結果丟 queue 給主迴圈。"""
        if self.fw is None:
            return
        interval = float(self.cfg["file_watch"]["interval_s"])
        while not self._stop.is_set():
            t0 = time.time()
            try:
                for hit in self.fw.poll():
                    self._file_hits.put(hit)
            except Exception as e:
                self.log(L("檔案掃描例外：", "file scan exception: ")
                         + "".join(traceback.format_exception_only(type(e), e)).strip())
            took = time.time() - t0
            if took > interval:
                self.log(L(f"檔案掃描花了 {took:.0f}s，比間隔還久，考慮縮小 file_watch.roots",
                           f"file scan took {took:.0f}s, longer than the interval; consider narrowing file_watch.roots"))
            self._stop.wait(max(5.0, interval - took))

    def _drain_files(self, discard: bool = False) -> None:
        while True:
            try:
                hit = self._file_hits.get_nowait()
            except queue.Empty:
                return
            if not discard:
                self.store.append_event({"type": "file", "path": hit["path"], "ts": hit["ts"]})

    # ---------------- state.json ----------------
    def write_state(self, now: float, paused_until: float = 0) -> None:
        cur = None
        if self.cur:
            cur = {"app": self.cur["app"], "title": self.cur["title"], "kind": self.cur["kind"],
                   "since": self.cur["since"]}
        state = {
            "ts": round(now, 3), "t": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "pid": __import__("os").getpid(),
            "idle": self.idle, "idle_since": round(self.idle_since, 3),
            "paused_until": paused_until,
            "current": cur,
            "segments": self.segs[-40:],
            "last_screen": self.last_screen,
            "last_alert": self.last_alert,
            "last_insight_ts": self.last_insight.get("ts") if self.last_insight else None,
            "analyzer": self.analyzer.available(),
            "analyzer_backend": self.analyzer.describe(),
            "ocr": self.ocr is not None,
            "calls_last_hour": len([t for t in self._call_times if now - t < 3600]),
        }
        self.store.write_json(self.store.state_path, state)


def main() -> None:
    try:
        Daemon().run()
    except Exception:
        # pythonw 沒有 console：啟動就掛的錯誤一定要寫進 log，不然只會看到「沒在跑」
        try:
            with open(Store().log_path, "a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S") + L(" 致命錯誤：\n", " fatal error:\n") + traceback.format_exc())
        except OSError:
            pass
        raise


if __name__ == "__main__":
    main()
