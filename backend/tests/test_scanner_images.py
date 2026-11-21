"""Screenshot sanitisation (app/scanner/images.py): pure unit tests, no database."""

import io

import pytest
from PIL import Image, ImageDraw

from app.scanner.images import ImageRejected, hamming, sanitize

LIMITS = {"max_bytes": 5 * 1024 * 1024, "max_pixels": 40_000_000, "max_side": 12_000}


def screenshot(fmt: str = "PNG", size=(390, 844), text: str = "Ooredoo", **save) -> bytes:
    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    d.rectangle((10, 10, size[0] - 10, 80), fill=(40, 60, 200))
    for i in range(12):
        d.text((20, 100 + i * 50), f"{text} commentaire {i}", fill="black")
        d.line((20, 130 + i * 50, size[0] - 20, 130 + i * 50), fill=(200, 200, 200))
    buf = io.BytesIO()
    im.save(buf, fmt, **save)
    return buf.getvalue()


def with_gps_exif(fmt: str = "JPEG") -> bytes:
    im = Image.open(io.BytesIO(screenshot("PNG")))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"                       # Make
    exif[0x0112] = 6                                   # Orientation: rotate 90° CW
    exif.get_ifd(0x8825)[2] = (36.0, 45.0, 0.0)        # GPSLatitude (Algiers)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, fmt, exif=exif.tobytes())
    return buf.getvalue()


@pytest.mark.parametrize("fmt,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"),
                                      ("WEBP", "image/webp")])
def test_accepts_supported_formats(fmt, mime):
    s = sanitize(screenshot(fmt), **LIMITS)
    assert s.mime_type == mime and (s.width, s.height) == (390, 844)
    assert len(s.sha256) == 64 and -(2**63) <= s.dhash < 2**63
    assert Image.open(io.BytesIO(s.data)).format == fmt


@pytest.mark.parametrize("fmt", ["JPEG", "WEBP"])
def test_strips_metadata_and_applies_orientation(fmt):
    raw = with_gps_exif(fmt)
    assert Image.open(io.BytesIO(raw)).getexif()       # the upload carries EXIF + GPS
    s = sanitize(raw, **LIMITS)
    out = Image.open(io.BytesIO(s.data))
    assert not out.getexif(), "EXIF survived"
    assert "exif" not in out.info and "icc_profile" not in out.info
    assert (s.width, s.height) == (844, 390)           # rotated upright, then dropped the tag


def test_strips_png_text_chunks():
    im = Image.open(io.BytesIO(screenshot("PNG")))
    from PIL.PngImagePlugin import PngInfo
    info = PngInfo()
    info.add_text("Author", "Kenza")
    buf = io.BytesIO()
    im.save(buf, "PNG", pnginfo=info)
    out = Image.open(io.BytesIO(sanitize(buf.getvalue(), **LIMITS).data))
    assert "Author" not in out.info


@pytest.mark.parametrize("raw,code", [
    (b"", "empty_file"),
    (b"%PDF-1.7 not an image", "unsupported_type"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "unsupported_type"),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40, "unsupported_type"),
])
def test_rejects_non_images(raw, code):
    with pytest.raises(ImageRejected) as e:
        sanitize(raw, **LIMITS)
    assert e.value.code == code


def test_rejects_other_formats_even_if_valid_images():
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, "GIF")
    with pytest.raises(ImageRejected) as e:
        sanitize(buf.getvalue(), **LIMITS)
    assert e.value.code == "unsupported_type"


def test_rejects_oversized_files_and_dimensions():
    raw = screenshot("PNG")
    with pytest.raises(ImageRejected) as e:
        sanitize(raw, max_bytes=100, max_pixels=10**8, max_side=10**5)
    assert e.value.code == "file_too_large"
    with pytest.raises(ImageRejected) as e:
        sanitize(raw, max_bytes=10**8, max_pixels=1000, max_side=10**5)
    assert e.value.code == "image_too_large"
    with pytest.raises(ImageRejected) as e:
        sanitize(raw, max_bytes=10**8, max_pixels=10**8, max_side=500)
    assert e.value.code == "image_too_large"


def test_decompression_bomb_is_refused_before_decoding():
    # A tiny PNG that claims 20000 x 20000 pixels.
    buf = io.BytesIO()
    Image.new("1", (20000, 20000)).save(buf, "PNG")
    assert len(buf.getvalue()) < 200_000
    with pytest.raises(ImageRejected) as e:
        sanitize(buf.getvalue(), **LIMITS)
    assert e.value.code == "image_too_large"


def test_truncated_image_is_refused():
    raw = screenshot("JPEG")
    with pytest.raises(ImageRejected) as e:
        sanitize(raw[: len(raw) // 3], **LIMITS)
    assert e.value.code in ("corrupt_image", "unsupported_type")


def test_fingerprints_exact_and_near_duplicates():
    a = sanitize(screenshot("PNG"), **LIMITS)
    again = sanitize(screenshot("PNG"), **LIMITS)
    assert a.sha256 == again.sha256                       # exact duplicate
    resaved = sanitize(screenshot("JPEG", quality=70), **LIMITS)
    assert resaved.sha256 != a.sha256
    assert hamming(a.dhash, resaved.dhash) <= 6           # near duplicate
    other = sanitize(screenshot("PNG", size=(844, 390), text="Mobilis"), **LIMITS)
    assert hamming(a.dhash, other.dhash) > 6              # a different screenshot
