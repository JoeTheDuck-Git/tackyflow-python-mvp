import re
from typing import Any

from app.domain.models import WorkflowRun


PLATFORM_LABELS = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "threads": "Threads",
    "linkedin": "LinkedIn",
}

REFERENCE_KIND_LABELS = {
    "brand_brief": "品牌 Brief",
    "product_facts": "產品與事實資料",
    "owned_content": "自家過往內容",
    "creator_reference": "外部創作者參考",
    "general": "一般補充資料",
}

REFERENCE_PRIORITIES = {
    "brand_brief": 1,
    "product_facts": 2,
    "owned_content": 3,
    "creator_reference": 4,
    "general": 5,
}

BRIEF_FIELD_LABELS = {
    "目標受眾": "target_audience",
    "內容角度": "angle",
    "開場hook": "hook",
    "hook": "hook",
    "關鍵重點": "key_points",
    "cta": "cta",
    "預估製作": "estimated_effort",
    "製作備註": "production_notes",
    "製作前須確認": "evidence_needed",
    "證據需求": "evidence_needed",
}

BRIEF_LIST_FIELDS = {"key_points", "production_notes", "evidence_needed"}


def build_reference_analysis(workflow: WorkflowRun) -> dict[str, Any]:
    """整理使用者提供的參考資料，只提取高層次特徵，不直接仿寫外部創作者。"""
    materials = sorted(
        workflow.input.reference_materials,
        key=lambda item: REFERENCE_PRIORITIES.get(item.kind, 99),
    )
    sources: list[dict[str, Any]] = []
    applied_guidance: list[str] = []
    style_signals: list[str] = []

    for material in materials:
        text = " ".join(material.content.split())
        focus = " ".join(material.focus.split()) or "整體參考"
        sources.append(
            {
                "name": material.name,
                "kind": material.kind,
                "kind_label": REFERENCE_KIND_LABELS.get(material.kind, material.kind),
                "source_url": material.source_url,
                "focus": focus,
                "content_length": len(text),
                "priority": REFERENCE_PRIORITIES.get(material.kind, 99),
                "status": "已納入分析",
            }
        )
        if material.kind in {"brand_brief", "product_facts"} and text:
            applied_guidance.append(f"{material.name}：{text[:120]}{'…' if len(text) > 120 else ''}")
        if material.kind in {"creator_reference", "owned_content"} and text:
            sentence_count = max(1, sum(text.count(mark) for mark in "。！？!?"))
            average_length = max(1, round(len(text) / sentence_count))
            style_signals.append(
                f"{material.name}：平均句長約 {average_length} 字，重點參考「{focus}」"
            )

    return {
        "has_references": bool(materials),
        "source_count": len(materials),
        "sources": sources,
        "applied_guidance": applied_guidance,
        "style_signals": style_signals,
        "priority_rule": "品牌 Brief → 產品事實 → 自家內容 → 外部創作者 → 一般資料",
        "reference_boundary": workflow.input.reference_boundary,
        "safeguards": [
            "外部創作者資料僅提取節奏、結構與表達特徵，不直接複製原句。",
            "資料衝突時，以品牌 Brief、產品事實與使用者限制為優先。",
        ],
    }


