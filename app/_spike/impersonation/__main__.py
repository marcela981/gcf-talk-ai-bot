"""SPIKE — REMOVE BEFORE MERGE.

One-shot CLI entry for the impersonation probe (bypasses the FastAPI app and the
AppAPI middleware entirely — only needs the ExApp env vars to construct the
client):

    docker exec -it gcf-talk-ai-bot python -m app._spike.impersonation
"""
from __future__ import annotations

import asyncio
import json
import logging

from app._spike.impersonation.probe import run_probe


def _main() -> None:  # pragma: no cover — spike entry point
    logging.basicConfig(level=logging.INFO)
    report = asyncio.run(run_probe())
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    _main()
