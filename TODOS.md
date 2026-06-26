# TODOS — waste-for-agents

MVP 刻意延後的項目。寫下來才算數(ENGINEERING Prime Directive 7)。

## 安全 / 濫用面(開放給可信任 tester 以外前必處理)

- [ ] **create_watch = 持久排程 raw-SQL primitive。** query 原樣透傳 Twinkle `query_rows`
      (接受 raw SQL),借用維運者 token 無限期重跑。需:query 驗證(限 column-op-value
      結構化過濾、拒 raw SQL)、interval 下限、每來源/每呼叫者 rate-limit、watch 數量上限。
      參考 Twinkle `query_rows` docstring 自己的警告:「對外暴露時 gateway 應只接受結構化過濾」。
- [ ] **`/changes` 與 `/health` 未授權。** 預設 bind 127.0.0.1。README 的「維運者代管」建議
      若 bind 非 loopback,這兩個端點對外開放(洩漏所有被監看 rows + watch 數)。需:放在
      Tailscale / reverse-proxy auth 後,或加應用層 token。
- [ ] **source 白名單只是「serve() 剛好只註冊 twinkle」。** 開放任意來源時要真正的 allowlist +
      SSRF 防護(禁私網/metadata endpoint)。
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

## v2(產品方向,非 bug)

- [ ] **The Underground Route(webhook push adapter)。** README 信封 header 的真正實作,給能收
      webhook 的非 agent 消費者。
- [ ] **agent「持續接收」inbox 模型研究(R1)。** 見設計文件 open questions:loop poll 頻率/成本、
      去重與已讀游標、跨 session 記憶接續。決定 v2 形狀。
