from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"


def test_legacy_script_artifact_marks_writer_complete_in_ui():
    javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'workflow.artifacts.script && !workflow.agent_results.some((result) => result.agent === "writer")' in javascript
    assert 'setAgentState("writer", "completed")' in javascript
