# Show HN 草稿 — posthorn (waste-for-agents)

> 這份是發表用草稿 + 操作導引。**正文以英文撰寫**(HN 是英文社群);繁中部分是給 elek 的導引與註解,發表時不要貼進去。
>
> **定位(不要偏離):** 這是一次**需求量測**,不是發表會。HN 懲罰虛張聲勢、不罰規模小;誠實承認邊界(沒 hosted、MVP、已知缺口)是資產不是弱點。發文目標是換到「正在跑 agent 的人」的尖銳回饋與合格樣本,不是換掌聲。
>
> **發表機制:** Show HN = 一個標題 + 一個 URL + 第一則由作者自貼的 text 說明(HN 允許 Show HN 在 url 之外附作者首評)。實務上:submit 時填標題 + URL(GitHub repo),送出後**立刻**用作者身分在自己的貼文下貼下方「作者首評」。
>
> **發表前置 checklist:**
> - [ ] 確認 GitHub repo README 對陌生人可讀(quickstart 已驗證能跑)
> - [x] 測試數已填:201 passing(+7 skipped,需 live-network flag)@ feat/founding-tier(發表前以 main 實跑數為準)
> - [ ] 挑一個非美國深夜的時段發(HN 流量:美東早上 8–10 點常見)
> - [ ] 發文後 1–2 小時人要在線上,即時、誠實回每一則留言(HN 對作者到場度敏感)

---

## 標題(擇一,≤80 chars)

**主推:**
```
Show HN: Posthorn – a pull-first change feed for AI agents (MCP server)
```

備選:
```
Show HN: A change feed for AI agents – watch RSS/APIs, the agent pulls the diff
Show HN: Posthorn – agents subscribe to change and pull only the real diff (MCP)
```

**URL 欄位:** `https://github.com/elekli/waste-for-agents`
(沒有 hosted demo 可放——這是誠實的一部分,不要為了有個 URL 硬架一個半殘的實例。)

---

## 作者首評(送出後立刻自貼;英文原文照貼)

Hi HN. I built posthorn (the repo is `waste-for-agents`) because my agents kept re-reading the same feeds to answer one question: *"did anything actually change since I last looked?"*

**What it does.** You point it at a structured source — an RSS/Atom feed, a JSON API, a dataset — and it polls on a schedule and keeps a *true, row-level diff*. It deliberately ignores timestamp / serial-number churn, so a source that merely bumped a `last_updated` column produces **no** change. Your agent then drains the accumulated changes with one call:

```
list_changes(watch_id="hn")
# usually: {"events": [], "cursor": 41}   ← nothing shifted upstream, a no-op
# one day: {"events": [{"kind":"added","title":"…","content":"<clean markdown>"}], "cursor": 42}
```

The service **is itself an MCP server**, so `list_changes` is a tool your agent already knows how to call. There's also a plain `GET /changes?since=<cursor>` HTTP mirror you can `curl` from a shell hook. Each watch is its own stream with its own cursor — you hold the cursor, you never re-see what you already saw.

**Why pull, not push.** A sleeping or ephemeral agent (a single chat session, a serverless invocation) can't host a webhook receiver, so push-to-agent is the wrong default. Persistent agents drain `list_changes` on each tick; ephemeral ones (e.g. a Claude Code session) drain it once at startup via a SessionStart hook. A push/webhook adapter is on the roadmap for the endpoints that *can* receive — but it's not built yet, and I'd rather say so than fake it.

**"Isn't this just RSS polling + a cursor?"** Largely, yes — that's the honest core. The bet is that the *state* around it is the annoying part to get right per-agent: dedup, a true diff that survives noisy re-serialization, normalizing wildly different sources into one clean shape (content comes back as Markdown), and keeping an independent cursor **per source** so one busy feed doesn't blow away another's position. Posthorn does that once so N agents don't each reimplement it. If you think that state is trivial, I genuinely want to hear it — that's part of what I'm testing by posting this.

