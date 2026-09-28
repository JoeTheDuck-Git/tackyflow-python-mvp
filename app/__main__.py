from __future__ import annotations

import uvicorn

from app.config import settings


def main() -> None:
    """Run the local application with a stable, reload-free entry point."""
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
