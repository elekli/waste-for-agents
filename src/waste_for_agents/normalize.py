"""HTML→Markdown 正規化 + 版本戳(F5)。

content 轉 pinned Markdown:保留連結/結構、去 HTML 噪音、LLM 原生友善。
固定 markdownify 參數確保 determinism(不變式 6);norm_version() 含 feedparser +
markdownify 版本,scheduler 用它偵測轉換器升級、避免偽報整片 feed modified。
"""

from __future__ import annotations

from importlib.metadata import version

from markdownify import markdownify as _md


def html_to_markdown(html: str) -> str:
    """HTML → Markdown(固定參數,位元級 determinism;不變式 6)。

    固定 heading_style/strip 是 determinism 的前提:任何參數變動等同正規化版本變動,
    須一併反映進 norm_version()。
    """
    result: str = _md(html, heading_style="ATX", strip=["script", "style"])
    return result.strip()


def norm_version() -> str:
    """正規化版本戳:含 markdownify + feedparser 精確版(F5 偵測升級)。"""
    return f"md{version('markdownify')}+fp{version('feedparser')}"
