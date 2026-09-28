from app.domain.models import AgentResult, RiskLevel, WorkflowRun


HIGH_RISK_TERMS = {"醫療", "投資", "法律", "選舉", "政治", "medical", "investment", "legal"}


class DeterministicAgent:
    """本地可跑的 agent stub；之後可替換成 OpenAI/Claude/Gemini adapter。"""

    def __init__(self, name: str, purpose: str) -> None:
        self.name = name
        self.purpose = purpose

    async def execute(self, workflow: WorkflowRun) -> AgentResult:
        topic = workflow.input.topic
        is_high_risk = self.name == "verification" and any(
            term.lower() in topic.lower() for term in HIGH_RISK_TERMS
        )
        risk = RiskLevel.HIGH if is_high_risk else RiskLevel.LOW
        confidence = 0.78 if is_high_risk else 0.92
        issues = ["主題屬高風險領域，需要人工確認來源與表述"] if is_high_risk else []
        return AgentResult(
            agent=self.name,
            confidence=confidence,
            risk_level=risk,
            summary=f"{self.purpose}已完成：{topic}",
            issues=issues,
            evidence=["deterministic-agent-v1"],
            artifact={"topic": topic, "purpose": self.purpose},
            confidence_basis="local_rule_heuristic",
            is_simulated=True,
        )


class LocalVisualDirectorAgent:
    """離線視覺統籌：依逐字稿產生相關提示，人工素材僅審查不覆寫。"""

    name = "visual_director"
    provider = "local_rule"
    model = "visual-director-local-v1"

    async def execute(self, workflow: WorkflowRun) -> AgentResult:
        sections = workflow.artifacts.get("script", {}).get("sections", [])
        prompts = [
            (
                f"{section.get('label', f'段落 {index + 1}')}："
                f"{section.get('visual_direction') or section.get('voiceover', '')[:120]}，"
                f"對應主題「{workflow.input.topic}」，具體主體、動作與鏡位，專業內容攝影"
            )
            for index, section in enumerate(sections[:6])
        ] or [f"{workflow.input.topic}，依完整逐字稿安排具體主體、動作與鏡位"]
        generation_prompts = [
            {
                "shot": index + 1,
                "label": section.get("label", f"段落 {index + 1}"),
                "image_prompt": (
                    f"以「{workflow.input.topic}」為主題，呈現{section.get('visual_direction') or section.get('voiceover', '')[:160]}。"
                    "寫實商業攝影，主體清晰，交代環境與道具，中景搭配細節特寫，自然柔光，畫面不含錯字、浮水印或未授權商標。"
                ),
                "video_prompt": (
                    f"以「{workflow.input.topic}」為主題，拍攝{section.get('visual_direction') or section.get('voiceover', '')[:160]}。"
                    "4 秒鏡頭，緩慢推近或水平移動，動作連續自然，首尾構圖穩定，光線與色彩一致；避免物體變形、閃爍、跳切與錯誤文字。"
                ),
                "negative_prompt": "浮水印、亂碼、錯誤品牌標誌、物體變形、過度銳化、閃爍、突兀鏡頭運動",
                "aspect_ratio": "9:16" if workflow.input.output_type == "short_video" else "16:9",
            }
            for index, section in enumerate(sections[:30])
        ]
        context = workflow.artifacts.get("_visual_review_context", {})
        cues = context.get("visual_cues", [])
        summary = f"已完成 {len(sections)} 個段落與 {len(cues)} 個人工素材段的視覺對齊檢查。"
        return AgentResult(
            agent=self.name,
            confidence=0.88,
            risk_level=RiskLevel.LOW,
            summary=summary,
            evidence=["full_script_visual_alignment", "human_visual_priority"],
            artifact={
                "visual_direction_review": {
                    "confidence": 0.88,
                    "risk_level": "low",
                    "summary": summary,
                    "issues": [],
                    "alignment_notes": ["人工素材保留原值，僅提供對齊檢查。"] if cues else ["圖像提示已改由逐字稿段落產生。"],
                    "visual_style": f"{workflow.input.topic} 專業實拍與證據導向內容風格",
                    "palette": ["#1F2937", "#F8FAFC", "#2563EB"],
                    "image_prompts": prompts,
                    "broll_queries": [
                        f"{workflow.input.topic} {section.get('label', '')}".strip()
                        for section in sections[:10]
                    ],
                    "youtube_references": [],
                    "generation_prompts": generation_prompts,
                    "suggested_visual_cues": [],
                    "review_mode": context.get("mode", "initial_plan"),
                    "generation": {
                        "provider": "local_rule",
                        "model": self.model,
                        "prompt_version": "workflow-visual-director-v1",
                    },
                }
            },
            confidence_basis="local_script_alignment",
            is_simulated=True,
        )
