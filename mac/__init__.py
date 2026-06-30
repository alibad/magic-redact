"""magic-redact macOS (Apple Silicon) target.

Platform-specific pieces that plug into the portable ``core`` engine:

* ``detect_vision``        — Apple Vision detector (loaded as ``vision``).
* ``diffusion_drawthings`` — optional Tier-3 Draw Things adapter.
* ``server``               — the shared, platform-neutral FastAPI service.

See ``mac/README.md`` for the on-device runbook.
"""