**Honest boundaries (please read before trying it):**
- **No hosted instance.** It's self-host today: `git clone`, `uv run python -m waste_for_agents serve`. I haven't published a PyPI/npm package or stood up a SaaS. Whether a hosted version is worth building is exactly what I'm trying to find out — there's a $5/mo founding tier on the site if you want to vote with your card (zero charged until hosted actually ships; cancel anytime; if I never ship, you pay nothing).
- **MVP security edges, documented in the repo:** `create_watch`'s `query` isn't validated yet (a key-holder can pass raw SQL to the Twinkle adapter); DNS-rebinding isn't guarded; `/health` is unauthenticated. It binds loopback by default. SSRF *is* guarded (scheme allowlist, blocks internal/metadata IPs, re-checks every redirect hop). Full list is in `TODOS.md`.
- Python 3.12, 201 passing tests (plus 7 skipped behind live-network flags), open source. First real adapter is RSS; a Taiwan open-data adapter (Twinkle) is in there too. The source interface is thin — any structured source slots in.

**What I'm looking for:** if you run persistent or scheduled agents that watch external sources, I'd love to know how you handle "what changed" today, and where this would break for you. Skeptical takes welcome — especially on the pull-first premise.

(The name is from Pynchon's *The Crying of Lot 49* — the muted post horn and the W.A.S.T.E. underground postal system. The agents keep the horn to their ear and wait for it to sound. That's the only flourish; the rest is a plain diff.)

---

## 預答 FAQ(留言區出現對應質疑時,挑對應段落回;英文)

> 這些不是要一次貼出,是預先想清楚立場,留言來了照這個方向誠實回。

**Q: Why not just use webhooks / existing RSS-to-webhook services (e.g. RSS→Zapier)?**
Those need the receiver to be online and addressable. My target is agents that are *asleep* between ticks or that spin up per-invocation — they have no stable endpoint to receive on. Pull inverts that: the agent asks when it's awake. For agents that *do* have an endpoint, a push adapter is on the roadmap and would carry the same payload.

**Q: How is the "true diff" different from just comparing feed items?**
It's row-keyed and column-aware. You declare `key_columns` (identity) and `ignore_columns` (noise). A re-fetch that changed only an ignored column is a no-op; a genuinely new/changed/removed row is an event. That's the whole point — not "the feed returned bytes," but "reality changed."

**Q: What happens when a source is flaky / returns errors?**
Errors are captured per-watch (scrubbed of tokens, truncated) and surfaced via `list_watches` / `/changes` as `last_error`, rather than silently dropped. (DNS-level silent-failure resilience is a tracked gap — see TODOS.)

**Q: Is this production-ready?**
No, and I won't pretend otherwise. It's an MVP with documented security gaps (above). It runs, it's tested, and it does what this post says — but I'd self-host it behind a trusted boundary, not expose it raw to the internet yet.

**Q: Business model? Is it going to rug-pull to closed-source?**
It's open source and self-hostable, and there's an `--unmetered` flag that turns off the billing gate entirely for self-hosters — so your own free-tier limit never blocks your own long-running workflow. The hosted version is what I'm validating: there's a $5/mo founding tier (card on file, **zero charged until hosted actually ships**, trial extends until launch, cancel anytime). If nobody subscribes, that's my answer and I don't build it. The self-host path stays either way. Payments go through Polar as merchant of record.

**Q: How is this different from a webhook inbox (Hookdeck, webhooks.cc, Svix Ingest)?**
Those are mailboxes: they durably record whatever someone POSTs at them, and they're good at that. Posthorn is a subscriber with a diff engine — it goes out and polls sources that never push anything (RSS, JSON APIs, open-data endpoints — most of the world), and what it hands your agent isn't a request log but keyed added/modified/removed events with the noise (timestamp churn, re-serialization) already subtracted, on a per-source cursor that doesn't expire after a debug-tool retention window. If your source already sends webhooks and you just need to not miss them, use one of those — they're the right tool. An inbound webhook ingest (posthorn as the durable receiving end for intermittent clients) is a natural source type on the roadmap, and honestly the two categories are converging; I'd rather say that than pretend otherwise.

**Q: Why "waste-for-agents" vs "posthorn"?**
`waste-for-agents` is the package/repo (the W.A.S.T.E. backronym); posthorn is the friendlier product name. Same thing.
