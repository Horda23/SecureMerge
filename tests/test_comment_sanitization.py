"""Тесты защиты GitHub-комментария от LLM-текста и секретов."""

from __future__ import annotations

from ai_analyzer import AnalysisResult
from main import _status_description, build_block_comment


def _comment(settings, reason: str) -> str:
    result = AnalysisResult(status="BLOCK", reason=reason, degraded=False)
    return build_block_comment(
        result,
        settings,
        repo_full_name="acme/demo",
        pull_number=7,
        head_sha="a" * 40,
        action="opened",
        status_published=True,
    )


def test_known_secret_is_redacted_from_comment(settings) -> None:
    token = "ghp_" + "A1b2C3d4" * 4
    comment = _comment(settings, f"Найден токен {token} в app.py.")

    assert token not in comment
    assert "ghp_" not in comment
    assert "REDACTED" in comment
    assert "app.py" in comment


def test_comment_escapes_html_and_neutralizes_mentions(settings) -> None:
    comment = _comment(
        settings,
        '<script>alert("xss")</script> @admin [click](https://evil.example)',
    )

    assert "<script>" not in comment
    assert "&lt;script&gt;" in comment
    assert "@\u200badmin" in comment
    # Markdown-ссылка не должна остаться активной.
    assert "[click]" not in comment


def test_status_description_does_not_expose_llm_reason(settings) -> None:
    token = "ghp_" + "Z9y8X7w6" * 4
    result = AnalysisResult(
        status="BLOCK", reason=f"Найден токен {token} в app.py.", degraded=False
    )

    description = _status_description(result)
    assert token not in description
    assert "app.py" not in description
    assert "BLOCK" in description
