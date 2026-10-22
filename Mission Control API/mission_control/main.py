"""Mission Control API — read-only FastAPI layer over the three job agents.

Run:  uvicorn mission_control.main:app --reload --port 8080   # 8000 = Agent 1's backend, 8001 is Windows-reserved here
Docs: http://localhost:8080/docs
"""
import logging

from .app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = create_app()
