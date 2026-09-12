"""``python -m syllabus_studio`` — run the development server."""

from __future__ import annotations

import uvicorn

from syllabus_studio.config import get_settings


def main() -> None:
    s = get_settings()
    uvicorn.run(
        "syllabus_studio.app:create_app",
        factory=True,
        host=s.host,
        port=s.port,
        reload=s.reload,
        reload_dirs=["src"] if s.reload else None,
    )


if __name__ == "__main__":
    main()
