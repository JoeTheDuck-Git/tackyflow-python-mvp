# Content Workflow — Python Edition

這是 TackyFlow 的 Python AI 後端。Python 是 AI workflow、知識擷取、向量檢索與 agent orchestration 的唯一主系統；既有 TypeScript 程式只保留 UI 與過渡期 API，不再維護第二套 AI workflow。目標是把使用者可見流程從多個人工工作站收斂成四個階段，並讓 AI／sub-agent 在背景完成研究、驗證、撰寫、品質檢查與製作規劃。

目前版本是可執行的本機垂直切片，使用 SQLite 持久化工作流、內容機會、版本、使用事件與回饋，並以 deterministic agent 跑通完整流程。內容生成可維持離線規則模式，或切換到單一 OpenAI provider；即時趨勢資料仍未串接，介面不會把規則適配分偽裝成搜尋量。它用來固定 API、workflow 與 human-on-exception 契約，不會改動舊系統。已建立的任務與探索紀錄預設保存在 `data/workflows.db`；尚未建立任務的表單草稿則按 workspace 與來源版本保存在目前瀏覽器。一般重新整理後都可還原，但清除瀏覽器網站資料會移除尚未送出的草稿。

## 四階段流程

1. `requirements`：需求設定與自動路由
2. `ai_creation`：研究、驗證、撰寫與品質迴圈
3. `production_package`：分鏡、視覺、B-roll 與發布素材
4. `approval_publish`：人工最終核准

高風險或低信心結果會進入 `waiting_for_human`，一般中間步驟不要求人工逐一確認。

## 啟動

需要 Python 3.12+ 與 [uv](https://docs.astral.sh/uv/)：

```bash
cd "20260922 Content Workflow_Python"
cp .env.example .env
# 在 .env 填入本機資料庫、Supabase 與 AI provider 憑證
uv sync
uv run python -m app
```

這個 Python 專案只讀取本資料夾的 `.env`，不再向上讀取舊版
Node／React 專案的環境檔。`.env` 是本機唯一設定來源且已被 Git 忽略；
`.env.example` 只保存可提交的欄位範本。Vercel 部署仍以 Dashboard 的
Environment Variables 為準，而 `.env.local` 僅供 Vercel CLI 管理短效
OIDC token，不應把人工維護的 API key 寫入其中。

開發時需要自動重載，也可以使用：

```bash
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

穩定入口可用 `CONTENT_WORKFLOW_HOST`、`CONTENT_WORKFLOW_PORT` 調整監聽位址與連接埠。介面每 15 秒檢查一次 `/health`；服務中斷時會保留已載入畫面並提供重新連線，不會把網路錯誤誤報成生成失敗。

開啟：

- 繁體中文工作台：<http://127.0.0.1:8000/>
- API 文件：<http://127.0.0.1:8000/docs>
- Health check：<http://127.0.0.1:8000/health>

## 登入與工作區

預設 `AUTH_MODE=session`。第一次開啟時，登入畫面會要求建立第一位管理員；這個操作只在資料庫完全沒有帳號時開放，完成後會同時建立 `default` 工作區，因此既有本機任務仍可繼續使用。

登入後可從左下角切換所屬工作區；owner／admin 可在「系統設定」新增或移除成員，owner 可調整 admin／member 角色。Session 使用 HttpOnly、SameSite=Strict Cookie，production 模式會加 Secure；所有修改資料的 API 另要求 CSRF token。

本機自動化測試若需要保留舊版 Header 契約，可明確設定 `AUTH_MODE=local`；正式環境禁止使用 local 模式。Session 時限可用 `SESSION_TTL_HOURS` 與 `SESSION_IDLE_MINUTES` 調整。

後續可靠性、成本、版本評估與回饋匯出順序整理於 [MVP Roadmap](docs/MVP_ROADMAP.md)。

## 啟用 OpenAI 內容生成

程式在未設定環境變數時會安全退回 `local_rule`；目前 MVP 的 `.env.example` 則以 OpenAI 為優先。請只在伺服器環境設定下列值，不要把 key 寫進前端、資料庫或提交到 Git：

```bash
export CONTENT_PROVIDER=openai
export OPENAI_API_KEY="你的伺服器端 API key"
export OPENAI_MODEL=gpt-5.6-sol
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

OpenAI provider 使用 Responses API 的結構化輸出，將回應解析成標題、摘要、完整段落與畫面方向；模型、response ID 與 token usage 會保存為生成 metadata，但 API key 不會保存。正式發布前仍必須由人工確認事實、品牌語氣與權利風險。

生成與濫用保護可用環境變數調整：

- `DAILY_GENERATION_LIMIT`：每個 workspace 每日可啟動的內容生成次數，預設 30。
- `REQUESTS_PER_MINUTE`：同一網路來源的每分鐘 API 請求數，預設 300。
- `OPENAI_TIMEOUT_SECONDS`：OpenAI 請求逾時秒數，預設 90。
- `OPENAI_PRODUCTION_TIMEOUT_SECONDS`：分鏡、B-roll 與發布素材規劃的專用逾時，預設 180；此階段會對暫時性連線錯誤進行受控 SDK 重試。

目前每分鐘限制是單機記憶體實作；部署成多個 instance 前要換成 Redis、API Gateway 或平台原生 rate limit。每日額度與回饋則已持久化在資料庫並按 workspace 隔離。

## Crawl4AI、LlamaIndex、pgvector 與 LangGraph

內容機會的新知識鏈路由 Python 管理：Crawl4AI 擷取公開網頁，LlamaIndex 切分文件並建立 OpenAI embedding，pgvector 按 workspace 保存及檢索，最後由 LangGraph 決定使用知識型結果或舊規則 fallback。外部網址會先做 SSRF 檢查；知識表啟用 RLS，且不開放給 PostgREST 前端直接存取。

第一次部署 PostgreSQL 時先套用 migration：

```bash
uv run python -m app.db.migrations
```

本機安裝 Crawl4AI 瀏覽器 runtime：

```bash
CRAWL4_AI_BASE_DIRECTORY="$PWD/data" uv run crawl4ai-setup
```

正式切換採漸進式 A/B，而不是一次取代舊規則：

```bash
# 1. 預設安全模式：完全使用舊代理
OPPORTUNITY_PIPELINE_MODE=legacy
OPPORTUNITY_KNOWLEDGE_ROLLOUT_PERCENT=0

# 2. Shadow / 小流量驗證：依 workspace + topic 固定分桶
OPPORTUNITY_PIPELINE_MODE=knowledge_shadow
OPPORTUNITY_KNOWLEDGE_ROLLOUT_PERCENT=5

# 3. 指標通過後逐步調至 25、50、100，最後再改為正式主路徑
OPPORTUNITY_PIPELINE_MODE=knowledge_primary
OPPORTUNITY_KNOWLEDGE_ROLLOUT_PERCENT=100
```

任何 Crawl4AI、embedding、pgvector 或知識生成錯誤都會回到既有內容機會代理；若既有 OpenAI 代理也失敗，才使用 deterministic 本機規則。開發環境若使用 Clash fake-IP DNS，可明確設定 `CRAWL4AI_ALLOW_CLASH_FAKE_IP=true`；正式環境不要開啟。

整合 smoke test（會自動刪除測試資料）：

```bash
uv run python scripts/smoke_knowledge.py
```

## 最小操作流程

產生動態內容機會：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/opportunities/generate \
  -H 'Content-Type: application/json' \
  -H 'X-Workspace-ID: default' \
  -d '{
    "topic": "大疆",
    "platforms": ["youtube", "instagram"],
    "goal": "education",
    "count": 4
  }'
