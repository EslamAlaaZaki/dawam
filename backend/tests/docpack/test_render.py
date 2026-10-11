"""The documentation pack's Markdown and HTML renderers (spec §6.15, story 128). No database."""

from __future__ import annotations

from dawam.modules.docpack.render import (
    Bullets,
    Heading,
    Page,
    Paragraph,
    Table,
    markdown,
    single_html,
)


def test_a_table_cell_cannot_break_the_markdown_table():
    text = markdown([Table(["A", "B"], [["x | y", "line one\nline two"]])])

    assert text.splitlines() == ["| A | B |", "| --- | --- |", "| x \\| y | line one line two |"]


def test_headings_can_be_deepened_for_a_combined_file():
    assert markdown([Heading(1, "Title"), Bullets(["a", "b"])], shift=1) == "## Title\n\n- a\n- b\n"


def test_the_html_is_one_self_contained_file_and_escapes_user_text():
    pages = [
        Page("a.md", "First", [Heading(1, "First"), Paragraph("<script>alert(1)</script>")]),
        Page("b.md", "Second", [Table(["Col"], [["<img src=x onerror=y>"]])]),
    ]

    html = single_html("Pack <b>", pages)

    assert html.startswith("<!doctype html>")
    assert "<script" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=y&gt;" in html
    assert "<title>Pack &lt;b&gt;</title>" in html
    assert 'href="#page-0"' in html and 'id="page-1"' in html
    assert "http://" not in html and "https://" not in html
