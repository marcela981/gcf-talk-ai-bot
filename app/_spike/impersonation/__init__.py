"""SPIKE — REMOVE BEFORE MERGE.

Throwaway scaffolding to validate ADR-016 (identity model for skills): can the
ExApp impersonate the invoking user against Calendar (CalDAV) and Deck (REST) in
this stack? Read-only. Everything under app/_spike/ must be deleted before
merging to main.

Disabled by default. The HTTP router is registered in app/main.py ONLY when
SPIKE_IMPERSONATION_ENABLED=1. The same probe can be run as a one-shot module:

    python -m app._spike.impersonation

See docs/spikes/SPIKE_IMPERSONATION.md for hypotheses H1..H3 and the verdict.
"""
