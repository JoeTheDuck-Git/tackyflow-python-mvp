from __future__ import annotations

from hashlib import sha256
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.opportunity import OpportunityAgent
from app.domain.models import (
    OpportunityGenerationEvent,
    OpportunityRequest,
    OpportunityResponse,
    OpportunitySignal,
)
from app.knowledge.service import KnowledgeService


class OpportunityGraphState(TypedDict, total=False):
    request: OpportunityRequest
    history_topics: list[str]
    signals: list[OpportunitySignal]
    generation_id: str | None
    knowledge_signals: list[OpportunitySignal]
    knowledge_error: str
    source_captures: list[dict]
    primary_error: str
    result: OpportunityResponse


class KnowledgeOpportunityAgent:
    """Python-owned LangGraph orchestration with a legacy-safe fallback path."""

    provider = "python_langgraph"
    generation_mode = "knowledge_augmented"

    def __init__(
        self,
        primary: OpportunityAgent,
        fallback: OpportunityAgent,
        knowledge: KnowledgeService,
        *,
        mode: str = "legacy",
        rollout_percent: int = 0,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.knowledge = knowledge
        self.mode = mode
        self.rollout_percent = max(0, min(100, rollout_percent))
        self.model = getattr(primary, "model", "unspecified")
        self.graph = self._build_graph()

    def _knowledge_enabled(self, request: OpportunityRequest) -> bool:
        if self.mode == "knowledge_primary":
            return True
        if self.mode == "legacy":
            return False
        bucket = int(
            sha256(f"{request.workspace_id}:{request.topic}".encode()).hexdigest()[:8],
            16,
        ) % 100
        return bucket < self.rollout_percent

    async def _retrieve(self, state: OpportunityGraphState) -> OpportunityGraphState:
        request = state["request"]
        errors: list[str] = []
        for url in request.reference_urls[:5]:
            try:
                await self.knowledge.ingest_url(
                    request.workspace_id,
                    url,
                    generation_id=state.get("generation_id"),
                )
            except Exception as exc:
                errors.append(f"crawl:{type(exc).__name__}")
        query = "；".join(
            item
            for item in [
                request.topic,
                request.audience,
                request.goal,
                request.brand_brief[:500],
                " ".join(request.constraints),
            ]
            if item
        )
        try:
            chunks = await self.knowledge.retrieve(
                request.workspace_id,
                query,
                generation_id=state.get("generation_id"),
                mode=self.mode,
            )
        except Exception as exc:
            captures = await self.knowledge.list_captures(
                request.workspace_id,
                generation_id=state.get("generation_id"),
            )
            return {
                "knowledge_error": ",".join(errors + [type(exc).__name__]),
                "source_captures": [item.model_dump(mode="json") for item in captures],
            }
        signals = [
            OpportunitySignal(
                name=chunk.title or f"知識來源 {index}",
                source="llamaindex_retrieval",
                is_live=False,
                confidence=max(0.0, min(1.0, chunk.score)),
                summary=chunk.content[:1800],
                source_url=chunk.source_url,
                acquisition_method=str(chunk.metadata.get("crawler") or "knowledge_index"),
                completeness=chunk.metadata.get("completeness"),
                requires_human_review=bool(
                    chunk.metadata.get("requires_human_review", False)
                ),
                excerpt=str(chunk.metadata.get("excerpt") or chunk.content[:500]),
            )
            for index, chunk in enumerate(chunks, start=1)
            if chunk.score >= 0.35
        ]
        captures = await self.knowledge.list_captures(
            request.workspace_id,
            generation_id=state.get("generation_id"),
        )
        capture_payload = [item.model_dump(mode="json") for item in captures]
        if not signals:
            return {
                "knowledge_error": ",".join(errors + ["no_relevant_chunks"]),
                "source_captures": capture_payload,
            }
        return {"knowledge_signals": signals, "source_captures": capture_payload}

    async def _generate_primary(self, state: OpportunityGraphState) -> OpportunityGraphState:
        try:
            result = await self.primary.generate(
                state["request"],
                state["history_topics"],
                [*state.get("signals", []), *state.get("knowledge_signals", [])],
                state.get("generation_id"),
            )
            augmented = result.model_copy(
                update={
                    "generation_mode": f"{result.generation_mode}+llamaindex",
                    "source_captures": state.get("source_captures", []),
                    "events": result.events
                    + [
                        OpportunityGenerationEvent(
                            action="knowledge_retrieved",
                            note=(
                                f"Python LangGraph 透過 LlamaIndex 提供 "
                                f"{len(state.get('knowledge_signals', []))} 段 Workspace 知識。"
                            ),
                        )
                    ],
                },
                deep=True,
            )
            return {"result": augmented}
        except Exception as exc:
            return {"primary_error": type(exc).__name__}

    async def _fallback_generate(self, state: OpportunityGraphState) -> OpportunityGraphState:
        try:
            result = await self.primary.generate(
                state["request"],
                state["history_topics"],
                state.get("signals", []),
                state.get("generation_id"),
            )
            reason = state.get("knowledge_error") or state.get("primary_error") or "unknown"
            return {
                "result": result.model_copy(
                    update={
                        "source_captures": state.get("source_captures", []),
                        "events": result.events
                        + [
                            OpportunityGenerationEvent(
                                action="knowledge_fallback",
                                note=f"知識流程不可用，已回到原內容機會代理：{reason}",
                            )
                        ]
                    },
                    deep=True,
                )
            }
        except Exception as primary_exc:
            result = await self.fallback.generate(
                state["request"],
                state["history_topics"],
                state.get("signals", []),
                state.get("generation_id"),
            )
            return {
                "result": result.model_copy(
                    update={
                        "events": result.events
                        + [
                            OpportunityGenerationEvent(
                                action="local_rule_fallback",
                                note=f"主要代理不可用，已安全降級：{type(primary_exc).__name__}",
                            )
                        ]
                    },
                    deep=True,
                )
            }

    def _build_graph(self):
        graph = StateGraph(OpportunityGraphState)
        graph.add_node("retrieve_knowledge", self._retrieve)
        graph.add_node("generate_primary", self._generate_primary)
        graph.add_node("fallback", self._fallback_generate)
        graph.add_edge(START, "retrieve_knowledge")
        graph.add_conditional_edges(
            "retrieve_knowledge",
            lambda state: "fallback" if state.get("knowledge_error") else "generate_primary",
        )
        graph.add_conditional_edges(
            "generate_primary",
            lambda state: "fallback" if state.get("primary_error") else END,
        )
        graph.add_edge("fallback", END)
        return graph.compile()

    async def generate(
        self,
        request: OpportunityRequest,
        history_topics: list[str],
        signals: list[OpportunitySignal] | None = None,
        generation_id: str | None = None,
    ) -> OpportunityResponse:
        if not self._knowledge_enabled(request):
            result = await self.primary.generate(
                request, history_topics, signals, generation_id
            )
            if self.mode == "knowledge_shadow":
                try:
                    chunks = await self.knowledge.retrieve(
                        request.workspace_id,
                        request.topic,
                        generation_id=generation_id,
                        mode="knowledge_shadow",
                    )
                    result = result.model_copy(
                        update={
                            "events": result.events
                            + [
                                OpportunityGenerationEvent(
                                    action="knowledge_shadow_evaluated",
                                    note=f"A/B 對照組保留原輸出；知識庫可取得 {len(chunks)} 段。",
                                )
                            ]
                        },
                        deep=True,
                    )
                except Exception:
                    pass
            return result
        final_state = await self.graph.ainvoke(
            {
                "request": request,
                "history_topics": history_topics,
                "signals": signals or [],
                "generation_id": generation_id,
            }
        )
        return final_state["result"]
