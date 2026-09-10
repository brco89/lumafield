import hashlib
import io
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError
from .models import DomainError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000


def normalize_image(raw: bytes) -> tuple[bytes, str]:
    """Decode actual pixels, orient, strip metadata and normalize into JPEG.

    No filename, claimed MIME, inspection identity or transcript crosses this API.
    Normalized bytes define duplicate evidence, including differently named files.
    """
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise DomainError("INVALID_IMAGE_SIZE", "Use a photo up to 10 MiB.", 413)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in {"JPEG", "PNG"}:
                    raise ValueError("Unsupported format")
                if source.width * source.height > MAX_IMAGE_PIXELS or getattr(source, "n_frames", 1) != 1:
                    raise ValueError("Image too large or animated")
                source.load()
                oriented = ImageOps.exif_transpose(source).convert("RGB")
                oriented.thumbnail((2048, 2048))
                # Fresh image strips EXIF, ICC, comments and PNG text chunks.
                clean = Image.new("RGB", oriented.size)
                clean.paste(oriented)
                output = io.BytesIO()
                clean.save(output, format="JPEG", quality=90, subsampling=0)
                normalized = output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise DomainError("INVALID_IMAGE", "Upload a valid JPEG or PNG photo up to 24 megapixels.", 422) from None
    return normalized, hashlib.sha256(normalized).hexdigest()
