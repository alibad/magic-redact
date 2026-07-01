"""magic-redact core — portable, model-free redaction engine.

Runs identically on Windows and macOS. Platform code (detection backends, the
optional diffusion Tier-3) lives in ../win and ../mac and imports from here.
"""
from .identity import Identity, generate_identity, normalize_field
from .pipeline import default_strategies, detect_and_redact, redact, replace_strategies
from .strategies.base import RegionSpec

__all__ = [
    "Identity", "generate_identity", "normalize_field",
    "redact", "detect_and_redact", "default_strategies", "replace_strategies", "RegionSpec",
]
