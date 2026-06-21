"""排程器(asyncio 常駐 loop)。

Chunk 4 實作:對每個到期 watch fetch -> diff -> 有變化才寫 event + 更新 snapshot。
錯誤具名寫入 watch.last_error,不靜默吞、不影響其他 watch。
"""
