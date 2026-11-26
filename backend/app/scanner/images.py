"""Screenshot validation and sanitisation (collection only — no content analysis).

Every uploaded image is:
  * checked against size, pixel and side limits *before* it is decoded (decompression bombs);
  * identified from its bytes, never from the file name or the client's MIME type, and limited
    to PNG, JPEG and WebP;
  * fully decoded, rotated upright from its EXIF orientation, and re-encoded in the same format
    *without any metadata* (EXIF incl. GPS location, XMP, ICC, text chunks) — the stored file
    is ours, not the uploaded bytes;
  * fingerprinted: SHA-256 of the stored bytes (exact duplicates) and a 64-bit difference hash
    (near-duplicates such as the same screenshot re-saved or slightly cropped).
"""

import hashlib
import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

FORMATS = {  # Pillow format → (MIME type, file extension)
    "PNG": ("image/png", "png"),
    "JPEG": ("image/jpeg", "jpg"),
    "WEBP": ("image/webp", "webp"),
}
NEAR_DUPLICATE_MAX_DISTANCE = 6   # of 64 bits


class ImageRejected(Exception):
    """The upload is not an acceptable image. `code` is shown to the user (translated)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SanitizedImage:
    data: bytes
    mime_type: str
    extension: str
    width: int
    height: int
    sha256: str
    dhash: int   # signed 64-bit, as stored in PostgreSQL bigint


def _dhash(im: Image.Image) -> int:
    small = im.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = small.tobytes()   # 72 grey levels, row by row
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return bits - (1 << 64) if bits >= (1 << 63) else bits


def hamming(a: int, b: int) -> int:
    return ((a ^ b) & ((1 << 64) - 1)).bit_count()


def sanitize(raw: bytes, *, max_bytes: int, max_pixels: int, max_side: int) -> SanitizedImage:
    if not raw:
        raise ImageRejected("empty_file", "The file is empty")
    if len(raw) > max_bytes:
        raise ImageRejected("file_too_large", "The file is too large")
    try:
        with Image.open(io.BytesIO(raw)) as probe:   # reads the header only
            fmt = probe.format
            width, height = probe.size
    except Image.DecompressionBombError as e:       # Pillow's own header check (> 2x its limit)
        raise ImageRejected("image_too_large", "The image dimensions are too large") from e
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise ImageRejected("unsupported_type", "Not a PNG, JPEG or WebP image") from e
    if fmt not in FORMATS:
        raise ImageRejected("unsupported_type", "Not a PNG, JPEG or WebP image")
    if width < 1 or height < 1 or width > max_side or height > max_side \
            or width * height > max_pixels:
        raise ImageRejected("image_too_large", "The image dimensions are too large")

    mime, ext = FORMATS[fmt]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as im:
                im.seek(0)                       # first frame of an animated WebP/PNG
                im.load()
                upright = ImageOps.exif_transpose(im)
                frame = upright.copy()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as e:
        raise ImageRejected("corrupt_image", "The image could not be read") from e

    if fmt == "JPEG":
        frame = frame.convert("RGB")
    elif frame.mode not in ("RGB", "RGBA", "L", "LA", "P"):
        frame = frame.convert("RGBA")
    if fmt == "WEBP" and frame.mode not in ("RGB", "RGBA"):
        frame = frame.convert("RGBA")

    out = io.BytesIO()
    # No exif=, icc_profile=, pnginfo= arguments: the re-encoded file carries no metadata.
    if fmt == "JPEG":
        frame.save(out, "JPEG", quality=92, optimize=True)
    elif fmt == "PNG":
        frame.save(out, "PNG", optimize=True)
    else:
        frame.save(out, "WEBP", quality=92, method=4)
    data = out.getvalue()
    return SanitizedImage(data=data, mime_type=mime, extension=ext, width=frame.width,
                          height=frame.height, sha256=hashlib.sha256(data).hexdigest(),
                          dhash=_dhash(frame))
