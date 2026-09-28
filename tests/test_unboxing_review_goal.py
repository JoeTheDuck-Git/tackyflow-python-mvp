from pathlib import Path

from app.agents.opportunity import _goal_bonus


STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"


def test_unboxing_review_goal_is_available_in_both_entry_points():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert html.count('<option value="unboxing_review">開箱測評</option>') == 2


def test_unboxing_review_goal_survives_normalization_and_influences_ranking():
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'unboxing_review: "unboxing_review"' in javascript
    assert '"開箱測評": "unboxing_review"' in javascript
    assert _goal_bonus("unboxing_review", "選購決策") == 12
    assert _goal_bonus("開箱測評", "真實挑戰") == 12
