from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"


def test_opportunity_analysis_has_progress_bar_and_percentage_badge() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="opportunityProgress"' in html
    assert 'id="opportunityProgressBar"' in html
    assert 'id="opportunityProgressPercent"' in html
    assert 'id="opportunityProviderBadge"' in html
    assert "startOpportunityProgress()" in javascript
    assert "completeOpportunityProgress()" in javascript
    assert "opportunityProgressSnapshot" in javascript


def test_opportunity_ui_does_not_render_provider_or_model_name() -> None:
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "OpenAI 研究生成" not in javascript
    assert "供應者 ${" not in javascript
    assert "模型 ${" not in javascript
    assert "state.healthInfo.opportunity_model" not in javascript
