from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable

from pydantic import BaseModel, Field


class PromptComparisonJudgment(BaseModel):
    score_a: int = Field(ge=0, le=100)
    score_b: int = Field(ge=0, le=100)
    winner: str = Field(pattern=r"^(a|b|tie)$")
    summary: str = Field(min_length=1, max_length=1000)
    strengths_a: list[str] = Field(default_factory=list, max_length=8)
    strengths_b: list[str] = Field(default_factory=list, max_length=8)
    risks_a: list[str] = Field(default_factory=list, max_length=8)
    risks_b: list[str] = Field(default_factory=list, max_length=8)


def _usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    data = usage.model_dump(mode="json") if usage is not None and hasattr(usage, "model_dump") else {}
    return {
        "input_tokens": int(data.get("input_tokens", 0) or 0),
        "output_tokens": int(data.get("output_tokens", 0) or 0),
        "total_tokens": int(data.get("total_tokens", 0) or 0),
    }


class OpenAIPromptTestRunner:
    """Run two prompt variants concurrently, then judge their anonymized outputs."""

    def __init__(
        self,
        *,
        model: str,
        timeout_seconds: float,
        input_usd_per_million: float = 0,
        output_usd_per_million: float = 0,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.input_usd_per_million = input_usd_per_million
        self.output_usd_per_million = output_usd_per_million
        self.client_factory = client_factory

    def _client(self) -> Any:
        if self.client_factory is not None:
            return self.client_factory()
        from openai import OpenAI

        return OpenAI(timeout=self.timeout_seconds, max_retries=1)

    def _estimated_cost(self, usage: dict[str, int]) -> float | None:
        if self.input_usd_per_million <= 0 and self.output_usd_per_million <= 0:
            return None
        return round(
            usage["input_tokens"] / 1_000_000 * self.input_usd_per_million
            + usage["output_tokens"] / 1_000_000 * self.output_usd_per_million,
            8,
        )

    def _run_variant(
        self,
        *,
        prompt_name: str,
        description: str,
        locked_rules: str,
        instructions: str,
        test_input: str,
        rubric: str,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        response = self._client().responses.create(
            model=self.model,
            store=False,
            reasoning={"effort": "low"},
            max_output_tokens=2500,
            input=[
                {
                    "role": "system",
                    "content": (
                        f"你正在接受 Prompt A/B 測試。扮演「{prompt_name}」。\n"
                        f"任務說明：{description}\n"
                        f"不可覆蓋的規則：{locked_rules}\n"
                        "請直接交付成果，不要描述內部思考，也不要評論測試本身。\n\n"
                        f"本版本業務指示：\n{instructions}"
                    ),
                },
                {
                    "role": "user",
                    "content": f"固定測試輸入：\n{test_input}\n\n驗收規準：\n{rubric}",
                },
            ],
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        output = str(getattr(response, "output_text", "") or "").strip()
        if not output:
            raise RuntimeError("prompt test returned an empty output")
        usage = _usage(response)
        return {
            "output": output,
            "latency_ms": elapsed_ms,
            "usage": usage,
            "estimated_cost_usd": self._estimated_cost(usage),
            "model": getattr(response, "model", self.model),
            "response_id": getattr(response, "id", None),
        }

    def _judge(self, *, test_input: str, rubric: str, result_a: dict[str, Any], result_b: dict[str, Any]) -> dict[str, Any]:
        response = self._client().responses.parse(
            model=self.model,
            store=False,
            reasoning={"effort": "low"},
            input=[
                {
                    "role": "system",
                    "content": (
                        "你是獨立 Prompt 評測員。只依固定輸入與驗收規準評估匿名輸出 A/B。"
                        "檢查正確性、相關性、完整性、可執行性與風險，不可因篇幅較長就給高分。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "test_input": test_input,
                            "rubric": rubric,
                            "output_a": result_a["output"],
                            "output_b": result_b["output"],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            text_format=PromptComparisonJudgment,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("prompt test judge returned no structured result")
        usage = _usage(response)
        return {
            **parsed.model_dump(mode="json"),
            "usage": usage,
            "estimated_cost_usd": self._estimated_cost(usage),
            "model": getattr(response, "model", self.model),
            "response_id": getattr(response, "id", None),
            "evaluation_type": "llm_judge",
        }

    async def run(
        self,
        *,
        prompt: dict[str, str],
        instructions_a: str,
        instructions_b: str,
        test_input: str,
        rubric: str,
    ) -> dict[str, Any]:
        common = {
            "prompt_name": prompt["name"],
            "description": prompt["description"],
            "locked_rules": prompt["locked"],
            "test_input": test_input,
            "rubric": rubric,
        }
        result_a, result_b = await asyncio.gather(
            asyncio.to_thread(self._run_variant, instructions=instructions_a, **common),
            asyncio.to_thread(self._run_variant, instructions=instructions_b, **common),
        )
        judgment = await asyncio.to_thread(
            self._judge,
            test_input=test_input,
            rubric=rubric,
            result_a=result_a,
            result_b=result_b,
        )
        return {"result_a": result_a, "result_b": result_b, "judgment": judgment}
