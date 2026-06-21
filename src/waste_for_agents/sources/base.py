"""Source protocol + registry。

Chunk 3 實作:Source(Protocol) 含 async fetch(query) -> list[dict];
registry 以 source 名查 adapter。介面刻意留薄(行得通一定會抽象)。
"""
