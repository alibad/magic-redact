"""Draw Things Tier-3 diffusion adapter (macOS / Apple Silicon target).

magic-redact's Tier-3 is an *optional* upgrade for maximum realism on a single
region (font-preserving text edit, pose-matched face swap). On Windows that work
is sent to the local Qwen server; on the Mac the equivalent backend is
**Draw Things**, which runs Qwen-Image / Qwen-Image-Edit natively on Apple
Silicon and exposes a local HTTP API.

This adapter is the Mac counterpart of the Windows Qwen adapter and is meant to
plug into the same Tier-3 hook the win target defines (a callable that takes a
region crop + an instruction and returns a replacement PIL image).

Server Offload / Bridge
-----------------------
Draw Things can run the model on *another* machine via its "Server Offload" /
gRPCServerCLI bridge — e.g. point it at the user's RTX 5090 box on the LAN with
no change to this code. From this adapter's point of view it's still just an HTTP
endpoint; only the base URL changes. Defaults to ``http://127.0.0.1:7860`` (the
Draw Things HTTP API default); override with ``MAGIC_REDACT_DRAWTHINGS_URL``.

Status: implemented vs stubbed
------------------------------
* ``DrawThingsClient.health()``            — IMPLEMENTED (simple GET probe).
* ``DrawThingsClient.txt2img()``           — IMPLEMENTED against the documented
  Draw Things ``/sdapi/v1/txt2img`` REST shape (A1111-compatible). Returns a PIL
  image. NOT yet verified on-device — the exact field names/params can vary by
  Draw Things version, so treat as "ready to test, may need a tweak".
* ``generate_portrait()``                  — IMPLEMENTED (wraps txt2img with an
  ID-photo prompt). Pending on-device verification.
* ``instruction_edit_region()``            — STUBBED. Qwen-Image-Edit instruction
  editing in Draw Things uses img2img + an edit/instruction field whose exact API
  surface must be confirmed on the device. The request is assembled and the code
  path is wired, but it raises NotImplementedError by default until verified, so
  nobody ships an untested edit path. See the inline TODO for what to confirm.

Portability
-----------
Import-clean on Windows. Networking uses the stdlib (``urllib``) so there is no
hard third-party dependency; ``requests`` is used if present but is optional. No
real HTTP call happens at import or construction time.
"""
from __future__ import annotations

import base64
import io
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

from PIL import Image

# Optional: use `requests` if available, else fall back to urllib. Importing is
# guarded so this module loads on a machine without requests installed.
try:  # pragma: no cover - trivial import guard
    import requests  # type: ignore
    _HAS_REQUESTS = True
except Exception:  # noqa: BLE001
    requests = None  # type: ignore
    _HAS_REQUESTS = False


