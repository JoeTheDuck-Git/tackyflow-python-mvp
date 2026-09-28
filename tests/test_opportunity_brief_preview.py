import pytest

from app.domain.models import ReferenceMaterial, WorkflowInput, WorkflowRun
from app.workflow.artifacts import build_content_preview, build_reference_analysis


def test_opportunity_handoff_brief_changes_complete_script_preview() -> None:
    workflow = WorkflowRun(
        input=WorkflowInput(
            topic="360 相機選購",
            goal="education",
            platforms=["youtube"],
            output_type="short_video",
            target_word_count=500,
            brand_voice="專業但自然",
            constraints=[
                "避免使用絕對化宣稱",
                "目標受眾：第一次購買運動相機的旅行創作者",
                "內容角度：先從真實拍攝情境判斷是否需要 360 相機",
                "開場 Hook：不是每個旅行創作者都需要 360 相機，先看這三個情境。",
                "關鍵重點：先確認拍攝情境；比較後製時間；計算完整設備預算",
                "CTA：留言告訴我你的拍攝情境，我會幫你判斷適不適合。",
                "預估製作：中：需要實拍與基本素材",
                "製作備註：使用繁體中文；加入一段戶外實拍畫面",
                "製作前須確認：官方規格頁；實際續航測試",
            ],
        )
    )

    script = build_content_preview(workflow)
    brief = script["source_brief"]

    assert brief["has_structured_brief"] is True
    assert brief["target_audience"] == "第一次購買運動相機的旅行創作者"
    assert brief["angle"] == "先從真實拍攝情境判斷是否需要 360 相機"
    assert brief["key_points"] == ["先確認拍攝情境", "比較後製時間", "計算完整設備預算"]
    assert brief["production_notes"] == ["使用繁體中文", "加入一段戶外實拍畫面"]
    assert brief["evidence_needed"] == ["官方規格頁", "實際續航測試"]
    assert brief["other_constraints"] == ["避免使用絕對化宣稱"]

    full_text = script["full_text"]
    assert "不是每個旅行創作者都需要 360 相機" in full_text
    assert "第一次購買運動相機的旅行創作者" in full_text
    assert "先從真實拍攝情境判斷是否需要 360 相機" in full_text
    assert "先確認拍攝情境；比較後製時間；計算完整設備預算" in full_text
    assert "官方規格頁；實際續航測試" in full_text
    assert "使用繁體中文；加入一段戶外實拍畫面" in full_text
    assert "避免使用絕對化宣稱" in full_text
    assert "留言告訴我你的拍攝情境" in full_text

    action = next(section for section in script["sections"] if section["id"] == "action")
    assert "預估製作：中：需要實拍與基本素材" in action["visual_direction"]
    assert "加入一段戶外實拍畫面" in action["visual_direction"]
    assert "內容 Brief 指定受眾" in script["summary"]


def test_unstructured_constraints_remain_compatible() -> None:
    workflow = WorkflowRun(
        input=WorkflowInput(
            topic="AI 內容工作流",
            goal="education",
            platforms=["youtube"],
            output_type="short_video",
            constraints=["不要使用誇張承諾"],
        )
    )

    script = build_content_preview(workflow)

    assert script["source_brief"]["has_structured_brief"] is False
    assert script["source_brief"]["other_constraints"] == ["不要使用誇張承諾"]
    assert "不要使用誇張承諾" in script["full_text"]
    assert len(script["sections"]) == 5


@pytest.mark.parametrize("target_word_count", [1500, 1800, 5000])
def test_local_preview_reaches_long_form_word_targets(target_word_count: int) -> None:
    workflow = WorkflowRun(
        input=WorkflowInput(
            topic="AI 客服導入風險",
            goal="education",
            platforms=["youtube"],
            output_type="long_text_article",
            target_word_count=target_word_count,
        )
    )

    script = build_content_preview(workflow)

    assert script["word_count"] >= target_word_count
    assert script["word_count"] <= target_word_count + 120
    assert script["target_word_count"] == target_word_count


def test_blank_reference_focus_uses_honest_default_label() -> None:
    workflow = WorkflowRun(
        input=WorkflowInput(
            topic="內容工作流",
            goal="education",
            platforms=["youtube"],
            output_type="short_video",
            reference_materials=[
                ReferenceMaterial(
                    name="參考稿",
                    kind="creator_reference",
                    content="短句示例。",
                    focus="",
                )
            ],
        )
    )

    analysis = build_reference_analysis(workflow)

    assert analysis["sources"][0]["focus"] == "整體參考"
    assert "重點參考「整體參考」" in analysis["style_signals"][0]
