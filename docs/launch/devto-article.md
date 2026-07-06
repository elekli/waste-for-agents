# Dev.to 技術長文草稿 — posthorn

> 發表用草稿 + 導引。**正文英文**;繁中是給 elek 的導引。
>
> **定位:** 與 Show HN 刻意不同角度——Show HN 是 launch + 誠實邊界;這篇是**設計決策深度敘事**,主軸放在一個真實踩坑(多 watch 共用全域 cursor 的混流 bug,即 PR #18 的教訓)。Dev.to 讀者吃「真的踩過的坑 + 為什麼這樣設計」,不吃行銷。原創技術肉在這裡,不要稀釋成產品公告。
>
> **平台與 tags:** Dev.to。建議 tags:`#showdev` `#opensource` `#python` `#ai`(Dev.to 最多 4 個)。
> **Cross-post:** 這篇當 **canonical**(canonical_url 指向 Dev.to 原文或自有網域),之後可用 Dev.to/Hashnode 的 canonical 機制轉載到 Hashnode;未來若投 Lobsters,以這篇的「深度技術文」型態投(Lobsters 反感單向 launch 公告,但吃設計決策文)。**不要**把這篇原封不動貼去 Reddit——Reddit 各版另寫短的 show-and-tell。
> **發表節奏:** 建議在 Show HN 之後 1–3 天發,附一句「discussed on HN: <連結>」,讓兩邊互相沉澱而不是同一天洗版。

---

## 標題(擇一)

**主推:** `The bug that taught me agents need per-source cursors`
備選:
- `Giving AI agents a "what changed?" feed — and the cursor bug I hit`
- `Pull-first change feeds for AI agents: a design writeup`

---

## 正文(英文原文)

### The one question my agents kept re-deriving

I run a few small agents that watch external sources — an RSS feed, a government open-data table, a JSON API. Every time one woke up, it did the same expensive dance: re-fetch everything, and try to figure out *did anything actually change since I last looked?*

That question is deceptively annoying to answer well. "The feed returned 30 items" is not the same as "reality changed." A source that re-serializes itself every poll, bumps a `last_updated` timestamp, or reorders rows will look different byte-for-byte while meaning nothing new. And each agent was reimplementing its own half-broken version of the same dedup logic.

So I pulled it out into a small service — [posthorn](https://github.com/elekli/waste-for-agents) (the package is `waste-for-agents`). This is a writeup of the two design decisions that mattered, and the bug that forced the second one.

### Decision 1: pull, not push

The obvious design is webhooks: source changes → service POSTs to the agent. It's wrong for this audience.

A lot of agents aren't addressable. A chat session is ephemeral. A serverless invocation is gone in 200ms. Even a persistent agent behind NAT has no stable endpoint to receive on. Push assumes a receiver that's online and reachable — exactly what a *sleeping* agent isn't.

So the delivery leg is a pull:

```
list_changes(watch_id="hn")
# usually nothing shifted upstream — a no-op:
{"events": [], "cursor": 41}

# then one day the horn sounds:
{"events": [{"kind":"added","title":"…","content":"<clean markdown>"}], "cursor": 42}
```

The service is itself an MCP (Model Context Protocol) server, so `list_changes` is just a tool the agent already knows how to call. Persistent agents drain it on each tick — the loop *is* the awaiting. Ephemeral ones (a Claude Code session) drain it once at startup from a SessionStart hook. There's also a plain `GET /changes?since=<cursor>` HTTP mirror so a shell script can do the same with `curl`, no MCP handshake needed.

(Push isn't wrong for *every* endpoint — a webhook adapter is on the roadmap for agents that can receive. It's just the wrong *default*.)

### Decision 2: a true diff, not a firehose

Handing an agent the raw feed defeats the purpose — you've just moved the "what changed?" problem into its context window. So the core is a structured, row-level diff:

```python
create_watch(
    source="rss",
    query={"url": "https://news.ycombinator.com/rss"},
    key_columns=["id"],        # identity: what makes a row "the same row"
    ignore_columns=[],         # noise: columns whose churn is not a change
    interval_s=3600,
)
```

You declare identity (`key_columns`) and noise (`ignore_columns`). A re-fetch that only touched an ignored column is a no-op. A genuinely added / changed / removed row is an event. Content gets normalized to clean Markdown on the way through, so downstream agents see one shape regardless of whether the source was RSS, JSON, or a dataset.

Here's the shape of the whole thing:

```
                        ┌─────────────────────────────┐
   RSS / API / dataset  │  posthorn (an MCP server)   │
   ───────────────────► │                             │
        (it polls,      │   poll on interval          │
     you don't)         │      │                      │
                        │      ▼                       │
                        │   true diff (key/ignore cols)│
                        │      │                       │
                        │      ▼                       │
                        │   change_events  (one global │
                        │   append-only id space)      │
                        └──────────┬──────────────────┘
                                   │  list_changes(watch_id, since_cursor)
                                   ▼
                          ┌──────────────────┐
                          │  your agent       │  holds one cursor PER watch
                          │  (pulls on wake)  │
                          └──────────────────┘
```

That last line — *one cursor per watch* — is where I got it wrong the first time.

### The bug: one global cursor, many watches

Early on there was effectively one stream. `list_changes` returned everything new since your cursor, across all your watches, and advanced a single cursor. It demoed fine with one watch.

Then I dogfooded it with three watches on one key, and it fell apart in a way that's obvious in hindsight:

- Watch A (a busy feed) and watch B (a quiet one) shared a cursor. Draining A advanced the cursor *past* B's unread events. B's changes were silently skipped — the agent watching B never saw them.
- Passing a `watch_id` to filter *looked* like it worked, but the underlying MCP framework was quietly dropping the argument, so every call still got the merged stream.
- Worse, the "give me a merged digest" behavior was the *silent default*. Nothing errored. You just lost events and had no signal that you had.

The lesson: **if each source is an independently-consumable stream, each needs its own cursor, and merging must never be silent.** The fix was a few coupled changes:

1. **`watch_id` is required on the MCP `list_changes` tool.** Omit it and you get an explicit error, not a silently-merged stream. (The HTTP `/changes` mirror keeps a digest mode, because a shell hook genuinely can't enumerate watch IDs — but there it's an *explicit* opt-in, not the default.)
2. **Cursors are per-watch.** The cursor a watch hands back is that watch's high-water mark in the *global* `change_events.id` space — not a watch-local counter. The client stores one cursor per watch and replays it verbatim; cursors from different watches aren't interchangeable.
3. **Ownership checks return bit-identical empty responses for "not yours" and "doesn't exist."** Otherwise the presence or absence of an error leaks whether a watch_id exists — a subtle privacy hole when watch IDs are guessable.

None of this is exotic. But it's the exact class of state that I *don't* want every agent author to reimplement — and getting it subtly wrong is easy, which is sort of the whole argument for factoring it out.

### "Isn't this just RSS polling with a cursor?"

Yeah, mostly. I'd rather say that plainly than dress it up. The bet is that the *surrounding state* — a diff that survives noisy re-serialization, dedup, normalization to one shape, and an independent cursor per source — is the annoying part, and doing it once beats N agents each doing it badly. If you think that state is trivial, I'd genuinely like to be told why.

### Honest boundaries

It's an MVP and I'm not going to pretend otherwise:

- **No hosted instance.** Self-host today: `git clone`, `uv run python -m waste_for_agents serve`. No PyPI/npm package yet, no SaaS. Whether a hosted version is worth building is what I'm currently trying to find out.
- **Documented security edges:** `create_watch`'s `query` isn't validated yet (raw SQL reaches the Twinkle adapter for key-holders); DNS-rebinding isn't guarded; `/health` is unauthenticated; it binds loopback by default. SSRF *is* guarded — scheme allowlist, blocked internal/metadata IPs, per-redirect-hop re-validation. Full list is in the repo's `TODOS.md`.
- Python 3.12, 186 passing tests, open source.

### Try it / tell me where it breaks

Repo: https://github.com/elekli/waste-for-agents

If you run persistent or scheduled agents that watch external sources, I'd really like to know how you answer "what changed?" today, and where this design would break for you. The pull-first premise is the part I'm least sure about — push back on it.

*(The name is from Pynchon's* The Crying of Lot 49 *— the muted post horn of the W.A.S.T.E. underground postal system. The agents keep the horn to their ear and wait for it to sound.)*
