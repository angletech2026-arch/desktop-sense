# desktop-sense

**讓你的 AI 寫程式助手直接看得到你在做什麼。**

desktop-sense 是一個在 Windows 背景執行的小程式。你每傳一則訊息給 Claude Code（或 Codex CLI、Gemini CLI），它就自動附上「你現在在哪個視窗、最近 20 分鐘做了什麼、畫面上有什麼錯誤」。不用再貼截圖、複製錯誤訊息，也不用解釋「我現在在瀏覽器看部署 log」。

[English](README.md) · [繁體中文](README.zh-TW.md) · MIT 授權 · Windows 10/11

## 功能

- **每則訊息自動附上畫面狀態**：目前視窗、最近時間線、畫面上的錯誤、最新截圖路徑。
- **省 token**：同一個對話裡只附「上一則訊息之後的變化」。第一則約幾百到 1,500 tokens，之後通常不到 200。
- **錯誤雷達**：偵測畫面上的 `Traceback`、`npm ERR!`、`error TS…`、建置失敗、同一指令重複跑，背景用 Haiku 分析並跳 Windows 通知給修法。
- **自動上網找解法**：同一個錯誤出現第二次，就自動去 **GitHub issues、Reddit、Stack Overflow** 找有沒有人遇過，整理出最可能的修法和連結。開始做新東西時，會先找 GitHub 上有沒有類似的開源專案，免得重造輪子。
- **「看著我」模式**：`ds stream` 搭配 Claude Code 的 Monitor，事件一發生 AI 就能反應。
- **`/desk` skill 與 `ds` 指令**：隨時查時間線、截圖、OCR 文字、分析結果。

## 隱私：100% 本機

- **沒有伺服器、不用帳號、不回傳任何使用數據。** desktop-sense 自己從不連上網際網路；它唯一可能自己發出的連線，是你開了「完全本機模式」時連到你自己電腦上的本地模型（預設只准連本機）。截圖、OCR、時間線、分析結果都只存在你電腦的 `data/` 資料夾。
- **唯一會離開電腦的**，是你自己的 AI 助手看到的內容，用你自己的帳號送出，跟你自己貼截圖給它一樣：
  - 每則訊息附的狀態：進你自己的對話
  - 背景分析：透過你的 `claude` 指令，送出允許的視窗的最新截圖與去識別化的 OCR 文字
  - 自動搜尋：只送錯誤那一行或主題文字（不送截圖），而且只能用網路搜尋
  把 `analyzer.enabled`、`research.enabled` 設成 `false`，就只剩對話裡的那段狀態。
- **敏感視窗一律不截**：密碼管理器、無痕／私密瀏覽、網路銀行、病歷、成人網站、`.env`／私鑰／憑證檔，只記 App 名稱，不留標題、不截圖、不 OCR。內建關鍵字涵蓋繁中、簡中、英、日、韓、西、法、德、葡文；你的銀行名、客戶名可以自己加進 `config.json`。聊天軟體、網頁版信箱、視訊會議、遊戲只記標題。
- **畫面上出現祕密就整張丟掉**：各家 API 金鑰與權杖（OpenAI、Anthropic、GitHub、GitLab、AWS、Google、Slack、Supabase、npm、Hugging Face、SendGrid、Stripe、Discord、Telegram…）、JWT、私鑰、帶密碼的網址、`PASSWORD=...` 這類設定、卡號、身分證號 → 不是打馬賽克，是整張不存不送。
- **新規則會回溯套用**：之後加的規則，也會把以前記到的相符標題、截圖、指令、檔案路徑、錯誤、分析藏起來。
- **自訂規則只能加、不能減**：`config.json` 裡的清單會加在內建預設上，舊的或寫壞的設定檔永遠關不掉內建保護。
- `ds pause 30` 暫停記錄 30 分鐘。截圖 48 小時後自動刪除，時間線保留 30 天，分析與搜尋結果保留 90 天。

螢幕和網頁上的文字一律視為**不可信資料**：去掉看不見的字元、清理過、包在隨機標記裡；即時串流每行標〔螢幕資料〕；自動搜尋的連結只有 GitHub、Stack Overflow、Reddit 這類開發者網站才能點；並明確告訴 AI 不准照裡面的指示做（防 prompt injection）。背景分析和自動搜尋的 `claude` 在沙箱裡跑：空的工作資料夾、不載入 CLAUDE.md／外掛／hook（`--safe-mode`）、沒有能執行程式的工具（`--restricted`）；而且只有終端機或編輯器裡的錯誤會觸發搜尋，網頁不行。

## 支援的工具

| 工具 | 方式 | 狀態 |
|---|---|---|
| **Claude Code** | `UserPromptSubmit` hook + `/desk` skill | ✅ 天天在用 |
| **Codex CLI** | `UserPromptSubmit` hook（`~/.codex/hooks.json`） | ✅ 已在 Windows + Codex CLI 0.161 實測；第一次要在 Codex 輸入 `/hooks` 設為信任 |
| **Gemini CLI** | `BeforeAgent` hook（`~/.gemini/settings.json`） | 🧪 實驗性 |

