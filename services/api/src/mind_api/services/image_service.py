"""MIND Image: provider generation plus real local edits with Pillow.

A generated image is only recorded after the bytes decode as a valid image.
"""

from __future__ import annotations

import asyncio
import io
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from mind_ai import ProviderError
from PIL import Image, ImageEnhance, ImageOps
from pydantic import BaseModel, Field

from ..db import session_scope
from ..jobs import JobContext, JobFailed, RetryableJobError, handler
from ..models import GeneratedArtifact, ModelConfig
from ..storage import get_storage
from .artifacts import store_version
from .audit import record_usage
from .providers import adapter_for

MAX_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def inspect_image(data: bytes) -> dict[str, Any]:
    """Decode fully (not just the header) and return basic facts; raises on invalid data."""
    with Image.open(io.BytesIO(data)) as im:
        im.verify()
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        return {"format": im.format, "width": im.width, "height": im.height, "mode": im.mode}


@handler("image.generate")
def image_generate(ctx: JobContext) -> dict[str, Any]:
    p = ctx.payload
    artifact_id = uuid.UUID(p["artifact_id"])
    with session_scope() as db:
        m = db.get(ModelConfig, uuid.UUID(p["model_config_id"]))
        if m is None or m.org_id != ctx.org_id or not m.enabled or "image_generation" not in m.capabilities:
            raise JobFailed("selected image model is not available")
        provider, model_name, price = m.provider, m.model_name, m.price_per_image

    async def go() -> Any:
        a = adapter_for(provider)
        try:
            return await a.generate_image(model_name, p["prompt"], size=p.get("size", "1024x1024"), n=int(p.get("n", 1)))
        finally:
            await a.aclose()

    ctx.progress(10, f"requesting {p.get('n', 1)} image(s) from {provider.kind}")
    try:
        res = asyncio.run(go())
    except ProviderError as exc:
        _mark(artifact_id, "failed")
        if exc.retryable:
            raise RetryableJobError(exc.message) from exc
        raise JobFailed(exc.message, {"provider_error": exc.to_dict()}) from exc
    ctx.progress(70, "validating images")
    with tempfile.TemporaryDirectory(prefix="mind-img-") as tmp:
        files, infos = [], []
        for i, data in enumerate(res.images):
            try:
                info = inspect_image(data)
            except Exception as exc:  # noqa: BLE001
                _mark(artifact_id, "failed")
                raise JobFailed(f"provider returned data that is not a valid image: {exc}") from exc
            ext = (info["format"] or "png").lower().replace("jpeg", "jpg")
            path = Path(tmp) / f"image-{i + 1}.{ext}"
            path.write_bytes(data)
            files.append((path, {**info, "format": ext, "image_format": info["format"]}))
            infos.append(info)
        with session_scope() as db:
            a = db.get(GeneratedArtifact, artifact_id)
            assert a is not None
            store_version(db, a, files, validation={"images": infos, "decoded": True, "revised_prompt": res.revised_prompt}, params={k: p[k] for k in ("prompt", "size", "n") if k in p}, user_id=ctx.user_id)
            a.status, a.validation_status = "completed", "decoded"
            cost = price * len(res.images) if price is not None else None
            record_usage(db, org_id=ctx.org_id, user_id=ctx.user_id, category="image", provider_kind=provider.kind, model_name=model_name, units=Decimal(len(res.images)), cost_usd=cost, ref_type="artifact", ref_id=artifact_id)
    return {"artifact_id": str(artifact_id), "images": infos}


class EditOp(BaseModel):
    op: Literal["resize", "crop", "rotate", "flip", "mirror", "grayscale", "brightness", "contrast", "fit"]
    width: int | None = Field(None, ge=1, le=8192)
    height: int | None = Field(None, ge=1, le=8192)
    left: int | None = Field(None, ge=0)
    top: int | None = Field(None, ge=0)
    degrees: float | None = None
    factor: float | None = Field(None, ge=0, le=5)


class EditRequest(BaseModel):
    ops: list[EditOp] = Field(min_length=1, max_length=20)
    output_format: Literal["png", "jpeg", "webp"] = "png"
    quality: int = Field(90, ge=1, le=100)


def apply_ops(im: Image.Image, ops: list[EditOp]) -> Image.Image:
    for o in ops:
        if o.op == "resize":
            if not (o.width or o.height):
                raise ValueError("resize needs width or height")
            w = o.width or round(im.width * (o.height or im.height) / im.height)
            h = o.height or round(im.height * w / im.width)
            im = im.resize((w, h), Image.Resampling.LANCZOS)
        elif o.op == "fit":
            if not (o.width and o.height):
                raise ValueError("fit needs width and height")
            im = ImageOps.fit(im, (o.width, o.height), Image.Resampling.LANCZOS)
        elif o.op == "crop":
            if not (o.width and o.height):
                raise ValueError("crop needs width and height")
            left, top = o.left or 0, o.top or 0
            if left + o.width > im.width or top + o.height > im.height:
                raise ValueError("crop box is outside the image")
            im = im.crop((left, top, left + o.width, top + o.height))
        elif o.op == "rotate":
            im = im.rotate(-(o.degrees or 90), expand=True)
        elif o.op == "flip":
            im = ImageOps.flip(im)
        elif o.op == "mirror":
            im = ImageOps.mirror(im)
        elif o.op == "grayscale":
            im = ImageOps.grayscale(im)
        elif o.op == "brightness":
            im = ImageEnhance.Brightness(im).enhance(o.factor if o.factor is not None else 1.2)
        elif o.op == "contrast":
            im = ImageEnhance.Contrast(im).enhance(o.factor if o.factor is not None else 1.2)
    return im


def edit_image_bytes(data: bytes, req: EditRequest) -> tuple[bytes, dict[str, Any]]:
    with Image.open(io.BytesIO(data)) as src:
        src.load()
        im = apply_ops(src.copy(), req.ops)
    if req.output_format == "jpeg" and im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format=req.output_format.upper(), **({"quality": req.quality} if req.output_format in ("jpeg", "webp") else {}))
    out = buf.getvalue()
    return out, inspect_image(out)


def _mark(artifact_id: uuid.UUID, status: str) -> None:
    with session_scope() as db:
        a = db.get(GeneratedArtifact, artifact_id)
        if a:
            a.status = status


def source_image_bytes(storage_key: str) -> bytes:
    return get_storage().get_bytes(storage_key)