def build_content_preview(workflow: WorkflowRun) -> dict[str, Any]:
    """建立本地可閱讀成果；正式 provider 上線後由 Writer Agent artifact 取代。"""
    topic = workflow.input.topic
    goal = workflow.input.goal
    voice = workflow.input.brand_voice
    target_word_count = workflow.input.target_word_count
    source_brief = _parse_opportunity_brief(workflow.input.constraints)
    reference_analysis = workflow.artifacts.get("reference_analysis") or build_reference_analysis(workflow)
    reference_note = (
        f"，並參考 {reference_analysis['source_count']} 份指定資料"
        if reference_analysis["has_references"]
        else ""
    )
    hook = source_brief["hook"] or (
        f"你是不是也覺得，{topic} 聽起來很重要，真正開始做時卻不知道該從哪裡下手？"
    )
    context = (
        f"真正的關鍵，不是單純增加工具，而是把「{topic}」拆成可以追蹤、驗證和持續改善的流程。"
    )
    if source_brief["target_audience"]:
        context = f"這份內容是為「{source_brief['target_audience']}」準備。{context}"
    if source_brief["angle"]:
        context = f"{context}這次會從「{source_brief['angle']}」切入，讓討論聚焦在受眾真正需要的判斷。"

    insight = "先定義輸入與成功標準，再讓 AI 處理重複判斷；只有遇到風險、衝突或信心不足時，才交回給人決策。"
    if source_brief["key_points"]:
        insight = (
            f"這次要掌握的關鍵重點是：{'；'.join(source_brief['key_points'])}。"
            f"{insight}"
        )

    action_parts = [
        "第一步整理內容需求，第二步自動研究與創作，第三步同步準備分鏡和發布素材，最後再由人完成一次整體核准。"
    ]
    if source_brief["evidence_needed"]:
        action_parts.append(
            f"為了讓內容站得住腳，製作前需要確認：{'；'.join(source_brief['evidence_needed'])}。"
        )
    if source_brief["production_notes"]:
        action_parts.append(
            f"呈現與製作會遵循這些備註：{'；'.join(source_brief['production_notes'])}。"
        )
    if source_brief["other_constraints"]:
        action_parts.append(
            f"同時遵守內容限制：{'；'.join(source_brief['other_constraints'])}。"
        )
    action = "".join(action_parts)

    cta = source_brief["cta"] or (
        "把人的時間留給真正重要的判斷，讓流程替你處理剩下的工作。現在就從一個內容任務開始測試。"
    )
    production_direction = "四個步驟依序點亮，最後停在核准按鈕。"
    if source_brief["production_notes"]:
        production_direction = (
            f"{production_direction} 製作備註：{'；'.join(source_brief['production_notes'])}。"
        )
    if source_brief["estimated_effort"]:
        production_direction = (
            f"{production_direction} 預估製作：{source_brief['estimated_effort']}。"
        )

    video_sections = [
        {
            "id": "hook",
            "label": "開場 Hook",
            "voiceover": hook,
            "visual_direction": "快速切換三個常見工作卡點，以大字標出問題。",
        },
        {
            "id": "context",
            "label": "問題背景",
            "voiceover": context,
            "visual_direction": "畫面從零散工具縮放到一條清楚的工作流程。",
        },
        {
            "id": "insight",
            "label": "核心洞察",
            "voiceover": insight,
            "visual_direction": "用四階段流程圖呈現自動執行與人工決策點。",
        },
        {
            "id": "action",
            "label": "行動方法",
            "voiceover": action,
            "visual_direction": production_direction,
        },
        {
            "id": "cta",
            "label": "結尾 CTA",
            "voiceover": cta,
            "visual_direction": "回到產品工作台，顯示任務完成與成果預覽。",
        },
    ]
    format_sections = {
        "short_text_post": [
            {"id": "opening", "label": "主文開場", "voiceover": hook, "visual_direction": "首行直接呈現核心問題。"},
            {"id": "body", "label": "主文內容", "voiceover": f"{context}{insight}{action}", "visual_direction": "使用短段落與易掃讀留白。"},
            {"id": "cta", "label": "互動 CTA", "voiceover": cta, "visual_direction": "以明確提問收尾。"},
        ],
        "thread": [
            {"id": "opening", "label": "第 1 則｜開場", "voiceover": hook, "visual_direction": "第一則建立閱讀承諾。"},
            {"id": "context", "label": "第 2 則｜背景", "voiceover": context, "visual_direction": "每則只保留一個觀點。"},
            {"id": "insight", "label": "第 3 則｜洞察", "voiceover": insight, "visual_direction": "用編號呈現判斷依據。"},
            {"id": "action", "label": "第 4 則｜方法", "voiceover": action, "visual_direction": "提供可執行步驟。"},
            {"id": "cta", "label": "第 5 則｜收尾", "voiceover": cta, "visual_direction": "邀請回覆或收藏。"},
        ],
        "long_text_article": [
            {"id": "opening", "label": "導言", "voiceover": f"{hook}{context}", "visual_direction": "標題與摘要先交代文章承諾。"},
            {"id": "context", "label": "問題與脈絡", "voiceover": context, "visual_direction": "搭配背景資料或時間線。"},
            {"id": "insight", "label": "核心分析", "voiceover": insight, "visual_direction": "以小標與引文框拆解重點。"},
            {"id": "action", "label": "實作方法", "voiceover": action, "visual_direction": "用步驟清單與案例呈現。"},
            {"id": "cta", "label": "結論與下一步", "voiceover": cta, "visual_direction": "摘要重點並提供下一步。"},
        ],
        "carousel_slides": [
            {"id": "opening", "label": "封面", "voiceover": hook, "visual_direction": "一句標題與單一視覺焦點。"},
            {"id": "context", "label": "第 2–3 張｜問題", "voiceover": context, "visual_direction": "一張一個問題或數據。"},
            {"id": "insight", "label": "第 4–5 張｜洞察", "voiceover": insight, "visual_direction": "用圖表或對照卡呈現。"},
            {"id": "action", "label": "第 6 張｜方法", "voiceover": action, "visual_direction": "用步驟卡列出行動。"},
            {"id": "cta", "label": "第 7 張｜CTA", "voiceover": cta, "visual_direction": "收斂成收藏或留言行動。"},
        ],
    }
    sections = format_sections.get(workflow.input.output_type, video_sections)
    expansion_sentences = {
        "opening": [
            f"這份內容會先釐清「{topic}」的討論範圍，再逐步提出可檢驗的判斷與實作方法。",
        ],
        "body": [context, insight, action],
        "hook": [
            f"今天我們不只談表面的熱度，而是要拆解「{topic}」為什麼會被大量討論，以及這股注意力究竟從哪裡開始累積。",
            "如果只看單一數字，很容易高估一時聲量；真正值得觀察的是搜尋、討論與實際行動是否同步上升。",
        ],
        "context": [
            "一個議題快速升溫，通常不會只有單一原因，而是事件時機、社群擴散、既有受眾需求與媒體報導互相推動。",
            "因此分析時要先區分短期爆點與長期趨勢，避免把幾天內的高峰直接解讀成穩定成長。",
            f"回到「{topic}」本身，我們更需要確認討論者是誰、他們在意什麼，以及哪些內容正在反覆被引用。",
        ],
        "insight": [
            "第一個判斷重點是來源品質：同樣一句話，來自官方資料、媒體整理或社群轉述，可信度完全不同。",
            "第二個重點是互動深度。按讚代表看見，留言與分享才比較接近真實興趣，而後續搜尋或轉換更能證明熱度是否延續。",
            "第三個重點是時間。真正具備內容價值的題目，通常在第一波討論後仍會出現新的角度，而不是隔天就完全消失。",
        ],
        "action": [
            "實際製作內容時，可以先整理三個可靠來源，再列出已確認事實、合理推論與個人觀點，讓觀眾清楚知道每句話的依據。",
            "接著挑出最有爭議或最容易被誤解的一點作為開場，主體則用具體例子補足脈絡，避免只重複網路上的熱門說法。",
            "最後加入可驗證的下一步，例如持續追蹤搜尋趨勢、官方更新或使用者回饋，讓內容不只追逐流量，也能累積長期可信度。",
        ],
        "cta": [
            f"如果你也在觀察「{topic}」，不妨先把你看到的來源和判斷標準記下來，再比較一週後哪些訊號仍然成立。",
            "也歡迎分享你最在意的觀察角度，下一次我們可以針對資料、受眾反應或實際影響做更深入的拆解。",
        ],
    }
    sections = _expand_sections(
        sections,
        expansion_sentences,
        target_word_count,
        topic=topic,
    )
    full_text = "\n\n".join(section["voiceover"] for section in sections)
    actual_word_count = _content_length(full_text)
    return {
        "title": f"{topic}：從零散操作到自動內容流程",
        "summary": _build_script_summary(
            topic=topic,
            goal=goal,
            voice=voice,
            reference_note=reference_note,
            source_brief=source_brief,
        ),
        "sections": sections,
        "full_text": full_text,
        "word_count": actual_word_count,
        "target_word_count": target_word_count,
        "word_count_difference": actual_word_count - target_word_count,
        "quality_score": None,
        "quality_status": "local_checks_only",
        "quality_checks": _quality_checks(full_text, target_word_count, sections),
        "review_notes": [
            "開場在前兩句建立明確痛點。",
            "內容結構符合 Hook、洞察、方法、CTA。",
            *(
                ["已將內容機會 Brief 套用至目標受眾、角度、Hook、重點、證據需求、製作備註與 CTA。"]
                if source_brief["has_structured_brief"]
                else []
            ),
            *( ["已套用參考資料優先順序與創作邊界。"] if reference_analysis["has_references"] else [] ),
            "目前僅完成本機結構檢查；正式上線後須由真實 Research Agent 補上外部來源與人工編審。",
        ],
        "source_brief": source_brief,
        "reference_analysis": reference_analysis,
        "research": {
            "summary": f"目前為本地示範模式，已依使用者需求整理「{topic}」的內容框架；尚未連接外部搜尋來源。",
            "claims_checked": 0,
            "external_verification": False,
            "sources": [
                {"title": "使用者提供的內容需求", "type": "primary_input", "status": "provided"},
                {"title": "系統工作流與品牌語氣設定", "type": "system_context", "status": "configured"},
                *[
                    {
                        "title": source["name"],
                        "type": source["kind"],
                        "status": "provided",
                    }
                    for source in reference_analysis["sources"]
                ],
            ],
        },
    }