```

內容機會由可離線運作的 Python 策略代理產生，會辨識主題類型、參考同一 workspace 的既有任務降低重複，先評估候選集，再依相關性、新穎度、受眾價值與製作可行性排序。每個結果包含完整 Brief、資料信心、必要證據、模型／規則版本與來源時間。這個「內容適配分」不代表搜尋量或即時市場趨勢；日後可透過相同 provider 契約替換成 LLM 與外部 signal 來源。

內容機會支援：

- 永久探索紀錄與 `?page=opportunities&generation=<id>` 深連結
- 收藏、略過、恢復與採用狀態
- 2–3 題固定比較區
- 單題重做，建立保留父版本的 immutable child generation
- 單題重做以父版本與 item ID 區分快取；同題重送維持冪等，不同題不會誤用同一 child
- 將受眾、平台、目標、Brief、限制、參考邊界與來源 ID 完整帶入 AI 工作台
- 任務建立前的工作台輸入會按 workspace、來源 generation、item 與 revision 暫存在瀏覽器；刷新後可接續，建立成功即清除
- 相同 generation + opportunity 重複建立工作流時回傳既有工作流，避免重複任務
- 工作流已建立但「已採用」狀態同步失敗時會保存補償紀錄，介面可直接重試，不必重建任務

主要內容機會 API：

```text
POST  /api/v1/opportunities/generate
GET   /api/v1/opportunities?workspace_id=default  # 並帶 X-Workspace-ID
GET   /api/v1/opportunities/{generation_id}
PATCH /api/v1/opportunities/{generation_id}/items/{item_id}
POST  /api/v1/opportunities/{generation_id}/items/{item_id}/regenerate
```

建立 workflow：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/workflows \
  -H 'Content-Type: application/json' \
  -H 'X-Workspace-ID: default' \
  -d '{
    "topic": "AI 如何改變內容行銷",
    "goal": "education",
    "platforms": ["youtube"],
    "output_type": "short_video",
    "brand_voice": "專業但自然"
  }'
```

