from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"


def test_user_platform_choices_only_show_supported_mvp_channels() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert html.count('value="youtube"') == 2
    assert html.count('value="instagram"') == 2
    assert html.count('value="threads"') == 2
    assert 'value="linkedin"' not in html.casefold()
    assert "linkedin" not in html.casefold()


def test_legacy_hidden_platform_is_filtered_from_rendered_artifacts() -> None:
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert '["youtube", "instagram", "threads"].includes' in javascript
    assert "visibleDistributionKit(production.distribution_kit || [])" in javascript
    assert 'linkedin: "https://www.linkedin.com/feed/"' not in javascript.casefold()


def test_user_language_choices_hide_japanese() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert '<option value="zh-Hant">繁體中文</option>' in html
    assert '<option value="zh-Hans">簡體中文</option>' in html
    assert '<option value="en">英文</option>' in html
    assert 'value="ja"' not in html
    assert "日文" not in html
    assert "日文" not in javascript