DEFAULT_URL = os.environ.get("MAGIC_REDACT_DRAWTHINGS_URL", "http://127.0.0.1:7860")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class DrawThingsConfig:
    base_url: str = DEFAULT_URL
    # Model name as it appears in Draw Things' API; user-configurable. For the
    # edit path this should be a Qwen-Image-Edit checkpoint.
    model: str = os.environ.get("MAGIC_REDACT_DRAWTHINGS_MODEL", "qwen_image_edit")
    steps: int = 8           # Lightning LoRA-friendly default; raise for quality
    seed: int = -1           # -1 => random; set for reproducibility
    timeout: float = 180.0   # diffusion can be slow on first run / cold model
    # Optional Lightning LoRA for fast sampling on Apple Silicon (set to the
    # LoRA name installed in Draw Things, or leave empty to disable).
    lightning_lora: str = os.environ.get("MAGIC_REDACT_DRAWTHINGS_LORA", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _img_to_b64_png(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _b64_to_img(b64: str) -> Image.Image:
    # Draw Things / A1111 sometimes prefix with a data URI; strip it.
    if "," in b64[:32]:
        b64 = b64.split(",", 1)[1]
    raw = base64.b64decode(b64)
    return Image.open(io.BytesIO(raw)).convert("RGB")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class DrawThingsClient:
    """Thin HTTP client for the local Draw Things API."""

    def __init__(self, config: Optional[DrawThingsConfig] = None):
        self.config = config or DrawThingsConfig()

    # -- low-level transport ------------------------------------------------

    def _post_json(self, path: str, body: dict) -> dict:
        url = self.config.base_url.rstrip("/") + path
        data = json.dumps(body).encode("utf-8")
        if _HAS_REQUESTS:
            resp = requests.post(url, data=data,
                                 headers={"Content-Type": "application/json"},
                                 timeout=self.config.timeout)
            resp.raise_for_status()
            return resp.json()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=self.config.timeout) as r:  # noqa: S310
            return json.loads(r.read().decode("utf-8"))

    def _get(self, path: str, timeout: float = 5.0) -> dict:
        url = self.config.base_url.rstrip("/") + path
        if _HAS_REQUESTS:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
            return json.loads(r.read().decode("utf-8"))

    # -- public API ---------------------------------------------------------

    def health(self) -> bool:
        """Best-effort probe that the Draw Things API is reachable.

        IMPLEMENTED. Tries the A1111-compatible options endpoint; returns True on
        any 2xx JSON response, False on connection error.
        """
        for path in ("/sdapi/v1/options", "/sdapi/v1/sd-models"):
            try:
                self._get(path)
                return True
            except (urllib.error.URLError, OSError, ValueError):
                continue
            except Exception:  # noqa: BLE001 - requests.* errors etc.
                continue
        return False

    def txt2img(
        self,
        prompt: str,
        *,
        width: int = 512,
        height: int = 640,
        negative_prompt: str = "",
        steps: Optional[int] = None,
        seed: Optional[int] = None,
    ) -> Image.Image:
        """Generate an image from a text prompt.

        IMPLEMENTED against the documented Draw Things HTTP API (A1111-compatible
        ``/sdapi/v1/txt2img``). Returns a PIL image. Not yet verified on-device;
        field names/params may need a tweak for your Draw Things build.
        """
        body = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "steps": steps if steps is not None else self.config.steps,
            "seed": seed if seed is not None else self.config.seed,
        }
        if self.config.lightning_lora:
            # A1111-style inline LoRA syntax; Draw Things accepts installed LoRAs.
            body["prompt"] = f"{prompt} <lora:{self.config.lightning_lora}:1>"

        result = self._post_json("/sdapi/v1/txt2img", body)
        images = result.get("images") or []
        if not images:
            raise RuntimeError(f"Draw Things returned no images: {str(result)[:300]}")
        return _b64_to_img(images[0])

    def instruction_edit_region(
        self,
        region_image: Image.Image,
        instruction: str,
        *,
        steps: Optional[int] = None,
        seed: Optional[int] = None,
    ) -> Image.Image:
        """Qwen-Image-Edit instruction edit of a single region crop.

        STUBBED pending on-device verification. The request body is assembled
        below, but the exact Draw Things surface for instruction/edit models is
        version-dependent, so this raises by default rather than shipping an
        unverified edit path.

        To finish on the Mac:
          1. In Draw Things, load a Qwen-Image-Edit model and run one manual edit
             so you can see which API the app exposes (img2img with an edit/
             instruction field, or a dedicated endpoint).
          2. Confirm the field names in the request body below and the response
             shape (base64 in ``images``?).
          3. Remove the ``raise NotImplementedError`` and test against a region
             crop from a real document.
        """
        body = {
            "init_images": [_img_to_b64_png(region_image)],
            "prompt": instruction,          # e.g. "replace the text with: ALONSO"
            "denoising_strength": 0.85,
            "steps": steps if steps is not None else self.config.steps,
            "seed": seed if seed is not None else self.config.seed,
            "width": region_image.width,
            "height": region_image.height,
            # TODO(on-device): Qwen-Image-Edit may expect the instruction in a
            # dedicated field (e.g. "edit_prompt"/"instruction") rather than
            # "prompt". Confirm against the running app, then enable.
        }
        raise NotImplementedError(
            "instruction_edit_region is stubbed pending on-device Draw Things "
            "verification. The request body is prepared; confirm the API surface "
            "for Qwen-Image-Edit on your device, then enable. "
            f"(Prepared body keys: {sorted(body)})"
        )


# ---------------------------------------------------------------------------
# Convenience: a synthetic ID portrait for the face strategy / face-pool refill.
# ---------------------------------------------------------------------------

_PORTRAIT_PROMPT = (
    "passport-style ID photo of one person, neutral expression, plain light "
    "background, evenly lit, front-facing, head and shoulders, photorealistic"
)
_PORTRAIT_NEGATIVE = (
    "multiple people, text, watermark, logo, hands, sunglasses, hat, blurry, "
    "cartoon, illustration"
)


def generate_portrait(
    identity=None,
    *,
    config: Optional[DrawThingsConfig] = None,
    width: int = 512,
    height: int = 640,
    seed: Optional[int] = None,
) -> Image.Image:
    """Generate one synthetic ID-style portrait via Draw Things txt2img.

    IMPLEMENTED (wraps txt2img). Pending on-device verification. ``identity`` is
    accepted so the prompt can be lightly conditioned (sex) for coherence with
    the rest of the redacted document; everything else stays generic on purpose
    (we never want a recognizable real person).
    """
    client = DrawThingsClient(config)
    prompt = _PORTRAIT_PROMPT
    if identity is not None:
        sex = getattr(identity, "sex", None)
        if sex == "M":
            prompt = "male " + prompt
        elif sex == "F":
            prompt = "female " + prompt
        if seed is None and getattr(identity, "seed", None) is not None:
            seed = identity.seed
    return client.txt2img(
        prompt,
        width=width,
        height=height,
        negative_prompt=_PORTRAIT_NEGATIVE,
        seed=seed,
    )


def is_available(config: Optional[DrawThingsConfig] = None) -> bool:
    """True if a Draw Things server is reachable at the configured URL."""
    return DrawThingsClient(config).health()


if __name__ == "__main__":  # pragma: no cover - manual smoke test on the Mac
    cfg = DrawThingsConfig()
    print(f"Draw Things URL: {cfg.base_url}")
    print(f"Reachable: {is_available(cfg)}")
