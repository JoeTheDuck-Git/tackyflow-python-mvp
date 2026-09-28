from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic.networks import HttpUrl


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class VisibleStage(StrEnum):
    REQUIREMENTS = "requirements"
    AI_CREATION = "ai_creation"
    PRODUCTION_PACKAGE = "production_package"
    APPROVAL_PUBLISH = "approval_publish"


class WorkflowStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    WAITING_FOR_HUMAN = "waiting_for_human"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PublicationStatus(StrEnum):
    READY = "ready"
    RETURNED = "returned"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class DecisionAction(StrEnum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    REJECT = "reject"


SUPPORTED_PLATFORMS = {"youtube", "instagram", "threads", "linkedin"}
SUPPORTED_OUTPUT_TYPES = {
    "short_video",
    "long_video",
    "short_text_post",
    "thread",
    "long_text_article",
    "carousel_slides",
}


class OpportunityGenerationStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class OpportunityItemStatus(StrEnum):
    NEW = "new"
    SAVED = "saved"
    DISMISSED = "dismissed"
    ADOPTED = "adopted"


class OpportunityFitLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class OpportunityDataConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AgentResult(BaseModel):
    agent: str
    status: str = "passed"
    confidence: float = Field(ge=0, le=1)
    risk_level: RiskLevel = RiskLevel.LOW
    summary: str
    issues: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    artifact: dict[str, Any] = Field(default_factory=dict)
    confidence_basis: str = Field(default="provider_reported", max_length=100)
    is_simulated: bool = False


class HumanRequest(BaseModel):
    reason: str
    question: str
    suggested_action: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ExecutionLogEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = Field(default_factory=utc_now)
    stage: VisibleStage
    event_type: str
    status: str
    title: str
    summary: str
    agent: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    risk_level: RiskLevel | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ReferenceMaterial(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(
        default="general",
        pattern="^(brand_brief|product_facts|owned_content|creator_reference|general)$",
    )
    content: str = Field(default="", max_length=50000)
    source_url: str = Field(default="", max_length=2000)
    focus: str = Field(default="整體參考", max_length=200)

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            return ""
        parsed = HttpUrl(normalized)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("source_url must use http or https")
        return str(parsed)


class WorkflowInput(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    goal: str = Field(min_length=1, max_length=100)
    platforms: list[str] = Field(min_length=1, max_length=4)
    output_type: str = Field(min_length=1, max_length=100)
    target_word_count: int = Field(default=500, ge=200, le=5000)
    brand_voice: str = Field(default="品牌預設語氣", max_length=500)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    reference_materials: list[ReferenceMaterial] = Field(default_factory=list, max_length=20)
    reference_boundary: str = Field(default="", max_length=1000)
    workspace_id: str = Field(default="default", min_length=1, max_length=100)
    source_opportunity_id: str | None = Field(default=None, max_length=100)
    source_generation_id: str | None = Field(default=None, max_length=100)
    target_audience: str = Field(default="", max_length=500)
    language: str = Field(default="繁體中文", max_length=100)
    region: str = Field(default="台灣", max_length=100)
    brand_name: str = Field(default="", max_length=200)

    @field_validator("topic", "goal", "output_type", "workspace_id")
    @classmethod
    def normalize_workflow_required_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("value must contain non-whitespace characters")
        return normalized

    @field_validator("platforms", "constraints")
    @classmethod
    def normalize_workflow_lists(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = " ".join(value.split())
            key = item.casefold()
            if item and key not in seen:
                normalized.append(item)
                seen.add(key)
        return normalized

    @field_validator("platforms")
    @classmethod
    def require_workflow_platforms(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("platforms must contain at least one non-whitespace value")
        unsupported = sorted(set(values) - SUPPORTED_PLATFORMS)
        if unsupported:
            raise ValueError(f"unsupported platforms: {', '.join(unsupported)}")
        return values

    @field_validator("output_type")
    @classmethod
    def require_supported_output_type(cls, value: str) -> str:
        if value not in SUPPORTED_OUTPUT_TYPES:
            raise ValueError("unsupported output_type")
        return value

    @field_validator("constraints")
    @classmethod
    def limit_constraint_size(cls, values: list[str]) -> list[str]:
        if any(len(value) > 1000 for value in values):
            raise ValueError("each constraint must be at most 1000 characters")
        return values

    @field_validator("target_audience", "language", "region", "brand_name")
    @classmethod
    def normalize_context_text(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("source_opportunity_id", "source_generation_id")
    @classmethod
    def normalize_source_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("source id must not be blank")
        return normalized

    @model_validator(mode="after")
    def require_complete_source_trace(self) -> "WorkflowInput":
        if bool(self.source_opportunity_id) != bool(self.source_generation_id):
            raise ValueError(
                "source_opportunity_id and source_generation_id must be provided together"
            )
        return self


class WorkflowRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    input: WorkflowInput
    stage: VisibleStage = VisibleStage.REQUIREMENTS
    status: WorkflowStatus = WorkflowStatus.DRAFT
    agent_results: list[AgentResult] = Field(default_factory=list)
    execution_log: list[ExecutionLogEntry] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    human_request: HumanRequest | None = None
    revision_count: int = 0
    stage_agent_positions: dict[str, int] = Field(default_factory=dict)
    revision_history: list[dict[str, Any]] = Field(default_factory=list)
    last_review_feedback: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class HumanDecision(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=2000)
    action: DecisionAction | None = None

    @model_validator(mode="after")
    def align_action_with_approval(self) -> "HumanDecision":
        if self.action is None:
            self.action = DecisionAction.APPROVE if self.approved else DecisionAction.REJECT
        if self.action == DecisionAction.APPROVE and not self.approved:
            raise ValueError("approve action requires approved=true")
        if self.action != DecisionAction.APPROVE and self.approved:
            raise ValueError("non-approve action requires approved=false")
        if self.action == DecisionAction.REQUEST_CHANGES and not self.note.strip():
            raise ValueError("request_changes requires review feedback")
        return self


class StageManagementRequest(BaseModel):
    target_stage: VisibleStage
    note: str = Field(default="由內容負責人調整階段", max_length=1000)


class PublicationManagementRequest(BaseModel):
    status: PublicationStatus
    scheduled_at: datetime | None = None
    published_url: str = Field(default="", max_length=2000)
    target_platform: str = Field(default="", max_length=100)
    note: str = Field(default="由發布中心更新", max_length=1000)

    @model_validator(mode="after")
    def validate_schedule(self) -> "PublicationManagementRequest":
        if self.status == PublicationStatus.SCHEDULED:
            if self.scheduled_at is None:
                raise ValueError("scheduled_at is required for scheduled publication")
            if self.scheduled_at.tzinfo is None or self.scheduled_at.utcoffset() is None:
                raise ValueError("scheduled_at must include a timezone")
            if self.scheduled_at <= utc_now():
                raise ValueError("scheduled_at must be in the future")
        if self.published_url:
            parsed = HttpUrl(self.published_url.strip())
            if parsed.scheme not in {"http", "https"}:
                raise ValueError("published_url must use http or https")
            self.published_url = str(parsed)
        self.target_platform = " ".join(self.target_platform.split())
        return self


class WorkflowReopenRequest(BaseModel):
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str) -> str:
        return " ".join(value.split())


class WorkflowArchiveRequest(BaseModel):
    archived: bool = True


class ScriptRevisionRequest(BaseModel):
    full_text: str = Field(min_length=1, max_length=50000)
    regenerate_production: bool = False

    @field_validator("full_text")
    @classmethod
    def normalize_script_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("full_text must contain non-whitespace characters")
        return normalized


class VisualCue(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=100)
    shot: int = Field(ge=1, le=100)
    cue_type: str = Field(
        default="broll",
        pattern="^(broll|product_shot|screen_recording|image|text_overlay|subtitle|title_card)$",
    )
    label: str = Field(min_length=1, max_length=200)
    start_seconds: int = Field(ge=0, le=7200)
    duration_seconds: int = Field(ge=1, le=1800)
    visual_description: str = Field(default="", max_length=2000)
    purpose: str = Field(default="", max_length=500)
    search_query: str = Field(default="", max_length=1000)
    source: str = Field(default="manual", pattern="^(ai|manual)$")

    @field_validator("label")
    @classmethod
    def normalize_visual_label(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("label must contain non-whitespace characters")
        return normalized

    @field_validator("visual_description", "purpose", "search_query")
    @classmethod
    def normalize_visual_text(cls, value: str) -> str:
        return " ".join(value.split())


class ProductionPlanRevisionRequest(BaseModel):
    visual_cues: list[VisualCue] = Field(default_factory=list, max_length=100)


class ImageGenerationRequest(BaseModel):
    shot: int = Field(ge=1, le=100)
    label: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=20, max_length=8000)
    aspect_ratio: str = Field(default="16:9", pattern=r"^(16:9|9:16|1:1|4:5)$")

    @field_validator("label", "prompt")
    @classmethod
    def normalize_image_generation_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must contain non-whitespace characters")
        return normalized


class OpportunityRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    audience: str = Field(default="", max_length=500)
    platforms: list[str] = Field(
        default_factory=lambda: ["youtube", "instagram"], min_length=1, max_length=4
    )
    goal: str = Field(default="education", max_length=100)
    preferred_formats: list[str] = Field(default_factory=list, max_length=8)
    region: str = Field(default="台灣", max_length=100)
    language: str = Field(default="繁體中文", max_length=100)
    brand_name: str = Field(default="", max_length=200)
    brand_voice: str = Field(default="專業、清楚，但保有自然的對話感", max_length=500)
    brand_brief: str = Field(default="", max_length=10000)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    reference_materials: list[ReferenceMaterial] = Field(default_factory=list, max_length=20)
    reference_urls: list[str] = Field(default_factory=list, max_length=20)
    count: int = Field(default=4, ge=4, le=8)
    variation: int = Field(default=0, ge=0, le=100000)
    exclude_topics: list[str] = Field(default_factory=list, max_length=100)
    workspace_id: str = Field(default="default", min_length=1, max_length=100)

    @field_validator(
        "topic",
        "goal",
        "region",
        "language",
        "brand_voice",
        "workspace_id",
    )
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("value must contain non-whitespace characters")
        return normalized

    @field_validator(
        "audience",
        "brand_name",
        "brand_brief",
    )
    @classmethod
    def normalize_optional_text(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator(
        "platforms",
        "preferred_formats",
        "constraints",
        "reference_urls",
        "exclude_topics",
    )
    @classmethod
    def normalize_text_list(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = " ".join(value.split())
            key = item.casefold()
            if item and key not in seen:
                normalized.append(item)
                seen.add(key)
        return normalized

    @field_validator("platforms")
    @classmethod
    def require_normalized_platforms(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("platforms must contain at least one non-whitespace value")
        return values

    @field_validator("reference_urls")
    @classmethod
    def validate_reference_urls(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            parsed = HttpUrl(value)
            if parsed.scheme not in {"http", "https"}:
                raise ValueError("reference URLs must use http or https")
            normalized.append(str(parsed))
        return normalized


class OpportunityScoreBreakdown(BaseModel):
    relevance: int = Field(ge=0, le=100)
    novelty: int = Field(ge=0, le=100)
    audience_value: int = Field(ge=0, le=100)
    feasibility: int = Field(ge=0, le=100)


class OpportunityBrief(BaseModel):
    target_audience: str = "未記錄"
    angle: str = "未記錄"
    hook: str = ""
    key_points: list[str] = Field(default_factory=list)
    cta: str = ""
    evidence_needed: list[str] = Field(default_factory=list)
    estimated_effort: str = "待評估"
    production_notes: list[str] = Field(default_factory=list)
    recommended_formats: list[str] = Field(default_factory=list)
    recommended_platforms: list[str] = Field(default_factory=list)


class OpportunitySignal(BaseModel):
    name: str
    source: str
    observed_at: datetime = Field(default_factory=utc_now)
    is_live: bool = False
    confidence: float = Field(ge=0, le=1)
    summary: str
    source_url: str | None = Field(default=None, max_length=2000)
    acquisition_method: str = ""
    completeness: float | None = Field(default=None, ge=0, le=1)
    requires_human_review: bool = False
    excerpt: str = Field(default="", max_length=1200)


class ContentOpportunity(BaseModel):
    id: str
    type: str
    topic: str
    description: str
    rationale: str
    recommended_formats: list[str]
    recommended_platforms: list[str]
    score: int = Field(ge=0, le=100)
    score_breakdown: OpportunityScoreBreakdown
    status: OpportunityItemStatus = OpportunityItemStatus.NEW
    feedback_note: str = Field(default="", max_length=2000)
    adopted_workflow_id: str | None = Field(default=None, max_length=100)
    fit_level: OpportunityFitLevel = OpportunityFitLevel.MEDIUM
    scoring_method: str = "local_rule_v2_no_live_signals"
    data_confidence: OpportunityDataConfidence = OpportunityDataConfidence.LOW
    brief: OpportunityBrief = Field(default_factory=OpportunityBrief)


class OpportunityGenerationEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    action: str
    occurred_at: datetime = Field(default_factory=utc_now)
    item_id: str | None = None
    from_topic: str | None = None
    to_topic: str | None = None
    note: str = ""


class OpportunityResponse(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    status: OpportunityGenerationStatus = OpportunityGenerationStatus.COMPLETED
    parent_generation_id: str | None = None
    revision: int = 0
    request_hash: str = ""
    request: OpportunityRequest = Field(
        default_factory=lambda: OpportunityRequest(topic="未記錄")
    )
    seed: str
    topic_type: str = "pending"
    topic_type_label: str = "準備中"
    generation_mode: str = "local_rule"
    provider: str = "local_rule"
    model: str = "deterministic_heuristics_v2"
    prompt_version: str = "opportunity-v2"
    score_version: str = "heuristic-v2"
    context_summary: str = ""
    history_topics_considered: int = 0
    candidates_evaluated: int = 0
    has_live_signals: bool = False
    signals: list[OpportunitySignal] = Field(default_factory=list)
    source_captures: list[dict[str, Any]] = Field(default_factory=list)
    opportunities: list[ContentOpportunity] = Field(default_factory=list)
    quality_review: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, int | float] | None = None
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    error: str | None = None
    events: list[OpportunityGenerationEvent] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utc_now)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class OpportunityItemUpdate(BaseModel):
    status: OpportunityItemStatus | None = None
    feedback_note: str | None = Field(default=None, max_length=2000)
    adopted_workflow_id: str | None = Field(default=None, max_length=100)

    @field_validator("adopted_workflow_id")
    @classmethod
    def normalize_adopted_workflow_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("adopted_workflow_id must not be blank")
        return normalized


class OpportunityRegenerateRequest(BaseModel):
    modification_instruction: str | None = Field(default=None, max_length=2000)
    topic: str | None = Field(default=None, min_length=1, max_length=200)
    audience: str | None = Field(default=None, max_length=500)
    platforms: list[str] | None = Field(default=None, min_length=1, max_length=4)
    goal: str | None = Field(default=None, max_length=100)
    preferred_formats: list[str] | None = Field(default=None, max_length=8)
    region: str | None = Field(default=None, max_length=100)
    language: str | None = Field(default=None, max_length=100)
    brand_name: str | None = Field(default=None, max_length=200)
    brand_voice: str | None = Field(default=None, max_length=500)
    brand_brief: str | None = Field(default=None, max_length=10000)
    constraints: list[str] | None = Field(default=None, max_length=20)
    reference_materials: list[ReferenceMaterial] | None = Field(default=None, max_length=20)
    reference_urls: list[str] | None = Field(default=None, max_length=20)
    exclude_topics: list[str] = Field(default_factory=list, max_length=100)
    variation: int | None = Field(default=None, ge=0, le=100000)

    @field_validator("topic", "goal", "region", "language", "brand_voice")
    @classmethod
    def normalize_required_override(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("value must contain non-whitespace characters")
        return normalized

    @field_validator("audience", "brand_name", "brand_brief", "modification_instruction")
    @classmethod
    def normalize_optional_override(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(value.split())

    @field_validator(
        "platforms",
        "preferred_formats",
        "constraints",
        "reference_urls",
        "exclude_topics",
    )
    @classmethod
    def normalize_override_list(
        cls, values: list[str] | None
    ) -> list[str] | None:
        if values is None:
            return None
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = " ".join(value.split())
            key = item.casefold()
            if item and key not in seen:
                normalized.append(item)
                seen.add(key)
        return normalized

    @field_validator("platforms")
    @classmethod
    def require_normalized_platform_overrides(
        cls, values: list[str] | None
    ) -> list[str] | None:
        if values is not None and not values:
            raise ValueError("platforms must contain at least one non-whitespace value")
        return values

    @field_validator("reference_urls")
    @classmethod
    def validate_reference_url_overrides(
        cls, values: list[str] | None
    ) -> list[str] | None:
        if values is None:
            return None
        return [str(HttpUrl(value)) for value in values]
