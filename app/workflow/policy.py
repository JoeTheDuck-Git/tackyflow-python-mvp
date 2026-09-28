from app.domain.models import AgentResult, HumanRequest, RiskLevel


AUTO_CONTINUE_CONFIDENCE = 0.85
MAX_AUTOMATIC_REVISIONS = 2


def evaluate_agent_result(result: AgentResult) -> HumanRequest | None:
    # Research uncertainty is routed to verification before asking a human.
    if result.agent == "research" or result.status == "skipped":
        return None
    if result.risk_level == RiskLevel.HIGH:
        return HumanRequest(
            reason="high_risk",
            question="內容涉及高風險領域，是否允許沿用目前來源與表述繼續？",
            suggested_action="檢查來源、聲明與關鍵事實後再核准。",
        )
    if result.confidence < AUTO_CONTINUE_CONFIDENCE:
        return HumanRequest(
            reason="low_confidence",
            question=f"{result.agent} 的信心值為 {result.confidence:.2f}，是否繼續？",
            suggested_action="補充資料或要求另一個 agent 驗證。",
        )
    return None
