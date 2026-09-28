from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"


def test_requirements_are_before_generated_content():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert html.index('id="workflowForm"') < html.index('id="executionLogPanel"')
    assert html.index('id="executionLogPanel"') < html.index('id="resultPanel"')


def test_execution_log_is_collapsed_by_default():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="executionLogPanel" class="panel execution-log-panel hidden collapsed"' in html
    assert 'id="executionLogToggle" class="execution-log-header" type="button" aria-expanded="false"' in html
    assert "setExecutionLogCollapsed(true);" in javascript


def test_two_approval_steps_explain_wait_and_next_action():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="decisionStepBadge"' in html
    assert "第 1 次核准｜允許繼續製作" in javascript
    assert "第 2 次核准｜最終發布確認" in javascript
    assert "通常需要 30–90 秒" in javascript
    assert "最終核准內容" in javascript
    assert 'label: "前往發布中心"' in javascript
    assert '["failed", "completed", "waiting_for_human"].includes(workflow.status)' in javascript


def test_result_action_matches_workflow_status():
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'button.textContent = "前往發布中心 →"' in javascript
    assert 'button.textContent = "前往核准決策 ↑"' in javascript
    assert 'if (state.workflow?.status === "completed")' in javascript
    assert 'if (decisionPanel.classList.contains("hidden"))' in javascript


def test_publishing_distinguishes_returned_content_from_cancelled_workflow():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert '<option value="returned">退回修改</option>' in html
    assert '<option value="blocked">工作流已退回</option>' in html
    assert '<option value="archived">已封存</option>' in html
    assert '<option value="returned" ${currentStatus === "returned" ? "selected" : ""}>退回修改</option>' in javascript
    assert 'blocked: "工作流已退回"' in javascript
    assert 'archived: "已封存"' in javascript
    assert 'data-reopen-workflow' in javascript
    assert 'data-archive-workflow' in javascript
    assert '退回原因' in javascript


def test_asset_center_explains_types_and_supports_preview_and_export():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'aria-label="素材中心使用導覽"' in html
    assert "完整逐字稿" in html
    assert "製作素材包" in html
    assert "參考分析" in html
    assert 'id="assetPreviewModal"' in html
    assert 'id="assetPreviewDownload"' in html
    assert 'id="assetPreviewExport"' in html
    assert 'data-preview-asset' in javascript
    assert 'data-download-asset' in javascript
    assert 'function assetExportText(asset)' in javascript
    assert 'function downloadAsset(asset, format = "txt")' in javascript
    assert 'function buildAssetProjects(assets)' in javascript
    assert 'function renderAssetAlignment(workflow)' in javascript
    assert 'function matchStoryboardShots(section, shots, index, sectionCount)' in javascript
    assert "逐字稿與製作包已關聯" in javascript
    assert "旁白 → 分鏡畫面 → B-roll 搜尋建議" in javascript


def test_asset_center_applies_official_pacing_guidance_to_storyboard():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    stylesheet = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert 'aria-label="B-roll 剪輯節奏設定"' in html
    assert 'id="assetPacingProfile"' in html
    assert "YouTube 續看率指南" in html
    assert "TikTok Creative Guidance" in html
    assert "前 6 秒每 2–3 秒；之後每 3–5 秒" in javascript
    assert "單段 B-roll 3–5 秒" in javascript
    assert "function assetShotTiming(workflow, shots, shot, shotIndex)" in javascript
    assert "建議時間軸" in javascript
    assert "畫面類型" in javascript
    assert "使用目的" in javascript
    assert "畫面文字" in javascript
    assert "節奏理由" in javascript
    assert "YouTube 拍攝參考" in javascript
    assert "AI 圖片／影片生成包" in javascript
    assert "data-copy-media-prompt" in javascript
    assert "data-generate-shot-image" in javascript
    assert "/generated-images" in javascript
    assert "OpenAI 目前沒有可用的影片生成 API" in javascript
    assert ".asset-youtube-reference" in stylesheet
    assert ".asset-generation-pack" in stylesheet


