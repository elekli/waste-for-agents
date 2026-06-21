"""結構化 diff 引擎(護城河)。

Chunk 2 實作:diff_rows(old, new, key_columns, ignore_columns) -> DiffResult。
重點:忽略 timestamp/流水號欄位以避免誤報(反誤報測試是核心)。
"""