def _parse_opportunity_brief(constraints: list[str]) -> dict[str, Any]:
    """解析內容機會交接時放在 constraints 的 Brief，不影響一般自由格式限制。"""
    brief: dict[str, Any] = {
        "has_structured_brief": False,
        "target_audience": "",
        "angle": "",
        "hook": "",
        "key_points": [],
        "cta": "",
        "estimated_effort": "",
        "production_notes": [],
        "evidence_needed": [],
        "other_constraints": [],
    }

    for raw_constraint in constraints:
        constraint = " ".join(raw_constraint.split()).strip()
        if not constraint:
            continue
        match = re.match(r"^([^：:]+)[：:]\s*(.+)$", constraint)
        if not match:
            brief["other_constraints"].append(constraint)
            continue

        label = re.sub(r"\s+", "", match.group(1)).casefold()
        field = BRIEF_FIELD_LABELS.get(label)
        if not field:
            brief["other_constraints"].append(constraint)
            continue

        value = match.group(2).strip()
        brief["has_structured_brief"] = True
        if field in BRIEF_LIST_FIELDS:
            values = [item.strip() for item in re.split(r"[；;]", value) if item.strip()]
            for item in values:
                if item not in brief[field]:
                    brief[field].append(item)
        elif not brief[field]:
            brief[field] = value

    return brief


