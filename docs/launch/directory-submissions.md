# 目錄提交素材 — posthorn (waste-for-agents)

> 這份是 MCP 生態**目錄類**的提交操作手冊。目錄 = 常青複利(一次提交、長期被搜到),與一次性貼文(Show HN/Reddit,見 `show-hn.md`)分開。
>
> **提交順序(兩份獨立調查都把目錄群排第一,理由:來訪者已經在找 MCP server,零 spam 風險):**
> 1. 官方 MCP Registry(**上游**——PulseMCP 等下游會自動抓,先做這個)
> 2. punkpeye/awesome-mcp-servers(PR,~9萬星,最大複利)
> 3. PulseMCP(多半承接 Registry 自動收錄,或補 submit 表單)
> 4. Glama / mcp.so(claim / submit)
> 5. Smithery(⚠ 需公開 endpoint,posthorn 無 hosted——見下方限制)

---

## 共用文案素材(所有目錄共用,改一處這裡改)

- **Name(product):** posthorn
- **Name(package/repo):** waste-for-agents
- **Repo:** https://github.com/elekli/waste-for-agents
- **Registry name(反轉網域式):** `io.github.elekli/waste-for-agents`
- **Language / stack:** Python 3.12, MCP (streamable HTTP transport)
- **Contact:** getposthorn@pm.me

**Tagline(一句,≤120 chars):**
> A pull-first change feed for AI agents — watch RSS/feeds/APIs, get a true row-level diff via the `list_changes` MCP tool.

**Short description(2–3 句,目錄簡介欄用):**
> posthorn watches structured sources (RSS/Atom feeds, JSON APIs, datasets) on a schedule and keeps a true, row-level diff that ignores timestamp/serial churn. Your agent drains only the real changes with one call — `list_changes` as an MCP tool, or `GET /changes` as an HTTP mirror — holding an independent cursor per source. Self-hostable; no hosted instance yet.

**分類提示:** 各目錄分類名不同,posthorn 最貼近的類別是 **Monitoring / Aggregators / Data & feeds** 一類(它「盯來源、聚合變化」)。提交時挑最接近的既有分類,別自創。

---

## 1. 官方 MCP Registry(registry.modelcontextprotocol.io)

**卡點已解(讀 registry 原始碼查證):** 二手說法「packages 或 remotes 至少要有一項」是**錯的**。直接讀 `modelcontextprotocol/registry` 的 `validators.go` / `registry_service.go`:空陣列時驗證迴圈直接跳過,無「長度 > 0」檢查;官方 `generic-server-json.md` 也明講「servers with neither packages nor remotes can still be defined for documentation purposes」。

**結論:posthorn 現況(只有 GitHub repo、git clone 自架、無 PyPI/npm/Docker/hosted)可以直接登記**——`name`/`description`/`version`/`repository` 四個核心欄位填好,packages/remotes 省略即通過。**不需要先發 PyPI。**

- **唯一代價(產品面,非阻擋):** client 端(Claude Desktop registry 瀏覽 UI 等)看到 packages/remotes 皆空,**沒有「一鍵安裝」按鈕**,退化成「純目錄條目 + 連回 README 自己看」。對現階段「先發、當量測」無妨;要在 client 生態跑量時,發 PyPI 是遲早的一步(列 backlog,非現在 blocker)。

**`server.json` 已建好**(在 repo root,見 [`../../server.json`](../../server.json)),已自驗:repo id `1275889674`(`gh api` 查得)、schema URL 回 200、version 對齊 `pyproject.toml` 的 `0.1.0`。

> ⚠ 命名硬限制:GitHub device-flow 驗證下,`name` **必須**是 `io.github.elekli/<name>`(行銷名 posthorn 放不進 `name`,已放 `title`)。version 不接受 range/`latest`,每次改版要同步遞增這裡。

**提交流程(官方 Quickstart 查證):**