def test_workbench_can_return_to_the_page_that_opened_a_workflow():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="workbenchReturnButton"' in html
    assert "const sourcePage = options.returnPage" in javascript
    assert 'state.workbenchReturnPage = sourcePage || "assets"' in javascript
    assert "function syncWorkbenchReturnButton()" in javascript
    assert "button.textContent = `← 返回${pageLabels[returnPage]}`" in javascript
    assert "showPage(returnPage)" in javascript


def test_script_preview_supports_human_revision_and_production_choice():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    stylesheet = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "人工修改腳本" in javascript
    assert "只存逐字稿，沿用原規劃" in javascript
    assert "存檔並重新設計" in javascript
    assert "regenerate_production" in javascript
    assert "script-editor-panel" in stylesheet


def test_workbench_visualizes_and_edits_broll_timeline():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    stylesheet = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert 'data-tab="rhythm"' in html
    assert "節奏與素材" in html
    assert "剪輯時間軸" in javascript
    assert "內容結構圖" in javascript
    assert "人工調整 B-roll／視覺" in javascript
    assert "＋ 新增素材段" in javascript
    assert 'cue_type: "broll"' in javascript
    assert "/production-plan" in javascript
    assert "請重新完成最終人工核准" in javascript
    assert ".rhythm-cue-lanes" in stylesheet
    assert ".structure-map-panel" in stylesheet
    assert ".visual-cue-editor" in stylesheet
    assert "10 個代理" in html
    assert 'data-agent="visual_director"' in html
    assert 'data-agent="youtube_reference_verifier"' in html
    assert 'data-agent="production_quality"' in html
    assert 'data-agent="publishing_preflight"' in html
    assert "視覺統籌代理" in html
    assert "重新執行視覺統籌" in javascript
    assert "/visual-review" in javascript
    assert ".visual-review-notice" in stylesheet


def test_asset_center_has_clickable_timeline_and_human_review_editor():
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    stylesheet = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "剪輯時間軸與 B-roll 審核" in javascript
    assert "點擊時間段查看畫面" in javascript
    assert 'data-asset-cue-index' in javascript
    assert 'data-asset-cue-card' in javascript
    assert "人工審核與調整" in javascript
    assert 'data-asset-cue-field' in javascript
    assert 'data-asset-add-cue' in javascript
    assert 'data-asset-remove-cue' in javascript
    assert 'data-asset-save-cues' in javascript
    assert "儲存並重新驗證" in javascript
    assert "Remotion 多軌預覽" in javascript
    assert "在預覽中查看" in javascript
    assert "已定位到" in javascript
    assert 'details.querySelector(".asset-remotion-preview")' in javascript
    assert "asset-timeline-scroll" in javascript
    assert "timelineMinWidth" in javascript
    assert 'data-cue-type="subtitle"' in javascript
    assert 'data-cue-type="title_card"' in javascript
    assert "assetTimelinePlayheads" in javascript
    assert "對齊口播字幕" in javascript
    assert "/production-plan" in javascript
    assert ".asset-timeline-workspace" in stylesheet
    assert ".asset-cue-review-card.focused" in stylesheet
    assert ".asset-remotion-preview" in stylesheet
    assert ".rhythm-cue.focused" in stylesheet
    assert ".asset-preview-stage.located" in stylesheet
    assert ".asset-timeline-scroll" in stylesheet
    assert "position: sticky" in stylesheet
    assert ".asset-preview-subtitle" in stylesheet
    assert ".asset-preview-title-card" in stylesheet
    assert ".asset-multitrack-lanes" in stylesheet


def test_reference_documents_are_extracted_and_require_human_confirmation():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert ".pdf,.docx,.xlsx,.pptx" in html
    assert 'id="referenceFileKind"' in html
    assert 'id="referenceFileReview"' in html
    assert "擷取文字（可直接修改）" in javascript
    assert "確認文字正確，納入 AI 參考" in javascript
    assert 'api("/api/v1/documents/extract"' in javascript
    assert "pendingFiles" in javascript


def test_quality_agents_are_visible_and_reviews_rendered():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    stylesheet = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "10 個代理" in html
    assert "製作包 QA 代理" in html
    assert "發布前 Preflight" in html
    assert "內容機會 Reviewer 已覆核" in javascript
    assert "production_quality_review" in javascript
    assert "publishing_preflight_review" in javascript
    assert ".opportunity-quality-review" in stylesheet
    assert ".quality-gate-notice" in stylesheet
