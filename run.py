from __future__ import annotations

import os
import uvicorn

if __name__ == "__main__":
    dev = os.environ.get("ENV", "production").lower() in ("dev", "development", "local")
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=dev,
    )