```bash
# 1. 裝 CLI(擇一)
brew install mcp-publisher
# 或 curl 官方 release(見 subagent 報告)

# 2. server.json 已建好,略過 init(或 mcp-publisher init 後覆蓋)
# 3. GitHub device-flow 驗證身份(name 前綴須 = io.github.elekli/)
mcp-publisher login github

# 4. 發佈
mcp-publisher publish

# 5. 驗證上架
curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.elekli/waste-for-agents"
```

**未來發 PyPI 時的 ownership 驗證(供 backlog 參考):** `uv build` → `uv publish` → 在 README 放一行 `<!-- mcp-name: io.github.elekli/waste-for-agents -->` → `server.json` 補 `packages: [{registryType:"pypi", identifier:"waste-for-agents", version:"0.1.0", transport:{...}}]`。

---

## 2. punkpeye/awesome-mcp-servers(GitHub PR)

**做法:** fork → 依 `CONTRIBUTING.md` 把一行 entry 按字母序放進對應分類 → 開 PR。格式需與既有條目一致(大小寫、標點、emoji 標記、排序),否則會被要求改。

> ⚠ emoji 圖例(🐍 Python / 🏠 local service / ☁️ cloud 等)以該 repo 的 legend 為準——開 PR 前對照 README 頂部圖例填,別亂猜。posthorn 是 Python + 本機自架,大致是 🐍 + 🏠 一類。

**Entry 草稿(emoji 待對照 legend):**
```markdown
- [waste-for-agents (posthorn)](https://github.com/elekli/waste-for-agents) 🐍 🏠 - A pull-first change feed for agents: watch RSS/feeds/APIs, get a true row-level diff via the `list_changes` MCP tool, one cursor per source.
```

---

## 3. PulseMCP(pulsemcp.com)

**首選路徑:** 先完成第 1 步(進官方 Registry),PulseMCP 每週自動收錄,零額外動作。
**若想加速:** `pulsemcp.com/submit` 表單填 repo URL;逾一週未收錄寄 hello@pulsemcp.com。
**附帶紅利:** 被收錄後有機會被 PulseMCP 的 Weekly Pulse newsletter 帶到(週期性推播)。

---

## 4. Glama(glama.ai/mcp/servers)

**做法:** Glama 會自動爬公開 repo,先產生一個「匿名版」listing;用 GitHub 帳號驗證 **claim** 後才能控制描述/連結。
**進階(讓 listing 顯示為「可用」):** 需在 repo 放 Dockerfile 並通過後台檢查——posthorn 目前無 Docker image,這步可延後,claim + 正確描述先做。

---

## 5. mcp.so

**做法:** 網站的「Submit」入口(部分來源指其走 GitHub issue 提交)填 repo URL + 簡述。審核週期不明,提交後觀察即可。SEO 排名好,常出現在 MCP server 搜尋結果前列。

---

## 6. Smithery(smithery.ai)— ⚠ 條件不符,延後

**限制:** Smithery 偏「可直接被 agent 呼叫的**託管/公開 HTTPS endpoint**」(streamable HTTP,有 auth 需支援 OAuth)。posthorn 走本機 self-host、無公開示範端點,**現階段登記價值有限**——除非之後部署一個公開 demo 實例(Phase 1 hosted 才會有)。
**結論:** 有 hosted 實例後再回來登;現在跳過,不硬湊。

---

## 提交後的追蹤

- [ ] 官方 Registry publish 成功(`mcp-publisher publish` 回 OK)
- [ ] awesome-mcp-servers PR 開出(記 PR 連結)
- [ ] PulseMCP 出現 listing(Registry 自動收錄約 1 週)
- [ ] Glama listing claimed
- [ ] mcp.so submitted
- [ ] Smithery — 延後至有 hosted demo

> 目錄是「一次投入、長期複利」;貼文(Show HN/Reddit)是「一次性尖峰」。兩者搭配:**目錄打底(常青)+ 貼文打首發聲量(尖峰)**。理想時序:先把 Registry / awesome-mcp 提交上(讓搜尋得到),再發 Show HN(流量進來時目錄已在)。
