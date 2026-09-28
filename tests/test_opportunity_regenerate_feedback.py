from pathlib import Path

from app.domain.models import OpportunityRegenerateRequest


STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"


def test_regenerate_instruction_is_optional_and_normalized() -> None:
    assert OpportunityRegenerateRequest().modification_instruction is None
    request = OpportunityRegenerateRequest(
        modification_instruction="  改成新手角度\n並減少規格描述  "
    )
    assert request.modification_instruction == "改成新手角度 並減少規格描述"


def test_single_item_regenerate_ui_collects_optional_human_instruction() -> None:
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "希望 AI 怎麼修改？" in javascript
    assert "data-opportunity-regenerate-instruction" in javascript
    assert "modification_instruction: modificationInstruction || null" in javascript
    assert "toggleOpportunityRegeneratePanel" in javascript
    assert "data-opportunity-item-progress-percent" in javascript
    assert "startOpportunityItemProgress" in javascript
    assert "completeOpportunityItemProgress" in javascript
    assert "opportunityItemProgressSnapshot" in javascript
