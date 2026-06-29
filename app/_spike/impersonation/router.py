"""SPIKE — REMOVE BEFORE MERGE.

FastAPI router for the impersonation probe. Included in app/main.py ONLY when
SPIKE_IMPERSONATION_ENABLED=1.

NOTE on auth: unlike the Files spike, this router is NOT added to the
AppAPIAuthMiddleware `disable_for` list (main.py is left untouched except for the
conditional include). Therefore the HTTP route sits BEHIND the AppAPI shared-
secret middleware and needs valid AppAPI headers to reach. For a no-friction run
prefer the one-shot module instead:

    docker exec -it gcf-talk-ai-bot python -m app._spike.impersonation
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app._spike.impersonation.probe import run_probe

router = APIRouter()


@router.post("/debug/impersonation-spike")  # SPIKE — REMOVE BEFORE MERGE
async def impersonation_spike() -> dict[str, Any]:
    """Run the read-only impersonation probe and return its JSON report."""
    return await run_probe()
