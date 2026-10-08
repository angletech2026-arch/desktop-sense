"""設定：程式內建預設值 + 使用者 config.json（深度合併；privacy 清單只增不減，其他 list 整個覆蓋）。"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from .i18n import L

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG_PATH = ROOT / "config.json"

DEFAULTS: dict = {
    "language": "auto",  # auto = 跟 Windows 介面語言；zh / en 強制
    "poll_interval_s": 1.0,
    "idle_threshold_s": 120,
    "capture": {
        "enabled": True,
        "check_interval_s": 3.0,
        "dwell_before_capture_s": 1.5,
        # 192x108 縮圖中「有變化的像素比例」
        "major_change_ratio": 0.12,   # 換頁/捲動：≥3 秒就重拍
        "minor_change_ratio": 0.002,  # 多一行字：≥minor_interval_s 才重拍
        "minor_interval_s": 15,
        "major_min_interval_s": 10,   # 換頁/捲動也至少隔 10 秒（看影片時才不會一直拍）
        "refresh_s": 120,             # 有任何變化且超過這麼久也重拍
        "max_width": 1600,
        "jpeg_quality": 70,
        "ocr": True,
        "ocr_language": "",  # 空字串 = Windows 使用者語言；例：zh-Hant-TW、en-US、ja
        "ocr_max_chars": 8000,
    },
    "apps": {
        "terminal": ["WindowsTerminal.exe", "powershell.exe", "pwsh.exe", "cmd.exe", "conhost.exe",
                     "OpenConsole.exe", "mintty.exe", "wezterm-gui.exe", "alacritty.exe", "Tabby.exe"],
        "editor": ["Code.exe", "Cursor.exe", "Windsurf.exe", "devenv.exe", "idea64.exe", "pycharm64.exe",
                   "webstorm64.exe", "rider64.exe", "goland64.exe", "clion64.exe", "sublime_text.exe",
                   "notepad++.exe", "Zed.exe", "Unity.exe"],
        "browser": ["chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "Arc.exe"],
    },
    "privacy": {
        # 完全遮蔽：只記「有用這個 App」，標題、截圖、OCR 一律不留
        "blocked_apps": ["1Password.exe", "Bitwarden.exe", "KeePass.exe", "KeePassXC.exe", "LastPass.exe", "Dashlane.exe",
                         "Proton Pass.exe", "Enpass.exe"],
        # 也套用在終端機指令與檔案路徑上
        "blocked_title_regex": [
            # 醫療（繁中、簡中、日、韓、英、西、法、德、葡）
            r"病歷|病例|病患|患者|病人|個案管理|健保|掛號|門診|處方|檢驗報告|診斷書|診斷證明"
            r"|病历|医保|挂号|门诊|处方|检验报告|诊断书|诊断证明",
            r"カルテ|診療録|処方箋|診察券|検査結果|환자|진료|처방전|의무기록|진단서",
            r"\bpatients?\b|medical records?|\bEHR\b|\bclinic\b|\bdiagnos[ie]s\b|\bprescriptions?\b"
            r"|\bpaciente|historia clínica|historial médico|receta médica|dossier médical|ordonnance médicale"
            r"|Patientenakte|Krankenakte|Arztbrief|prontuário|receita médica",
            # 無痕 / 私密瀏覽（各語系 Chrome、Edge、Firefox 的視窗標題）
            r"\bInPrivate\b|\bIncognito\b|無痕|无痕|\(シークレット\)|シークレット ?(?:ウィンドウ|モード)|시크릿 ?(?:모드|창)|incógnito|Inkognito|navigation privée"
            r"|navigazione anonima|navegación privada|navegação privada|Private Browsing|私密瀏覽|隱私瀏覽|隐私浏览"
            r"|privates Fenster|Приватн|Инкогнито",
            r"1Password|Bitwarden|KeePass|LastPass|Dashlane|Password Manager|密碼管理|密码管理|パスワード ?マネージャー"
            r"|비밀번호 관리자",
            # 網路銀行
            r"網路銀行|網銀|网上银行|网银|ネットバンキング|インターネットバンキング|인터넷뱅킹|\b(?:internet|online) banking\b"
            r"|banca (?:en línea|online)|banque en ligne|Online-Banking|internet banking",
            # 成人內容：標題命中就整個視窗只記 App 名稱（不留標題、不截圖、不送分析）
            r"\bporn|\bnsfw\b|\bhentai\b|\bjav\b|\bmilf\b|onlyfans|fansly|pornhub|xvideos|xhamster|xnxx|redtube"
            r"|youporn|spankbang|chaturbate|stripchat|missav|javdb|javbus|rule34|e-hentai|nhentai",
            r"成人(?:影片|網站|論壇|動漫|漫畫|直播|网站|论坛|动漫|漫画)|色情|情色|18禁|十八禁|無碼|无码|有碼|有码|av女優"
            r"|啪啪|爆操|自慰|痴女|巨乳|做愛|做爱|性愛|性爱|約炮|约炮|裸聊|エロ|アダルト|無修正"
            r"|야동|포르노|19금|섹스",
            # 開著放祕密的檔案（.env、金鑰、憑證）
            r"(?:^|[\s\\/])\.env(?:\.[\w-]+)?\b|credentials?\.json|secrets?\.(?:json|ya?ml|toml)|\.pem\b|\bid_rsa\b|\.pfx\b|\.p12\b",
        ],
        # 只記標題、不截圖不 OCR：遊戲（全螢幕畫面截了也沒意義）、聊天（別人的訊息）
        "no_capture_apps": [
            "Riot Client.exe", "LeagueClientUx.exe", "League of Legends.exe", "VALORANT-Win64-Shipping.exe",
            "cs2.exe", "dota2.exe", "Overwatch.exe", "FortniteClient-Win64-Shipping.exe", "GenshinImpact.exe",
            "LockApp.exe", "Telegram.exe", "WhatsApp.exe", "Discord.exe", "Messenger.exe", "Signal.exe",
            "LINE.exe", "LineCall.exe", "LineMediaPlayer.exe", "WeChat.exe", "Weixin.exe", "KakaoTalk.exe",
            "Slack.exe", "Teams.exe", "ms-teams.exe", "Zoom.exe", "Webex.exe", "CiscoCollabHost.exe", "Skype.exe",
        ],
        "no_capture_title_regex": [
            r"\bGmail\b|\bOutlook\b|Yahoo Mail|Proton Mail|收件匣|收件箱",
            r"WhatsApp|\bDiscord\b|Telegram Web|\bMessenger\b|\bSlack\b|WeChat|微信",
        ],
        # AI 助手自己的視窗（Claude Code / Codex / Gemini CLI 終端機分頁、Claude 桌面版）：只記標題，不截自己
        "self_apps": ["claude.exe", "Codex.exe"],
        "self_title_regex": [r"^[\u2800-\u28ff✳✻✽✶✢·◐◓◑◒◴◵◶◷⏺]\s", r"\bClaude Code\b", r"^codex\b|\bCodex CLI\b",
                             r"^gemini\b|\bGemini CLI\b"],
        # 螢幕 OCR 命中任一條 → 整張視為私密：截圖跟文字都不留
        "sensitive_text_regex": [
            r"\b[A-Z][12]\d{8}\b",
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
            r"病歷號|身分證字號|身份證字號|健保卡號|medical record (?:number|no)|\bMRN\b",
            r"1Password|Bitwarden|KeePass|LastPass|密碼庫|password vault",
        ],
        # 出現這些關鍵字達 sensitive_keyword_min 種 → 視為醫療/個資畫面（英文不分大小寫）
        "sensitive_keywords": ["病历", "诊断", "处方", "用药", "检验", "カルテ", "診察", "処方", "환자", "진료", "처방",
                               "paciente", "diagnóstico", "receta médica",
                               "病歷", "病例", "病人", "病患", "患者", "診斷", "處方", "用藥", "檢驗", "肝功能",
                               "血壓", "掛號", "就診", "住院", "門診", "健保", "出生日期",
                               "patient", "diagnosis", "prescription", "medication", "medical record",
                               "lab result", "clinic", "date of birth", "symptom"],
        "sensitive_keyword_min": 2,
        # 遮蔽規則：寫入硬碟 / 送出前套用在標題、OCR、指令上。
        # private=true 的是「精準的祕密格式」：螢幕上出現就整張不存不送（因為送出去的是原圖，文字遮了圖還在）
        "redact": [
            {"pattern": r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", "repl": L("[私鑰]", "[private key]"), "private": True},
            {"pattern": r"\bsk-ant-[A-Za-z0-9_\-]{10,}", "repl": L("[API金鑰]", "[API key]"), "private": True},
            {"pattern": r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{20,}", "repl": L("[API金鑰]", "[API key]"), "private": True},
            {"pattern": r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,}", "repl": L("[API金鑰]", "[API key]"), "private": True},
            {"pattern": r"\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}", "repl": L("[GitHub權杖]", "[GitHub token]"), "private": True},
            {"pattern": r"\bAKIA[0-9A-Z]{16}\b", "repl": L("[AWS金鑰]", "[AWS key]"), "private": True},
            {"pattern": r"\bAIza[0-9A-Za-z_\-]{35}\b", "repl": L("[Google金鑰]", "[Google key]"), "private": True},
            {"pattern": r"\bxox[abprs]-[A-Za-z0-9\-]{10,}", "repl": L("[Slack權杖]", "[Slack token]"), "private": True},
            {"pattern": r"\bsb_secret_[A-Za-z0-9_\-]{10,}|\bsbp_[A-Za-z0-9]{20,}", "repl": L("[Supabase金鑰]", "[Supabase key]"), "private": True},
            {"pattern": r"\bre_[A-Za-z0-9]{8,}_[A-Za-z0-9]{16,}", "repl": L("[Resend金鑰]", "[Resend key]"), "private": True},
            {"pattern": r"\bnpm_[A-Za-z0-9]{36}\b", "repl": L("[npm權杖]", "[npm token]"), "private": True},
            {"pattern": r"\bhf_[A-Za-z0-9]{30,}", "repl": L("[Hugging Face權杖]", "[Hugging Face token]"), "private": True},
            {"pattern": r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}", "repl": L("[SendGrid金鑰]", "[SendGrid key]"), "private": True},
            {"pattern": r"\bglpat-[A-Za-z0-9_\-]{20,}", "repl": L("[GitLab權杖]", "[GitLab token]"), "private": True},
            {"pattern": r"\bwhsec_[A-Za-z0-9]{24,}", "repl": L("[Webhook金鑰]", "[webhook secret]"), "private": True},
            {"pattern": r"\b[MN][A-Za-z\d]{23,25}\.[\w-]{6}\.[\w-]{27,}", "repl": L("[Discord權杖]", "[Discord token]"), "private": True},
            {"pattern": r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b", "repl": L("[Telegram權杖]", "[Telegram token]"), "private": True},
            {"pattern": r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}", "repl": "[JWT]", "private": True},
            {"pattern": r"(?i)\bBearer\s+[A-Za-z0-9._~+/\-]{16,}=*", "repl": L("Bearer [遮蔽]", "Bearer [redacted]"), "private": True},
            {"pattern": r"(?i)\b([a-z][a-z0-9+.\-]{1,20})://[^\s:@/]{1,64}:[^\s@/]{1,128}@[^\s'\"]+", "repl": L(r"\1://[連線字串]", r"\1://[connection string]"), "private": True},
            {"pattern": r"\b[A-Z][12]\d{8}\b", "repl": L("[身分證]", "[national ID]"), "private": True},
            {"pattern": r"(?<!\d)(?:\d{4}[- ]){3}\d{4}(?!\d)", "repl": L("[卡號]", "[card number]"), "private": True},
            {"pattern": r"\bsb_publishable_[A-Za-z0-9_\-]{10,}", "repl": L("[Supabase公開金鑰]", "[Supabase publishable key]")},
            # 名字含 password/secret/token 的變數 = 一串 8 字以上、不像程式碼的值（例：DB_PASSWORD=hunter2xyz）→ 當真祕密，整張丟
            {"pattern": r"(?i)\b[A-Za-z0-9_]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)[A-Za-z0-9_]*\s*[:=]\s*['\"]?(?!(?:undefined|null|none|true|false|string|number|process|import|require|getenv)\b)[A-Za-z0-9!@#$%^&_\-+=/]{8,}(?=['\"\s;,]|$)", "repl": L("[遮蔽的祕密]", "[redacted secret]"), "private": True},
            # 其他「名字含 password/secret/token 的變數 = 值」：只遮文字（程式碼裡太常見，整張丟會失去寫程式的上下文）
            {"pattern": r"(?i)\b([A-Za-z0-9_]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)[A-Za-z0-9_]*)(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|\S+)", "repl": L(r"\1\2[遮蔽]", r"\1\2[redacted]")},
            {"pattern": r"(?i)(--?(?:password|passwd|pwd|token|secret|api-key)[\s=]+)\S+", "repl": L(r"\1[遮蔽]", r"\1[redacted]")},
            {"pattern": r"(?i)\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s'\"]+", "repl": L(r"\1://[連線字串]", r"\1://[connection string]")},
            {"pattern": r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,24}", "repl": "[email]"},
            {"pattern": r"(?<!\d)09\d{2}[- ]?\d{3}[- ]?\d{3}(?!\d)", "repl": L("[手機]", "[phone]")},
        ],
        # 檔案監看 / 終端機指令：路徑命中就不記
        "blocked_path_regex": [],
    },
    "terminal_history": {
        "enabled": True,
        "files": [r"%APPDATA%\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt"],
        "repeat_threshold": 3,
        "repeat_window_s": 600,
    },
    "file_watch": {
        "enabled": True,
        # 要監看的專案資料夾，例：r"C:\Users\you\projects"；空的就不監看
        "roots": [],
        "exclude_dirs": ["node_modules", ".git", ".next", "dist", "build", "out", "__pycache__", ".venv", "venv",
                         "target", ".turbo", ".vercel", "coverage", ".cache", ".pytest_cache", ".mypy_cache",
                         "Library", "Temp", "obj", "bin", ".gradle", ".idea", ".vs"],
        "debounce_s": 10,
        "interval_s": 30,
        "max_per_minute": 40,
    },
    "analyzer": {
        "enabled": True,
        # 分析用的模型：claude（預設，走你自己的 claude 指令）｜ollama｜openai（OpenAI 相容的本機伺服器：LM Studio、
        # llama.cpp、vLLM）。本地模型模式下截圖與文字完全不離開這台電腦，自動上網搜尋也會自動關閉。
        "backend": "claude",
        # 本地模型：url 空白 = 預設（Ollama http://127.0.0.1:11434、LM Studio http://127.0.0.1:1234/v1）；
        # model 空白 = Ollama 用 gemma3:4b。只准連 localhost，除非 allow_remote=true。
        "local": {"url": "", "model": "", "allow_remote": False},
        "model": "haiku",
        "deep_model": "sonnet",
        "periodic_minutes": 20,
        "error_min_gap_s": 120,
        "max_calls_per_hour": 12,
        "send_images": True,
        "image_max_width": 1280,
        "timeout_s": 150,
        "claude_path": "",
        "toast": True,
        # 「你是誰」：在 config.json 寫具體一點，分析器的建議會更貼切
        "user_profile": L("軟體工程師；偏好直接、具體、可執行的建議，不要說教。",
                          "Software engineer; prefers direct, specific, actionable suggestions. No lecturing."),
    },
    "hook": {
        "enabled": True,
        # 增量模式：同一個對話裡只附「上一則訊息之後的變化」，省 token；每 full_refresh_minutes 分鐘或訊息提到畫面時給完整版
        "delta": True,
        "full_refresh_minutes": 30,
        "minutes": 20,
        "max_segments": 10,
        "min_segment_s": 8,
        "include_ocr": True,
        "ocr_chars": 500,
        "screen_max_age_s": 1200,
    },
    # 自動搜尋：卡在同一個錯誤 → 去 GitHub / Reddit / Stack Overflow 找修法；開始做新東西 → 找有沒有類似的開源專案。
    # 只送錯誤行 / 主題文字（不送截圖），走使用者自己的 claude 帳號。
    "research": {
        "enabled": True,
        "on_repeated_error": True,
        "error_repeats": 2,          # 同一個錯誤在 error_window_s 內出現幾次就搜
        "error_window_s": 900,
        "on_stuck": True,            # 分析器判斷「卡住」（stuck）時也搜
        "similar_projects": True,
        "model": "sonnet",
        "max_per_day": 8,
        "cooldown_hours": 12,        # 同一個錯誤 / 主題多久內不重搜
        "timeout_s": 240,
        "max_budget_usd": 0.5,
        "toast": True,
    },
    "watch": {"min_dwell_s": 45, "min_gap_s": 20},
    "retention": {"shots_hours": 48, "max_shots_mb": 800, "events_days": 30, "insights_days": 90},
}


# 隱私清單只能「加」：預設 ∪ 使用者設定。這樣舊的 config.json 永遠蓋不掉新版程式加的保護。
ADDITIVE_PRIVACY = {"blocked_apps", "blocked_title_regex", "no_capture_apps", "no_capture_title_regex",
                    "self_apps", "self_title_regex", "sensitive_text_regex", "sensitive_keywords",
                    "redact", "blocked_path_regex"}

TEMPLATE = {
    "_readme": L("這裡只放你要改的設定，其餘用程式內建預設（ds config 可看）。"
                 "privacy 底下的清單會「加到」預設清單上，不會取代預設。",
                 "Only put the settings you want to change here; everything else uses built-in defaults (see `ds config`). "
                 "Lists under privacy are ADDED to the defaults and can never remove them."),
    "privacy": {"blocked_title_regex": [], "no_capture_apps": []},
    "file_watch": {"roots": []},
}


def _merge(base: dict, over: dict, path: tuple = (), errors: list | None = None) -> dict:
    for k, v in over.items():
        if k.startswith("_"):
            continue
        cur = base.get(k)
        if isinstance(cur, dict):
            if isinstance(v, dict):
                _merge(cur, v, path + (k,), errors)
            elif errors is not None:
                errors.append(".".join(path + (k,)) + L(" 應該是物件，忽略", " should be an object; ignored"))
        elif path == ("privacy",) and k in ADDITIVE_PRIVACY:
            if isinstance(v, list):
                base[k] = cur + [x for x in v if x not in cur]
            elif errors is not None:
                errors.append(L(f"privacy.{k} 應該是清單，忽略", f"privacy.{k} should be a list; ignored"))
        elif cur is not None and v is not None and type(cur) is not type(v) and not (
                isinstance(cur, (int, float)) and isinstance(v, (int, float)) and not isinstance(v, bool)):
            if errors is not None:
                errors.append(".".join(path + (k,)) + L(f" 型別不對（要 {type(cur).__name__}），忽略",
                                                       f" has the wrong type (expected {type(cur).__name__}); ignored"))
        elif v is not None:
            base[k] = v
    return base


def load(write_default: bool = True) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if not isinstance(user, dict):
                raise ValueError(L("最外層必須是物件 {...}", "top level must be an object {...}"))
            errors: list[str] = []
            _merge(cfg, user, (), errors)
            if errors:
                cfg["_config_error"] = L("config.json 有設定被忽略：", "some config.json settings were ignored: ") + "; ".join(errors[:5])
        except (OSError, ValueError) as e:
            cfg = copy.deepcopy(DEFAULTS)
            cfg["_config_error"] = L(f"config.json 讀取失敗，改用預設值：{e}", f"could not read config.json; using defaults: {e}")
    elif write_default:
        try:
            CONFIG_PATH.write_text(json.dumps(TEMPLATE, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return cfg


def save(cfg: dict) -> None:
    clean = {k: v for k, v in cfg.items() if not k.startswith("_")}
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))