背景分析與自動搜尋是透過 `claude` 指令（`claude -p`）執行，需要裝 Claude Code；hook 本身三種工具都能用。

## 安裝

需要：Windows 10/11、Python 3.10 以上（`winget install Python.Python.3.12`）、Claude Code／Codex CLI／Gemini CLI 其中之一。

```powershell
git clone https://github.com/angletech2026-arch/desktop-sense
cd desktop-sense
powershell -ExecutionPolicy Bypass -File install.ps1
```

安裝程式會建立虛擬環境、把 `ds` 加進 PATH、自動接上找到的 AI 工具（先備份設定檔，只動自己那一筆 hook）、設定開機自動啟動並立即啟動。裝完開一個新的終端機。

接著直接問 AI：

- 「看一下這個錯誤」「我畫面上哪裡怪怪的？」
- 「我過去一小時在幹嘛？」
- 「我 debug 的時候幫我盯著，有發現什麼跟我說」

## 常用指令

| 指令 | 說明 |
|---|---|
| `ds status` | daemon 狀態 |
| `ds now` | AI 看到的完整狀態 |
| `ds recent 60` | 最近 60 分鐘時間線、指令、錯誤 |
| `ds shot 3` | 最近 3 張截圖路徑 |
| `ds analyze [--deep]` | 立刻分析一次 |
| `ds research` | 幫最近一次的錯誤上網找解法 |
| `ds research 「主題」` | 找有沒有類似的開源專案 |
| `ds research --list` | 搜尋紀錄與連結 |
| `ds stream` | 即時事件串流（給 Monitor 用） |
| `ds pause 30` / `ds resume` | 暫停 / 恢復記錄 |
| `ds setup [claude\|codex\|gemini] [--remove]` | 接上 / 拆掉 AI 工具 |

## 設定

`config.json` 只放你要改的部分（第一次執行會自動建立，不會進 git），隱私清單會「加」在預設上：

```json
{
  "language": "zh",
  "capture": { "ocr_language": "zh-Hant-TW" },
  "privacy": {
    "blocked_title_regex": ["客戶專案名", "薪資"],
    "no_capture_apps": ["MyGame.exe"]
  },
  "file_watch": { "roots": ["C:\\Users\\you\\projects"] },
  "analyzer": { "user_profile": "全端工程師，Next.js + Supabase，要簡短具體的修法" }
}
```

## 完全本機模式（Ollama / LM Studio）

> **Beta**：照 Ollama 與 OpenAI 相容 API 的官方格式實作、有測試覆蓋，但還沒在很多環境實際跑過，歡迎回報問題。

連截圖都不想離開電腦？把 AI 分析改用本地的視覺模型：

1. 安裝 [Ollama](https://ollama.com)，下載一個看得懂圖的模型：`ollama pull gemma3:4b`（約 3 GB；顯卡 8 GB 以上可用 `qwen2.5vl:7b`，看螢幕更準）
2. `config.json` 加上：
   ```json
   { "analyzer": { "backend": "ollama", "local": { "model": "gemma3:4b" } } }
   ```
3. `ds restart`，再用 `ds status` 確認

LM Studio、llama.cpp、vLLM 用 `"backend": "openai"`（OpenAI 相容伺服器，預設 `http://127.0.0.1:1234/v1`）。本機模式下分析不花錢、資料完全不出電腦；自動上網搜尋因為一定要連網，會自動關閉。錯誤偵測和文字辨識在任何模式都是本機跑。

## 費用

- 背景分析：Haiku 一次約 US$0.002，有在用電腦時每 20 分鐘一次、遇到錯誤會多跑，每小時上限 12 次，一天大約幾美分。
- 自動搜尋：Sonnet + 網路搜尋，只在同一個錯誤重複出現、分析判斷你卡住、或開始做新東西時觸發，一天最多 8 次、每次上限 US$0.5。
- 用 Claude 訂閱的話，是扣訂閱額度，不另外收費。
- 完全本機模式：$0。
- 我們實測一整天正常使用：分析 15 次 + 搜尋 2 次 ≈ US$0.19。

## 支援哪些語言？

- **任何語言的 Windows 都能用**，AI 也看得懂任何語言的視窗標題與畫面文字。
- **介面**（指令輸出、通知、分析）：繁體中文與英文，依 Windows 語言自動選（`"language": "zh"` / `"en"` 可強制）。
- **OCR**：你的 Windows 有裝的 OCR 語言都行（設定 → 時間與語言 → 語言；約 25 種，含中、英、日、韓與多數歐洲語言），可用 `capture.ocr_language` 指定。
- **內建隱私關鍵字**：繁中、簡中、英、日、韓、西、法、德、葡文；其他語言可在 `config.json` 自己加。

## 移除

```powershell
powershell -ExecutionPolicy Bypass -File uninstall.ps1          # 保留資料
powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Purge   # 連資料、設定、.venv 一起刪
```

## 授權

[MIT](LICENSE) © 2026 [AngleTech](https://angletech.io)
