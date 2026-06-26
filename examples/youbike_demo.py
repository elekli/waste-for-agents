"""真實分鐘級 demo:盯台北市 YouBike 即時資訊,看 diff 引擎在「會動的源」上響一次。

跑法:uv run python examples/youbike_demo.py
做什麼:抓兩次(間隔 ~70s),對同一份資料跑兩種 diff——
  (A) 忽略 timestamp 欄位 → 只剩「車輛數真的變了」的站(這是我們要的訊號)
  (B) 不忽略         → 幾乎每站都被 timestamp 跳動誤報成 modified(這是 moat 擋掉的噪音)
證明:同一個引擎搬到真正高頻的源上,真變化抓得到、噪音擋得掉。
"""

from __future__ import annotations

import asyncio

from waste_for_agents.diff import diff_rows
from waste_for_agents.sources.http_json import HttpJsonSource

URL = "https://tcgbusfs.blob.core.windows.net/dotapp/youbike/v2/youbike_immediate.json"
KEY = ["sno"]
TIMESTAMP_COLS = ["mday", "updateTime", "infoTime", "srcUpdateTime", "infoDate"]
GAP_S = 70


async def main() -> None:
    src = HttpJsonSource()

    print(f"[1/2] 第一次抓取 {URL.split('/')[-1]} …")
    snap1 = await src.fetch({"url": URL})
    t1 = snap1[0].get("mday", "?")
    print(f"      {len(snap1)} 站,資料時間 {t1}")

    print(f"      等 {GAP_S}s 讓上游更新…")
    await asyncio.sleep(GAP_S)

    print("[2/2] 第二次抓取 …")
    snap2 = await src.fetch({"url": URL})
    t2 = snap2[0].get("mday", "?")
    print(f"      {len(snap2)} 站,資料時間 {t2}\n")

    signal = diff_rows(snap1, snap2, KEY, TIMESTAMP_COLS)
    noise = diff_rows(snap1, snap2, KEY, [])

    print("=== (A) 忽略 timestamp → 真訊號 ===")
    print(
        f"  車輛數有變的站:{len(signal.modified)}"
        f"  新增:{len(signal.added)}  移除:{len(signal.removed)}"
    )
    by_sno = {r["sno"]: r for r in snap2}
    for m in signal.modified[:8]:
        sno = m.key.strip('[]"')
        sna = by_sno.get(sno, {}).get("sna", sno).replace("YouBike2.0_", "")
        rent = m.changes.get("available_rent_bikes")
        ret = m.changes.get("available_return_bikes")
        bits = []
        if rent:
            bits.append(f"可借 {rent[0]}→{rent[1]}")
        if ret:
            bits.append(f"可還 {ret[0]}→{ret[1]}")
        print(f"    • {sna}:{'  '.join(bits)}")

    print("\n=== (B) 不忽略 timestamp → 噪音(moat 擋掉的) ===")
    print(f"  被誤報成 modified 的站:{len(noise.modified)}(每站 timestamp 都跳)")
    ratio = len(noise.modified) / max(len(signal.modified), 1)
    print(f"\n  → 不過濾的話訊噪比 {ratio:.0f}×:{len(noise.modified)} 筆噪音 vs {len(signal.modified)} 筆真變化")


if __name__ == "__main__":
    asyncio.run(main())
