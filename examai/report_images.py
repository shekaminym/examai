"""Validate report uploads and store only a bounded, metadata-free PNG."""
from io import BytesIO

from PIL import Image, ImageOps

MAX_IMAGE_BYTES = 2_000_000
MAX_IMAGE_PIXELS = 12_500_000
MAX_IMAGE_SIDE = 4096
IMAGE_FORMATS = ["PNG", "JPEG", "WEBP", "BMP", "GIF"]
IMAGE_ERROR = "Choose a valid PNG, JPEG, WebP, BMP or GIF up to 2 MB and 12.5 megapixels."


def normalize_report_image(data: bytes) -> bytes:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError(IMAGE_ERROR)
    try:
        with Image.open(BytesIO(data), formats=IMAGE_FORMATS) as header:
            width, height = header.size
            if (not 1 <= width <= MAX_IMAGE_SIDE or not 1 <= height <= MAX_IMAGE_SIDE
                    or width * height > MAX_IMAGE_PIXELS):
                raise ValueError(IMAGE_ERROR)
            header.verify()
        with Image.open(BytesIO(data), formats=IMAGE_FORMATS) as source:
            # Load and re-encode only the first frame. Honor camera orientation,
            # then drop text chunks, profiles and other source metadata.
            source.load()
            oriented = ImageOps.exif_transpose(source)
            alpha = "A" in oriented.getbands() or "transparency" in oriented.info
            picture = oriented.convert("RGBA" if alpha else "RGB")
            picture.info.clear()
        while True:
            sink = BytesIO()
            picture.save(sink, format="PNG", compress_level=3)
            if sink.tell() <= MAX_IMAGE_BYTES:
                return sink.getvalue()
            picture = picture.resize(tuple(max(1, size * 3 // 4) for size in picture.size),
                                     Image.Resampling.LANCZOS)
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError(IMAGE_ERROR) from exc
