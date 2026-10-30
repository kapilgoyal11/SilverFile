"""python -m dashboard  -> serves the review console on http://localhost:8000"""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("dashboard.app:app", host=os.environ.get("HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", "8000")))
