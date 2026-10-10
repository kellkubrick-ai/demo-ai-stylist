import io
import json
from urllib.parse import urlparse

from PIL import Image, ImageOps
from pydantic_ai import BinaryContent, ImageUrl

STYLIST_IMAGE_MAX_BYTES = 400_000
STYLIST_IMAGE_DIMENSIONS = (1280, 1024, 768, 512)
STYLIST_IMAGE_QUALITIES = (85, 75, 65, 55)


class ImageLoader:
    def __init__(self, client, max_bytes: int, repository=None):
        self.client = client
        self.max_bytes = max_bytes
        self.repository = repository

    async def load(self, product_id: str, url: str) -> BinaryContent:
        try:
            chunks = []
            size = 0
            async with self.client.stream("GET", url, follow_redirects=True) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ValueError("Product image exceeds configured byte limit")
                    chunks.append(chunk)
            data = b"".join(chunks)
            with Image.open(io.BytesIO(data)) as decoded:
                media_type = Image.MIME.get(decoded.format)
                if media_type not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
                    raise ValueError("Unsupported product photo format")
                decoded.verify()
            if self.repository:
                await self.repository.mark_image(product_id, url, True)
            return BinaryContent(data=data, media_type=media_type)
        except Exception:
            if self.repository:
                await self.repository.mark_image(product_id, url, False)
            raise

    async def attach(self, products) -> list:
        content = []
        for product in products:
            for position, url in enumerate(product.image_urls):
                content.append(json.dumps({"product_id": product.product_id, "position": position}))
                image = await self.load(product.product_id, str(url))
                content.append(self._prepare_for_stylist(image))
        return content

    @staticmethod
    def _prepare_for_stylist(image: BinaryContent) -> BinaryContent:
        with Image.open(io.BytesIO(image.data)) as source:
            decoded = ImageOps.exif_transpose(source)
            if "A" in decoded.getbands() or "transparency" in decoded.info:
                rgba = decoded.convert("RGBA")
                rgb = Image.new("RGB", rgba.size, "white")
                rgb.paste(rgba, mask=rgba.getchannel("A"))
            else:
                rgb = decoded.convert("RGB")

        for dimension in STYLIST_IMAGE_DIMENSIONS:
            prepared = rgb.copy()
            prepared.thumbnail((dimension, dimension), Image.Resampling.LANCZOS)
            for quality in STYLIST_IMAGE_QUALITIES:
                output = io.BytesIO()
                prepared.save(output, format="JPEG", quality=quality, optimize=True)
                data = output.getvalue()
                if len(data) <= STYLIST_IMAGE_MAX_BYTES:
                    return BinaryContent(data=data, media_type="image/jpeg")

        raise ValueError("Product image cannot be reduced below the stylist image byte limit")

    async def select(self, product_id: str, urls, limit=2):
        selected, content = [], []
        for raw_url in urls:
            url = str(raw_url)
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                continue
            content.extend(
                [
                    json.dumps({"product_id": product_id, "position": len(selected)}),
                    ImageUrl(url),
                ]
            )
            selected.append(url)
            if len(selected) >= limit:
                break
        if not selected:
            raise ValueError("No public exact-variant product photo URLs")
        return selected, content