def _build_script_summary(
    *,
    topic: str,
    goal: str,
    voice: str,
    reference_note: str,
    source_brief: dict[str, Any],
) -> str:
    summary = f"以「{topic}」為主題，製作一份符合「{goal}」目標、採用「{voice}」語氣的內容{reference_note}。"
    brief_context: list[str] = []
    if source_brief["target_audience"]:
        brief_context.append(f"受眾為「{source_brief['target_audience']}」")
    if source_brief["angle"]:
        brief_context.append(f"角度為「{source_brief['angle']}」")
    if brief_context:
        summary = f"{summary}內容 Brief 指定{'，'.join(brief_context)}。"
    return summary


def _content_length(text: str) -> int:
    return len("".join(text.split()))


def _quality_checks(
    text: str, target_word_count: int, sections: list[dict[str, str]]
) -> dict[str, Any]:
    actual = _content_length(text)
    sentences = [item.strip() for item in re.split(r"[。！？!?]", text) if item.strip()]
    prefixes = [item[:8] for item in sentences if len(item) >= 8]
    repeated_prefixes = len(prefixes) - len(set(prefixes))
    ratio = actual / target_word_count if target_word_count else 0
    return {
        "target_adherence": 0.9 <= ratio <= 1.15,
        "structure_present": len(sections) >= 3,
        "repeated_sentence_prefixes": repeated_prefixes,
        "repetition_check_passed": repeated_prefixes <= max(2, len(sentences) // 10),
        "basis": "deterministic_local_checks",
    }


def _expand_sections(
    sections: list[dict[str, str]],
    sentence_bank: dict[str, list[str]],
    target_word_count: int,
    *,
    topic: str = "內容主題",
) -> list[dict[str, str]]:
    """將本地示範稿擴寫至接近目標字數。

    先使用人工撰寫的句庫；長文超過句庫容量時，再以結構化且可重現的
    檢查維度補足。這讓本機 provider 也能遵守 1,500–5,000 字的需求，
    同時保留正式 Writer Agent 日後取代此函式的介面。
    """
    expanded = [dict(section) for section in sections]
    queues = {key: list(values) for key, values in sentence_bank.items()}

    while _content_length("".join(item["voiceover"] for item in expanded)) < target_word_count:
        added = False
        for section in expanded:
            queue = queues.get(section["id"], [])
            if not queue:
                continue
            sentence = queue.pop(0)
            section["voiceover"] = f"{section['voiceover']}{sentence}"
            added = True
            if _content_length("".join(item["voiceover"] for item in expanded)) >= target_word_count:
                break
        if not added:
            break

    # 長篇內容不能因固定句庫用完而停在約 800 字。以下句子由多組維度交叉
    # 組合，避免逐句完全重複，並將內容放在背景、洞察與行動段落，而不是
    # 不斷拉長 Hook 或 CTA。
    expandable = [section for section in expanded if section["id"] not in {"cta"}]
    dimensions = [
        "受眾的真實情境",
        "輸入資料的完整度",
        "成功標準",
        "人工接手機制",
        "錯誤與風險邊界",
        "時間與成本",
        "團隊責任分工",
        "品質檢查",
        "試行範圍",
        "後續成效追蹤",
    ]
    actions = [
        "列出目前做法與預期結果的差距",
        "用一個小規模案例驗證假設",
        "把可觀察訊號與主觀判斷分開記錄",
        "確認資料來源、日期與適用條件",
        "先定義何時必須交回給人處理",
        "比較導入前後的流程成本與品質",
    ]
    outcomes = [
        "避免團隊只看到工具功能，卻忽略實際工作條件",
        "讓每個結論都有可回查的依據",
        "及早發現不適合自動化的例外",
        "把一次性的嘗試變成可以重複改善的流程",
        "讓決策者清楚知道收益、限制與下一步",
        "降低把短期現象誤判為長期趨勢的風險",
    ]
    generated_index = 0
    max_generated_sentences = 300
    while (
        expandable
        and _content_length("".join(item["voiceover"] for item in expanded)) < target_word_count
        and generated_index < max_generated_sentences
    ):
        dimension = dimensions[generated_index % len(dimensions)]
        target_section = expandable[generated_index % len(expandable)]
        action = actions[
            (generated_index * 2 + generated_index // len(dimensions)) % len(actions)
        ]
        outcome = outcomes[
            (generated_index * 3 + generated_index // len(actions)) % len(outcomes)
        ]
        sentence_templates = {
            "context": (
                f"理解「{topic}」時，也要把焦點放回{dimension}：先{action}，"
                f"再確認是否具備擴大條件，才能{outcome}。"
            ),
            "insight": (
                f"下一個判斷維度是{dimension}。可以先{action}，再把結果納入決策；"
                f"這會{outcome}。"
            ),
            "action": (
                f"落地執行時，把{dimension}加入檢查清單，先{action}，"
                f"再決定下一步，以便{outcome}。"
            ),
            "opening": (
                f"從{dimension}來看，關鍵不是追求單一答案；團隊可先{action}，"
                f"藉此{outcome}。"
            ),
            "body": (
                f"另一個值得拆解的面向是{dimension}。團隊若能{action}，"
                f"就能{outcome}。"
            ),
        }
        sentence = sentence_templates.get(
            target_section["id"],
            f"針對{dimension}，建議{action}，這能{outcome}。",
        )
        target_section["voiceover"] = f"{target_section['voiceover']}{sentence}"
        generated_index += 1
    return expanded


def build_production_preview(workflow: WorkflowRun) -> dict[str, Any]:
    script = workflow.artifacts.get("script", {})
    sections = script.get("sections", [])
    is_video = workflow.input.output_type in {"short_video", "long_video"}
    is_carousel = workflow.input.output_type == "carousel_slides"
    seconds_per_character = 0.22
    storyboard = [
        {
            "shot": index + 1,
            "section": section["label"],
            "voiceover": section["voiceover"],
            "visual": section["visual_direction"],
            "broll_queries": [
                f"{workflow.input.topic} {section['label']}",
                section["visual_direction"],
            ],
            "duration_seconds": (
                max(4, round(_content_length(section["voiceover"]) * seconds_per_character))
                if is_video
                else None
            ),
            "broll_duration_seconds": (
                2 if workflow.input.output_type == "short_video" and index == 0
                else 3 if workflow.input.output_type == "short_video"
                else 4 if workflow.input.output_type == "long_video"
                else None
            ),
            "visual_type": (
                "實測／前後比較" if any(keyword in section["visual_direction"] for keyword in ("比較", "測試", "實測"))
                else "螢幕錄影／操作示範" if any(keyword in section["visual_direction"] for keyword in ("畫面", "介面", "操作", "流程"))
                else "產品實拍／特寫" if any(keyword in section["visual_direction"] for keyword in ("產品", "特寫", "開箱"))
                else "情境 B-roll"
            ),
            "visual_purpose": (
                "證明口播主張" if any(keyword in section["visual_direction"] for keyword in ("比較", "測試", "數據", "成果"))
                else "解釋操作／流程" if any(keyword in section["visual_direction"] for keyword in ("步驟", "操作", "流程", "介面"))
                else "補充情境並維持節奏"
            ),
            "transition": "直接切入／先看成果" if index == 0 else "直接切換（Hard cut）",
            "on_screen_text": section["label"] if index in {0, len(sections) - 1} else "",
            "timing_rationale": (
                "前 6 秒優先快速兌現標題承諾" if index == 0
                else "在段落語意轉折處提供有意義的畫面變化"
            ),
        }
        for index, section in enumerate(sections)
        if is_video or is_carousel
    ]
    platform_posts = []
    for platform in workflow.input.platforms:
        label = PLATFORM_LABELS.get(platform, platform.title())
        platform_copy = {
            "youtube": ("完整拆解", "訂閱並留言你想看的下一個案例。"),
            "instagram": ("重點快速看", "收藏這篇，實作時回來逐項確認。"),
            "threads": ("一起拆解", "回覆你遇到的情境，我們一起比較做法。"),
            "linkedin": ("團隊實務觀點", "分享給正在規劃流程的夥伴。"),
        }.get(platform, ("內容摘要", "收藏這份流程。"))
        platform_posts.append(
            {
                "platform": platform,
                "label": label,
                "title": f"{workflow.input.topic}｜{platform_copy[0]}",
                "caption": f"從受眾情境、判斷依據到實作步驟，重新整理「{workflow.input.topic}」，並標示需要人工確認的邊界。",
                "cta": platform_copy[1],
                "hashtags": ["#AI工作流", "#內容行銷", "#自動化"],
            }
        )

    return {
        "content_kind": workflow.input.output_type,
        "storyboard": storyboard,
        "editorial_plan": (
            [
                {"section": section["label"], "purpose": section["visual_direction"]}
                for section in sections
            ]
            if not is_video and not is_carousel
            else []
        ),
        "visual_plan": {
            "style": "Clean Command SaaS editorial",
            "palette": ["#1E3A5F", "#2563EB", "#F8FAFC"],
            "image_prompts": [
                f"現代內容團隊正在規劃 {workflow.input.topic}，深藍與亮藍品牌配色，乾淨專業",
                "四階段 AI 工作流程資訊圖，自動代理與人工核准形成清楚對比",
            ],
            "broll_queries": [workflow.input.topic, "AI content workflow team", "content planning dashboard"],
        },
        "distribution_kit": platform_posts,
        "estimated_duration_seconds": (
            sum(item["duration_seconds"] or 0 for item in storyboard) if is_video else None
        ),
        "preview_ready": bool(sections and platform_posts),
    }
