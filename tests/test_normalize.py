"""HTML→Markdown 正規化 + 版本戳(F5,不變式 6 determinism)。"""

from waste_for_agents.normalize import html_to_markdown, norm_version


def test_html_to_markdown_deterministic():
    html = "<p>Hello <a href='https://x.com'>link</a> <strong>bold</strong></p>"
    a = html_to_markdown(html)
    b = html_to_markdown(html)
    assert a == b  # 位元級相同(不變式 6)


def test_html_to_markdown_preserves_link():
    md = html_to_markdown('<p>see <a href="https://x.com/a">here</a></p>')
    assert "https://x.com/a" in md
    assert "[here](https://x.com/a)" in md


def test_html_to_markdown_strips_tags():
    md = html_to_markdown("<div><p>plain text</p></div>")
    assert "plain text" in md
    assert "<p>" not in md and "<div>" not in md


def test_norm_version_stable_and_contains_versions():
    v = norm_version()
    assert v == norm_version()  # 穩定
    assert "fp" in v and "md" in v and "bs4" in v  # 含三個影響輸出的元件
    # 與 pyproject 的 pin 同步——升版時一起改(故意硬編,當 pin 偏移的回歸警報)
    assert "6.0.12" in v and "1.2.2" in v and "4.15.0" in v
