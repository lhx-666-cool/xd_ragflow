from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "ragflow_sidecar.app:create_app",
        factory=True,
        host=os.getenv("TEXTBOOK_KG_HOST", "127.0.0.1"),
        port=int(os.getenv("TEXTBOOK_KG_PORT", "8890")),
        proxy_headers=False,
    )
