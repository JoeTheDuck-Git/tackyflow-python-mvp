import base64
from types import SimpleNamespace

import pytest

from app.media.generator import OpenAIImageGenerator


class FakeImages:
    def __init__(self) -> None:
        self.kwargs = None

    def generate(self, **kwargs):
        self.kwargs = kwargs
        png = b"\x89PNG\r\n\x1a\n" + b"fake-image-data"
        return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(png).decode())])


@pytest.mark.asyncio
async def test_openai_image_generator_stores_private_scoped_png(tmp_path) -> None:
    images = FakeImages()
    generator = OpenAIImageGenerator(
        model="gpt-image-test",
        timeout_seconds=30,
        storage_root=tmp_path,
        client_factory=lambda: SimpleNamespace(images=images),
    )

    result = await generator.generate(
        workspace_id="workspace-a",
        workflow_id="workflow-a",
        prompt="一個足夠詳細並可直接生成的產品攝影提示詞，包含光線與構圖。",
        aspect_ratio="9:16",
    )

    assert result.path.is_file()
    assert result.path.stat().st_mode & 0o777 == 0o600
    assert images.kwargs["size"] == "1024x1536"
    assert images.kwargs["output_format"] == "png"
    assert generator.resolve_path(
        workspace_id="workspace-a",
        workflow_id="workflow-a",
        asset_id=result.asset_id,
    ) == result.path
    with pytest.raises(FileNotFoundError):
        generator.resolve_path(
            workspace_id="workspace-a",
            workflow_id="workflow-a",
            asset_id="../../escape",
        )