持續執行至完成或需要人工介入：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/workflows/WORKFLOW_ID/run
```

最終人工核准：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/workflows/WORKFLOW_ID/decisions \
  -H 'Content-Type: application/json' \
  -d '{"approved": true, "note": "核准發布"}'
```

管理內容計畫的階段：

```bash
curl -X PATCH http://127.0.0.1:8000/api/v1/workflows/WORKFLOW_ID/stage \
  -H 'Content-Type: application/json' \
  -d '{"target_stage": "ai_creation", "note": "需要重新製作腳本"}'
```

向前調整時會自動補跑缺少的 Pipeline；退回時會清除目標階段之後的舊成果，但保留 AI 執行紀錄。介面刪除任務前會再次確認，刪除後會連同需求、AI 產出與紀錄永久移除；任何指向該任務的內容機會會回復為「已收藏」，不會留下失效的「已採用」連結。

目標字數支援 200–5,000 字。本機 Writer 會將短影音、長影音、串文與長文稿擴寫到接近目標，完整逐字稿會如實顯示實際差異；若必要 Brief 本身已超過目標，系統會優先保留必要內容。Agent 或 artifact provider 發生例外時，任務會保存為 `failed`，執行紀錄只保留安全摘要與錯誤類型，完整例外留在伺服器 log；修復服務後可在介面從失敗階段重試。

## 測試

```bash
uv run pytest
```

## 回饋與使用事件

成果預覽可送出「有幫助／需要改善」與選填備註。事件 API 只接受固定 allowlist，目前記錄頁面瀏覽、任務開啟與腳本複製；前端不會把腳本、品牌 Brief 或參考資料放進分析事件。

```text
GET  /api/v1/usage
POST /api/v1/events
POST /api/v1/workflows/{workflow_id}/feedback
```

## 本機資料保存

- 預設資料庫：`data/workflows.db`
- 保存內容：需求輸入、參考資料、AI 產出、執行紀錄、審核狀態
- 尚未建立任務的工作台草稿：目前瀏覽器網站儲存空間（依 workspace 與來源版本隔離）
- 內容機會另保存：完整生成需求、候選結果、收藏／略過／採用、版本鏈、來源訊號與失敗資訊
- 可透過 `DATABASE_PATH` 環境變數指定其他 SQLite 路徑
- 可透過 `OPPORTUNITY_PROVIDER=local_rule|openai` 選擇內容機會供應者；`openai` 先以低搜尋脈絡的 Web Search 建立研究摘要，再以 Structured Outputs 產生題目，並保存引用、信心與兩段 Token 使用紀錄
- `OPPORTUNITY_SIGNAL_PROVIDER=local_manual` 負責保存使用者提供的 Brief、參考文字與網址；OpenAI 代理取得的網路引用會另外標記為即時訊號
- 可透過 `CONTENT_PROVIDER=local_rule|openai` 選擇唯一內容生成 provider；`openai` 模式由伺服器環境讀取 `OPENAI_API_KEY`
- 可透過 `WORKFLOW_AGENT_PROVIDER=local_rule|openai` 選擇工作台代理；`openai` 會啟用研究、條件式驗證、品質主編與製作規劃，且每次呼叫都記錄用量與 response ID
- 工作台的一個任務只占用一次每日生成額度；任務內各代理的 Token 與事件仍分開計量
- `data/*.db*` 已排除在 Git 版本控制之外，避免把本機內容誤提交到儲存庫

## Workspace 邊界

所有會讀寫持久資料的 API 都會先從伺服器端 Session 取得使用者與目前 Workspace，再核對 `X-Workspace-ID`；Body 若明確傳入不同的 `workspace_id` 會回 `422`，跨 workspace 的讀取或修改會拒絕。SQLite 的 workflow 與 opportunity 查詢也會直接按 workspace 篩選，而不是只靠前端隱藏。

`X-Workspace-ID` 只是前端表達目前選擇的工作區，不能自行授權；後端會用 Session membership 驗證。正式接到外部身分平台後，可改由平台 principal／JWT 推導使用者與 membership，而不需修改 workflow repository 契約。

從內容機會建立任務時，兩個來源 ID 必須成對出現，後端也會驗證 generation、item 與 workspace。相同來源的並行重送會回傳同一任務；若同一來源帶入不同需求，會回 `409`，避免靜默覆蓋。

## 下一階段

完整 AI 邊界、代理改造順序與驗收條件見 [`docs/AI_INTEGRATION_MAP.md`](docs/AI_INTEGRATION_MAP.md)。

- PostgreSQL／SQLAlchemy repositories
- Supabase／其他平台 JWT 與 membership 驗證，取代本機 workspace Header resolver
- OpenAPI TypeScript client
- 真實 Research、Verification、Writer、Critic provider
- PostgreSQL job queue、inbox/outbox 與 idempotency
- 舊 tRPC endpoint 的 contract/parity tests
- React 前端逐頁切換至 `/api/v1`
