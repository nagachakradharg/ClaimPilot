"""Photo checks, storage and resizing. No model calls here."""

from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png"}


@dataclass(frozen=True)
class StoredPhoto:
    path: str  # original, on the local volume
    sha256: str
    width: int
    height: int
    mime_type: str


def validate_photo(data: bytes, name: str, max_bytes: int) -> str | None:
    """Return an error message, or None if the photo is acceptable."""
    if len(data) > max_bytes:
        return f"{name}: larger than {max_bytes // (1024 * 1024)} MB"
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            img.verify()
    except (UnidentifiedImageError, OSError):
        return f"{name}: not a readable image"
    if fmt not in ALLOWED_FORMATS:
        return f"{name}: format {fmt} not allowed (JPEG or PNG only)"
    return None


def store_photo(data: bytes, directory: Path) -> StoredPhoto:
    digest = hashlib.sha256(data).hexdigest()
    with Image.open(io.BytesIO(data)) as img:
        fmt, (w, h) = img.format, img.size
    ext = "jpg" if fmt == "JPEG" else "png"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest[:16]}.{ext}"
    path.write_bytes(data)
    return StoredPhoto(str(path), digest, w, h, ALLOWED_FORMATS[fmt])


def resized_jpeg_b64(path: str, max_side: int) -> str:
    """Downscale for the model (cost + latency) and strip EXIF (location privacy)."""
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()
