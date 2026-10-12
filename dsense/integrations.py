"""把 desktop-sense 接到 AI 寫程式工具：Claude Code（hook + /desk skill）、Codex CLI、Gemini CLI、Muse Code，
以及只能裝 skill 的 Grok Bot（它不跑使用者設的 hook，要使用者叫它看畫面才會去讀）。

只改使用者家目錄底下這幾個工具自己的設定檔；改之前先備份；只動我們自己那一筆 hook（靠指令內容辨識），
別人的 hook 一律不碰。設定檔不是合法 JSON 就不改，印出手動加的方法。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from collections.abc import Mapping
from pathlib import Path

from .config import ROOT
from .i18n import L

SKILL_SRC = ROOT / "skills" / "desk" / "SKILL.md"
GROK_SKILL_SRC = ROOT / "skills" / "grok" / "SKILL.md"  # 範本：{{DS}} 換成這個安裝位置的 ds 絕對路徑
# 舊版裝過的、沒有路徑的寫法（只認完全相同的字串）
_LEGACY = {"ds.cmd hook", "ds hook"}


def _python() -> Path:
    venv = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return venv if venv.exists() else Path(sys.executable)


def claude_command() -> str:
    """Claude Code 在 Windows 跑 hook 可能用 Git Bash，也可能用 PowerShell（沒裝 Git Bash 時）。
    PowerShell 遇到「"路徑" "參數"」會語法錯誤，所以 Windows 一律用正斜線、不加引號（兩邊都吃）；
    路徑有空白就換 8.3 短路徑，真的沒辦法才加引號（只剩 Git Bash 吃得下）。"""
    py, script = _python().as_posix(), (ROOT / "ds.py").as_posix()
    quoted = f'"{py}" "{script}" hook'
    if os.name != "nt":
        return quoted
    parts = []
    for p in (py, script):
        if _SHELL_UNSAFE.search(p):
            short = (_short_path(p) or "").replace("\\", "/")
            if not short or _SHELL_UNSAFE.search(short):
                return quoted
            p = short
        parts.append(p)
    return f"{parts[0]} {parts[1]} hook"


# Codex（PowerShell）/ Gemini CLI 跑 hook 的 shell 不一定。路徑沒有空白或特殊字元 → 直接給 bin\ds.cmd 的絕對路徑
# （cmd / PowerShell / bash 都吃，也不靠 PATH）；否則退回 PATH 上的 ds.cmd（install.ps1 會把 bin 加進 PATH）。
_SHELL_UNSAFE = re.compile(r"[\s&|<>^%\"'()`;,$@{}#]")


def _short_path(path: str) -> str | None:
    """Windows 8.3 短路徑（沒有空白），讓含空白的路徑也能不加引號；磁碟關掉 8.3 就回傳 None。"""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf))
        return buf.value if 0 < n < len(buf) else None
    except (AttributeError, OSError):
        return None


def shim_command() -> str:
    """一律給絕對路徑：不能只寫 ds.cmd，因為 cmd.exe 會先找目前資料夾，別人的 repo 放一個 ds.cmd 就會被執行。"""
    if os.name != "nt":  # macOS / Linux：sh -c 跑，用引號包住就行
        return '"' + str(ROOT / "bin" / "ds").replace('"', '\\"') + '" hook'
    launcher = str(ROOT / "bin" / "ds.cmd")
    if not _SHELL_UNSAFE.search(launcher):
        return f"{launcher} hook"
    short = _short_path(launcher)
    if short and not _SHELL_UNSAFE.search(short):
        return f"{short} hook"
    return f'"{launcher}" hook'  # 最後手段：cmd / bash 吃得下，PowerShell 不行


def _launcher_for(path: str, windows: bool, short: str | None = None) -> str:
    """給 AI 自己在 shell 裡跑的 ds 路徑（後面接子指令）。Grok Bot 在 Windows 用 PowerShell 跑指令：
    沒有空白就直接寫路徑（cmd / PowerShell 都吃）；有空白先換 8.3 短路徑，再不行才用 PowerShell 的 & "…"。"""
    if not windows:
        return '"' + path.replace('"', '\\"') + '"'
    if not _SHELL_UNSAFE.search(path):
        return path
    if short and not _SHELL_UNSAFE.search(short):
        return short
    return f'& "{path}"'


def ds_launcher() -> str:
    if os.name != "nt":
        return _launcher_for(str(ROOT / "bin" / "ds"), False)
    path = str(ROOT / "bin" / "ds.cmd")
    return _launcher_for(path, True, _short_path(path) if _SHELL_UNSAFE.search(path) else None)


def muse_command() -> str | None:
    """Muse Code 在 Windows 用 cmd.exe 跑 hook，而且會把指令裡的雙引號轉成 \\"（cmd 看不懂，整條失敗）。
    所以只能用 claude_command 那種不加引號的寫法；非加引號不可（路徑有空白又沒有 8.3 短路徑）→ None。"""
    cmd = claude_command()
    return None if os.name == "nt" and '"' in cmd else cmd


def _muse_entry() -> dict | None:
    cmd = muse_command()
    return {"type": "command", "command": cmd, "timeout": 10} if cmd else None  # timeout 單位是秒，一定要整數


def muse_config_dir(home: Path, env: Mapping[str, str] | None = None) -> Path:
    """Muse 找設定檔的順序（1.4.6 實測）：$XDG_CONFIG_HOME/muse → $HOME/.config/muse → 家目錄/.config/muse。
    Windows 上 Path.home() 不看 HOME，所以要自己查。env 是 None（測試用的假家目錄）就只看 home。"""
    env = env or {}
    xdg = (env.get("XDG_CONFIG_HOME") or "").strip()
    if xdg and os.path.isabs(xdg):
        return Path(xdg) / "muse"
    h = (env.get("HOME") or "").strip()
    if h and os.path.isabs(h):
        return Path(h) / ".config" / "muse"
    return home / ".config" / "muse"


def _norm(s: str) -> str:
    return (s or "").replace("\\", "/").replace('"', "").strip().lower()


def is_ours(command: str) -> bool:
    """只認「指向這個安裝位置」的 hook（長路徑或 8.3 短路徑），別人的 ds.py 不碰。"""
    cmd = _norm(command)
    if cmd in _LEGACY:
        return True
    if not cmd.endswith(" hook"):
        return False
    roots = {_norm(str(ROOT))}
    short = _short_path(str(ROOT))
    if short:
        roots.add(_norm(short))
    return any(f"{r}/" in cmd for r in roots)


def _load(path: Path) -> dict | None:
    """不存在 → {}；不是合法 JSON 物件 → None（不碰它）。"""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + f".bak-desktop-sense-{time.strftime('%Y%m%d-%H%M%S')}"))
    tmp = path.with_name(path.name + ".dsense.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _set_hook(data: dict, event: str, entry: dict | None) -> bool:
    """在 data["hooks"][event] 裡移掉我們舊的那筆，entry 不是 None 就加新的。回傳有沒有變。"""
    hooks = data.get("hooks")
    if hooks is None:
        if entry is None:
            return False
        hooks = data["hooks"] = {}
    if not isinstance(hooks, dict):
        raise ValueError("hooks is not an object")
    groups = hooks.get(event) or []
    if not isinstance(groups, list):
        raise ValueError(f"hooks.{event} is not a list")
    before = json.dumps(groups, sort_keys=True)
    kept = []
    for g in groups:
        if isinstance(g, dict) and isinstance(g.get("hooks"), list):
            inner = [h for h in g["hooks"] if not (isinstance(h, dict) and is_ours(str(h.get("command", ""))))]
            if inner:
                kept.append({**g, "hooks": inner})
        else:
            kept.append(g)
    if entry is not None:
        kept.append({"hooks": [entry]})
    if kept:
        hooks[event] = kept
    else:
        hooks.pop(event, None)
    if not hooks:
        data.pop("hooks", None)
    return json.dumps(kept, sort_keys=True) != before


AGENTS = {
    # 名稱: (設定檔, 事件, hook 內容, 偵測用資料夾)
    "claude": (Path(".claude") / "settings.json", "UserPromptSubmit",
               lambda: {"type": "command", "command": claude_command(), "timeout": 5}, ".claude"),
    "codex": (Path(".codex") / "hooks.json", "UserPromptSubmit",
              lambda: {"type": "command", "command": shim_command(), "commandWindows": shim_command(), "timeout": 10,
                       "statusMessage": "desktop-sense"}, ".codex"),
    "gemini": (Path(".gemini") / "settings.json", "BeforeAgent",
               lambda: {"type": "command", "command": shim_command(), "name": "desktop-sense", "timeout": 5000}, ".gemini"),
    # Muse 不認得的欄位會讓那筆 hook 被略過，所以只放 type / command / timeout
    "muse": (Path(".config") / "muse" / "settings.json", "UserPromptSubmit", _muse_entry, ".config/muse"),
}
LABELS = {"claude": "Claude Code", "codex": "Codex CLI", "gemini": "Gemini CLI", "muse": "Muse Code"}


def _paths(name: str, home: Path, env: Mapping[str, str] | None) -> tuple[Path, Path]:
    """(設定檔, 偵測用資料夾)。只有 Muse 的位置會隨環境變數變。"""
    rel, _e, _h, d = AGENTS[name]
    if name == "muse":
        base = muse_config_dir(home, env)
        return base / "settings.json", base
    return home / rel, home / d


def detect(home: Path, env: Mapping[str, str] | None = None) -> list[str]:
    return [name for name in AGENTS if _paths(name, home, env)[1].is_dir()]


def setup(home: Path, agents: list[str] | None = None, remove: bool = False,
          env: Mapping[str, str] | None = None) -> list[str]:
    """接上（或 remove=True 拆掉）各工具的 hook。回傳給人看的訊息。
    env：真的環境變數（ds setup 傳 os.environ）；測試不傳，就只看 home，不會碰到真的設定。"""
    msgs: list[str] = []
    targets = agents if agents else detect(home, env)
    if not targets:
        msgs.append(L("沒偵測到 Claude Code / Codex / Gemini CLI / Muse Code（家目錄沒有 .claude / .codex / .gemini / .config/muse）。"
                      "裝好之後再跑一次 ds setup。",
                      "No Claude Code / Codex / Gemini CLI / Muse Code found (no .claude / .codex / .gemini / .config/muse "
                      "in your home folder). Run `ds setup` again after installing one."))
        return msgs
    for name in targets:
        if name == "grok":  # 只裝 skill，不在自動偵測裡（有 .cursor 也可能只是 Cursor 編輯器）
            msgs += _grok_skill(home, remove)
            continue
        if name not in AGENTS:
            msgs.append(L(f"不認得的工具：{name}（可用：claude、codex、gemini、muse、grok）",
                          f"Unknown tool: {name} (choose from: claude, codex, gemini, muse, grok)"))
            continue
        _rel, event, make, _d = AGENTS[name]
        label = LABELS[name]
        path = _paths(name, home, env)[0]
        data = _load(path)
        entry = None if remove else make()
        if not remove and entry is None:
            msgs.append(L(f"⚠ {label}：desktop-sense 的路徑有空白或特殊字元，又沒有 8.3 短路徑，{label} 跑不了這個 hook，沒有修改。"
                          "把 desktop-sense 放到路徑沒有空白的資料夾再跑一次 ds setup。",
                          f"⚠ {label}: the desktop-sense path has spaces or special characters and no 8.3 short name, "
                          f"so {label} can't run the hook; left untouched. Move desktop-sense to a folder without spaces "
                          "and run `ds setup` again."))
            continue
        # Muse 的 settings.json 少了 schema_version 整個 Muse 會打不開：只改 Muse 自己建好的檔，絕不從零建
        if name == "muse" and not remove and isinstance(data, dict) and "schema_version" not in data:
            msgs.append(L(f"⚠ {label}：{path} 還沒由 Muse 建好（或少了 schema_version），沒有修改。先執行一次 muse，再跑 ds setup muse。",
                          f"⚠ {label}: {path} hasn't been created by Muse yet (or has no schema_version); left untouched. "
                          "Run muse once, then `ds setup muse`."))
            continue
        if data is None:
            msgs.append(L(f"⚠ {path} 不是合法 JSON，沒有修改。請手動在 hooks.{event} 加入：{json.dumps(entry, ensure_ascii=False)}",
                          f"⚠ {path} is not valid JSON; left untouched. Add this under hooks.{event} by hand: "
                          f"{json.dumps(entry, ensure_ascii=False)}"))
            continue
        try:
            changed = _set_hook(data, event, entry)
        except ValueError as e:
            msgs.append(L(f"⚠ {path} 的 hooks 格式不認得（{e}），沒有修改。", f"⚠ unrecognized hooks layout in {path} ({e}); left untouched."))
            continue
        if changed:
            _save(path, data)
        if remove:
            msgs.append(L(f"✓ {label}：已移除 hook（{path}）", f"✓ {label}: hook removed ({path})") if changed
                        else L(f"・{label}：本來就沒有 hook", f"・{label}: no hook to remove"))
        else:
            msgs.append(L(f"✓ {label}：hook 已接上（{path}）", f"✓ {label}: hook installed ({path})"))
        if name == "claude":
            msgs += _skill(home, remove)
        if name == "codex" and not remove:
            msgs.append(L("  → 第一次要在 Codex 裡輸入 /hooks，把 desktop-sense 這個 hook 設成信任（之後就不用）。",
                          "  → In Codex, run /hooks once and trust the desktop-sense hook (one-time)."))
        if name == "gemini" and not remove:
            msgs.append(L("  → Gemini CLI 支援是實驗性的。", "  → Gemini CLI support is experimental."))
        if name == "muse" and not remove:
            msgs.append(L("  → 已經開著的 Muse 要關掉重開才會載入（hook 只在開新 session 時讀）。",
                          "  → Restart any open Muse session to load it (hooks are read when a session starts)."))
            if data.get("contributor_mode_enabled") is True:
                msgs.append(L("  ⚠ 你的 Muse 開著 contributor mode：每則訊息（包括 desktop-sense 附上的螢幕摘要）都可能被 Meta "
                              "拿去訓練模型。不想的話在 Muse 裡打 /settings，把 contributor training data 關掉。",
                              "  ⚠ Muse has contributor mode on: every message, including the screen summary desktop-sense "
                              "adds, may be used by Meta to train models. To opt out, run /settings in Muse and turn off "
                              "contributor training data."))
    return msgs


def _skill(home: Path, remove: bool) -> list[str]:
    dst = home / ".claude" / "skills" / "desk" / "SKILL.md"
    ours = dst.exists() and "desktop-sense" in dst.read_text(encoding="utf-8", errors="replace")
    if remove:
        if ours:
            dst.unlink()  # 只刪我們的 SKILL.md；資料夾裡使用者自己放的東西不動
            try:
                dst.parent.rmdir()
            except OSError:
                pass
            return [L("✓ 已移除 /desk skill", "✓ /desk skill removed")]
        return []
    if not SKILL_SRC.exists():
        return [L(f"⚠ 找不到 {SKILL_SRC}，略過 /desk skill", f"⚠ {SKILL_SRC} not found; skipping the /desk skill")]
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not ours:  # 同名但不是我們的 skill：先備份
        shutil.copy2(dst, dst.with_name(f"SKILL.md.bak-desktop-sense-{time.strftime('%Y%m%d-%H%M%S')}"))
    shutil.copy2(SKILL_SRC, dst)
    return [L("✓ 已安裝 /desk skill", "✓ /desk skill installed")]


def _grok_skill(home: Path, remove: bool) -> list[str]:
    """Grok Bot（Cursor 做的）讀 ~/.cursor/skills；它不跑使用者設的 hook（實測 ~/.cursor/hooks.json 沒被執行），
    所以只能裝 skill：使用者叫它看畫面時，它照 skill 跑 ds now，而不是自己截一張沒遮隱私的全螢幕。"""
    dst = home / ".cursor" / "skills" / "desk" / "SKILL.md"
    ours = dst.exists() and "desktop-sense" in dst.read_text(encoding="utf-8", errors="replace")
    if remove:
        if not ours:
            return [L("・Grok Bot：本來就沒有 desk skill", "・Grok Bot: no desk skill to remove")]
        dst.unlink()  # 只刪我們的 SKILL.md；資料夾裡別的東西（含備份）不動
        try:
            dst.parent.rmdir()
        except OSError:
            pass
        return [L("✓ Grok Bot：已移除 desk skill", "✓ Grok Bot: desk skill removed")]
    if not GROK_SKILL_SRC.exists():
        return [L(f"⚠ 找不到 {GROK_SKILL_SRC}，略過 Grok Bot", f"⚠ {GROK_SKILL_SRC} not found; skipping Grok Bot")]
    text = GROK_SKILL_SRC.read_text(encoding="utf-8").replace("{{DS}}", ds_launcher())
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and not ours:  # 同名但不是我們的 skill：先備份
        shutil.copy2(dst, dst.with_name(f"SKILL.md.bak-desktop-sense-{time.strftime('%Y%m%d-%H%M%S')}"))
    tmp = dst.with_name("SKILL.md.dsense.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, dst)
    return [L(f"✓ Grok Bot：已安裝 desk skill（{dst}）", f"✓ Grok Bot: desk skill installed ({dst})"),
            L("  → Grok Bot 不會每則訊息自動附上畫面：要它看的時候說「看一下我的畫面」，它會去讀 desktop-sense。"
              "沒反應的話開一個新對話再試。",
              "  → Grok Bot doesn't get context on every message: say \"look at my screen\" and it reads desktop-sense. "
              "If it doesn't, start a new chat and try again."),
            L("  ⚠ 這只是給 Grok 的指示：沒用到這個 skill 時，它可能自己截整個螢幕送上雲端，那張截圖不會遮掉密碼、網銀這類畫面。"
              "畫面上有隱私內容時，別叫它看畫面。",
              "  ⚠ This is only an instruction to Grok: without the skill it may take its own full-screen screenshot and "
              "send it to the cloud, and that screenshot doesn't hide password managers or banking pages. Don't ask it "
              "to look at your screen while something private is showing.")]
