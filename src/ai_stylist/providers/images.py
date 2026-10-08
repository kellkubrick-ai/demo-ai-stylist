import io
import json

import httpx
from PIL import Image
from pydantic_ai import BinaryContent


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
                content.append(await self.load(product.product_id, str(url)))
        return content

    async def select(self, product_id: str, urls, limit=2):
        selected, content = [], []
        for raw_url in urls:
            url = str(raw_url)
            try:
                photo = await self.load(product_id, url)
            except (ValueError, OSError, httpx.HTTPError):
                continue
            content.extend(
                [
                    json.dumps({"product_id": product_id, "position": len(selected)}),
                    photo,
                ]
            )
            selected.append(url)
            if len(selected) >= limit:
                break
        if not selected:
            raise ValueError("No usable exact-variant product photos")
        return selected, content
