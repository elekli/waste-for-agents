# TODOS — waste-for-agents

MVP 刻意延後的項目。寫下來才算數(ENGINEERING Prime Directive 7)。

## 安全 / 濫用面(開放給可信任 tester 以外前必處理)

- [x] **API key 認證 + rate limit(THE-10)。** create/list/delete/replay 需 Bearer key;
      issue_key 自助發 free key(只存 hash);per-key rate limit;watch 歸戶 + per-caller scope
      (list_watches/list_changes 只見自己的)+ replay/delete ownership。**匿名濫用已擋。**
- [x] **SSRF 防護(THE-10)。** scheme allowlist + 內網/metadata 阻擋 + redirect 逐跳重驗 +
      出站 header allowlist,套用 rss/discovery/http_json。
- [ ] **create_watch 的 query 仍未驗證(raw SQL)。** auth 後仍是持 key 者的「持久排程 raw-SQL
      primitive」:query 原樣透傳 Twinkle `query_rows`。需:query 驗證(限 column-op-value
      結構化過濾、拒 raw SQL)、interval 下限、watch 數量上限。參考 Twinkle `query_rows`
      docstring:「對外暴露時 gateway 應只接受結構化過濾」。
- [ ] **`/health` 未授權**(只回 watch 數;`/changes` 已支援 Bearer 選填 + per-caller scope)。
      bind 非 loopback 時 `/health` 需放 Tailscale / reverse-proxy auth 後。
- [ ] **DNS-rebinding(SSRF 殘留)。** netguard check 解析的 IP 與 httpx 實連 IP 可能不同(TOCTOU)。
      上線前以「pin 解析 IP 後用該 IP 連線」收尾。MVP 接受(feed 規模小、polls 稀疏)。
- [ ] **issue_key 無限流(review)。** 未認證端點,可洪水發 free key → api_keys 列無界 +
      RateLimiter `_hits` 隨 distinct key 成長。需:per-IP rate limit(取 X-Forwarded-For / client IP)
      或全域發放速率 + key 數上限 + 閒置 key TTL 回收。MVP 預設 bind loopback,延後。
- [ ] **錯誤訊息的 token 防護目前靠 _scrub + 不在 message 帶 headers。** uvicorn 若記錄
      完整 traceback,__cause__(httpx 錯誤)理論上仍可能含 request 細節。確認 httpx 不在
      exception repr 帶 Authorization;必要時關閉 `from exc` 或自訂 log filter。

## 正確性 / 韌性

- [ ] **key 欄位缺失 → row 併桶漏報。** 若 fetched row 缺某 key column,`row_key` 給 None,
      多個缺 key 的 row 會 collapse 成同一桶(`_index` 後者覆蓋),真實 distinct row 消失 →
      under-report。需:create_watch 時驗證 key_columns ⊆ query 投影,或執行期偵測缺 key 並 fail-loud。
- [ ] **naive datetime 風險。** `_is_due` 用 `fromisoformat(last_run_at).timestamp()`。目前所有
      寫入皆 UTC-aware;未來來源若寫 naive iso,timestamp() 會當本地時間,interval gating 失準。
      需:正規化或斷言 aware。
- [ ] **change_events 無界成長。** 已加 watch_id 索引,但長壽 watch 的事件永久累積。需:保留策略
      (TTL / 上限)或已讀清理。
- [ ] **watch 靜默失敗(可觀測性缺口,Prime Directive 1/5)。** fetch 持續失敗時 `mark_run` 只更新
      `last_run_at`/`last_error`、故意不動 snapshot(對——不該用錯誤狀態蓋好資料),但對外沒有任何訊號:
      agent 呼叫 `list_changes` 只是拿不到新事件,不知道 watch 已壞、window 凍在最後一次成功。需:在
      watch status / list_changes 回傳曝 `last_error` + `consecutive_failures` + `last_success_at`,
      讓靜默過期變可見。**實例來源:** MacBook dogfood 的 HN watch 因 laptop sleep 後進程 resolver
      stale,自 05:57 起持續 `RssFetchError: 無法解析 host`(同機 curl 正常),window 凍結卻無感。
- [ ] **`getaddrinfo` 阻塞 event loop(潛在並行 bug)。** `netguard.check_outbound_url`(`netguard.py:65`)
      用同步 `socket.getaddrinfo`,未包 `asyncio.to_thread`,跑在 scheduler loop thread 上。真實網路中斷時
      一個 watch 的 DNS 解析會卡住**整條 loop**(所有 watch)直到 resolver timeout。watch 量小無感,量大會痛。

## v2(產品方向,非 bug)

### feed 「當前狀態」讀取面 + 排名(2026-07-04 討論)

**價值切成兩塊,分開做:通用的先做,窄的當 opt-in。**

- [ ] **「讀當前狀態」讀取面(通用,← 現在想先做的)。** 對**任何** feed 都有用:agent 想要「當前完整集」
      而非只有 diff。`list_changes` 天生答不了「現在整體長什麼樣」(它回差異不回快照)。缺的不是儲存——
      `get_snapshot`(`store.py:347`,回整份 `list[Row]`)內部已存在、只是沒掛成 `@mcp.tool()`。需:把它
      包成對外 tool(如 `get_state` / `list_items`),並確認排名型 feed 走 **dataset 模式**(snapshot = 當前
      完整集)而非 RSS 預設的 rolling_window(累積聯集、順序已丟)。
- [ ] **position 進 ignore_columns(要做)。** 一旦 row 帶 `position`,diff 的 `_compare` 會讓每次名次
      洗牌炸出 modified 洪水;把 `position` 列進 watch 的 ignore_columns(`diff.py:53` 的 `- ignore`)剔除。
      語意正確:**position 是狀態不是事件**,不該進 change stream。
- [ ] **position 當排名(窄,per-source opt-in)。** 只對「item 順序即排名」的 feed 有意義(HN/Reddit hot/
      PH/GitHub trending);時序型 feed 的 position ≈ recency,與 `published` 冗餘。**排名不以欄位暴露,只以
      item 文件順序暴露**(RSS/Atom 無 `<rank>`,無規範保證順序=名次)。做法:`_entry_to_row` 用 `enumerate`
      寫入 `position`(schema-less 儲存,無 migration);當 per-watch opt-in,別當通用功能。**注意順序在
      `_index`(`diff.py:46`)轉 dict 時已丟,position 要靠 row 內欄位自帶、不能靠 snapshot list 排列。**
      **已驗(2026-07-04,單次):** 抓 `news.ycombinator.com/rss` 對首頁,item 順序與名次 1–12 逐項一致;
      但這是單次觀測 + 只驗 HN,每個來源用前都該各自對、且值得排程性複驗(HN 隨時可改)。
- [ ] **7 欄投影對每個 feed 有損(獨立於排名)。** `_entry_to_row`(`rss.py:66`)硬取 7 欄,feedparser
      解出的其餘(`media:*`、`dc:*`、自訂 namespace 等擴充欄位)一律丟棄。RSS item 欄位本由發行者自訂
      (RSS 2.0 幾乎全 optional、Atom 僅強制 id/title/updated),若某來源價值藏在擴充欄位會被直接扔掉。
      要不要保留更多欄位(甚至收 feedparser 全部走 schema-less)是獨立決定。


- [ ] **The Underground Route(webhook push adapter)。** README 信封 header 的真正實作,給能收
      webhook 的非 agent 消費者。
- [ ] **agent「持續接收」inbox 模型研究(R1)。** 見設計文件 open questions:loop poll 頻率/成本、
      去重與已讀游標、跨 session 記憶接續。決定 v2 形狀。
