# TackyFlow MVP 開發順序

這份清單把正式試用前的五項能力排成依賴順序。原則是先建立身分與資料邊界，再處理執行可靠性、成本、品質與回饋分析；不以增加頁面為目標，而是嵌入現有工作台、系統設定與成效回顧。

## 1. 登入與 Workspace membership — 已完成 MVP

已包含：

- 一次性的首位管理員初始化。
- scrypt 密碼雜湊與個別 salt，資料庫不保存明文密碼。
- 隨機 Session token；資料庫只保存 token hash。
- HttpOnly、SameSite=Strict Cookie；production 模式加 Secure。
- CSRF token 保護所有登入後的資料修改 API。
- 12 小時絕對期限、120 分鐘閒置期限，均可用環境變數調整。
- 登入失敗次數限制：同一帳號識別值 15 分鐘內最多 5 次。
- Workspace 建立、切換與 owner／admin／member 角色。
- 管理員新增與移除成員；owner 調整角色。
- 使用者可變更自己的密碼，並撤銷其他裝置的 Session。
- API 每次在伺服器端驗證 Session、目前 Workspace 與 membership。

正式公開前仍需：忘記密碼／信箱驗證、邀請信、MFA、集中式 Session store 與安全事件告警。這些應交由正式身分供應商處理，不建議自行延伸密碼系統。

## 2. 背景任務、重試與逾時 — 下一個優先項

目的：LLM 或外部服務變慢時，HTTP 請求不會卡住，使用者重新整理後仍能看到進度。

建議實作：

- 將 generation 變成具唯一 ID 的 durable job。
- 狀態：queued、running、waiting、completed、failed、cancelled。
- 每個步驟保存 checkpoint，重試從失敗步驟繼續。
- 指數退避、最大重試次數、provider timeout 與 idempotency key。
- 同一 Workspace 的併發數與排隊上限。
- 工作台沿用現有執行紀錄顯示狀態，不新增頁面。

驗收：中斷服務後重新啟動，任務不遺失、不重複扣額度，也不重複產生成果。

## 3. 成本警示與額度管理

依賴：第 2 項的穩定 generation/job ID。

建議實作：

- 保存 input/output/cached token 與估算成本。
- Workspace 日／月額度及軟、硬上限。
- 80% 顯示警示，100% 阻擋新生成，但不阻擋查看既有成果。
- 管理員可調整額度；所有變更寫入 audit log。
- 系統設定顯示用量，工作總覽只顯示需要處理的告警。

驗收：provider 回應失敗、重試或取消時，用量不會重複計費；管理員能追溯每次額度變更。

## 4. Prompt／模型版本與品質比較

依賴：第 2 項 generation ID 與第 3 項成本紀錄。

建議實作：

- 每次生成固定保存 provider、model、prompt template version、參數與輸出 schema version。
- 建立一組去識別化的測試題與人工評分準則。
- 比較字數遵循、結構完整、事實風險、人工修改量、使用者回饋與成本。
- 新版本先 shadow／小流量測試，再成為預設版本。

驗收：任何成果都能回答「由哪個模型與哪版 prompt 生成」，而且可以安全回退。

## 5. 回饋匯出與產品學習

依賴：第 4 項版本 metadata，否則回饋無法連回模型與 prompt。

建議實作：

- 依日期、Workspace、評價、模型與 prompt version 匯出 CSV／JSON。
- 預設不包含完整腳本、品牌 Brief 或參考資料。
- 匯出前顯示欄位與筆數，並記錄操作者與時間。
- 提供刪除／保留期限政策，支援使用者資料請求。

驗收：AI 團隊能用匯出資料找出低評分版本，同時不把客戶內容混入訓練或分析資料。

## 建議 Release Gate

- 封閉式內部試用：第 1 項完成即可。
- 受邀外部 MVP：第 1、2、3 項完成，並切 PostgreSQL／HTTPS。
- 擴大試用與調整模型：完成第 4、5 項。
