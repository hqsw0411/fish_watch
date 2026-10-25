"""Serve the built frontend and API on loopback only."""
import runtime  # noqa: F401
import uvicorn

if __name__ == "__main__":
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000)
