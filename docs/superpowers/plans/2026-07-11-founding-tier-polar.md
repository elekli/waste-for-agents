# Founding tier × Polar 金流串接（feat/founding-tier）

**日期:** 2026-07-11 · **Branch:** `feat/founding-tier` · **Worktree:** `~/waste-for-agents-founding`
**前情:** elek 拍板「付費先於 Show HN」。賣的單位 = 卡號在檔創始名單:Polar 訂閱 **$5/月 + 3 個月 trial**(trial 可經 API `PATCH /v1/subscriptions/{id}` `trial_end` 逐一延長,承諾「hosted 沒出貨不扣款」可兌現)。sandbox product/checkout link 已建(IDs 在 `~/.claude/.polar-sandbox.env`,不進 repo)。

## Scope

1. **`billing_polar.py`** — Polar webhook 接收:standard-webhooks 驗簽 + 訂閱生命週期記錄 + 計費 tier 翻轉。
2. **`store.py`** — 新表 `billing_subscriptions`(additive,無 migration 風險)。
3. **`server.py`** — `build_app` 內 env-gated 掛 `POST /billing/polar/webhook`(`POLAR_WEBHOOK_SECRET` 未設 → 路由不存在)。
4. **CLI `bind-subscription`** — 把 Polar subscription 綁到某 api_key(Phase 0 人工發 key 後綁定用)。
5. **落地頁 pricing 區塊**(EN only)+ GoatCounter 事件。
6. **`docs/launch/show-hn.md`** — business model 預答對齊新事實 + 補「vs webhook inbox」預答。

## Non-goals(明寫,防兔子洞)

- **不部署** hosted 實例;webhook handler 測綠即止(live 投遞驗證等部署後)。
- **不自動發 key 給訂閱者**。理由:repo 鐵律「key 只存 hash、明文只回一次」(`auth.py:37`),webhook 時點無人接收明文 → 自動發 = 得存明文,違反設計。Phase 0 發 key 是人工回信,webhook 只記名單 + 綁定後翻 tier。
- 不動 pt/ja/ko 落地頁(EN 是唯一漏斗,carried decision)。
- 不做 Polar License Keys benefit(會換掉自有 key 體系,是 Phase 1 之後的獨立決策)。
- 不碰 TODOS 既有安全項(query 驗證等,另一條線)。

## 設計

### 資料流

```
Polar (sandbox/prod)
   │ POST /billing/polar/webhook   (standard-webhooks 簽章)
   ▼
verify_signature(secret, headers, raw_body)     ← billing_polar.py(純函式)
   │ 驗過才 parse
   ▼
handle_event(store, event)                       ← billing_polar.py(純函式)
   │  subscription.{created,active,updated,uncanceled}
   │      → upsert billing_subscriptions(status=…)
   │      → 若已綁 api_key_id:set_api_key_tier(kid,"paid")
   │  subscription.{canceled}      → 記錄(cancel_at_period_end,tier 不動)
   │  subscription.{revoked}       → status=revoked;若已綁:tier→"free"
   │  其他 event type              → 記 log、回 202(不失敗)
   ▼
billing_subscriptions(subscription_id PK, customer_id, customer_email,
                      status, api_key_id NULL, created_at, updated_at)
                                    ↑
CLI: waste-for-agents bind-subscription <sub_id> <api_key_id>
     → 寫 api_key_id + 立即按 status 翻 tier
```

### 決策與理由

- **驗簽用 `standard-webhooks` 套件**(Svix 官方參考實作;Polar 即此規格)。不手刻:規格有 timestamp 容忍、多簽章空白分隔、`whsec_` base64 解碼等細節,hand-roll 易錯。加進 `pyproject.toml` runtime deps。
- **驗簽失敗回 401、驗過但事件不認識回 202**——webhook 端點對未知事件必須寬容(Polar 之後加事件型別不能讓我們 500 重試風暴),但簽章錯絕不處理。
- **冪等**:以 `subscription_id` 為 PK upsert;同事件重送(webhook at-least-once)結果一致。tier 翻轉冪等(set 同值無害)。
- **`canceled` ≠ `revoked`**:canceled 是「本期末不續」(trial/付費期內服務照跑,tier 不動);revoked 才是權益終止。對照 Polar 事件語意,寫測試釘死。
- **env-gating 慣例**:沿 `WASTE_UNMETERED` 的 `_env_truthy` 風格,但這裡是「`POLAR_WEBHOOK_SECRET` 有值才掛路由」;沒設時 `POST /billing/polar/webhook` 404,零攻擊面。
- **raw body 先驗簽再 parse**(簽章對 raw bytes;先 json.loads 再 re-serialize 會炸簽)。

## 檔案

| 檔案 | 動作 |
|---|---|
| `src/waste_for_agents/billing_polar.py` | 新增:`verify_webhook()`、`handle_event()`、事件型別常數 |
| `src/waste_for_agents/store.py` | 新表 DDL + `upsert_billing_subscription()`、`get_billing_subscription()`、`bind_subscription_key()`(reuse `set_api_key_tier` `store.py:456`) |
| `src/waste_for_agents/server.py` | `build_app` 掛 route(仿 `/changes` `server.py:393` 模式) |
| `src/waste_for_agents/__main__.py` | `bind-subscription` 子命令(仿 `issue-key` `__main__.py:67`) |
| `pyproject.toml` | + `standard-webhooks` |
| `tests/test_billing_polar.py` | 新增(見驗證) |
| `docs/index.html`(+ `docs/assets/`) | pricing 區塊 + GoatCounter click 事件 |
| `docs/launch/show-hn.md` | FAQ 兩條 |

## 驗證(R-2 / R-5)

1. **單元/整合測試(TDD,先紅後綠):**
   - 驗簽:合法簽章過;壞簽章 401;過期 timestamp 401;缺 header 401。
   - 事件流:created(trialing)→ 名單有記錄;bind 後 active → tier=paid;revoked → tier=free;canceled → tier 不動;未知事件 → 202;重送同事件 → 冪等。
   - env-gating:無 secret → 404;有 secret → route 存在。
   - 測試用 `standard-webhooks` 的 signer 自簽 payload + `TestClient`(仿 `tests/test_server.py:72`)。
2. **全套既有測試** `uv run pytest -q` 綠(基線 186+7)+ `uv run mypy` + `uv run ruff check` 乾淨。
3. **落地頁**:實際瀏覽器 render(含 mobile 390px),checkout 連結真的可點到 Polar sandbox checkout。
4. **sandbox E2E(task #6)**:測試卡 4242 走 checkout → 本機 handler 收 webhook(自簽重放或 tunnel)→ bind → tier 翻 paid → Polar 取消 → revoked → tier 翻 free。
5. **multi-review** `--mode code`(repo 慣例,PR 前)。

## 風險

- **Polar 事件 payload 形狀憑文件非實測** → E2E 時抓 sandbox 真 payload 存 fixture 校正(測試裡 fixture 標注來源)。
- **KYC 未過件**(production 側 blocker)→ 全部工作在 sandbox 可完成;若 KYC 敗走 Dodo,`billing_polar.py` 驗簽層報廢但名單表/tier 翻轉/CLI 可留(Dodo 也是 webhook + 訂閱事件,handler 換 adapter)。
- **checkout URL 換手**:落地頁先用 sandbox URL 驗版型,**production URL 換上前不 merge 進 main 的 GitHub Pages**(sandbox 連結上線=真用戶刷到假結帳)。PR 保持 draft 至換手完成。
