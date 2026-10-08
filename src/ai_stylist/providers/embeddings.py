import math


class EmbeddingClient:
    def __init__(self, client, api_key: str, model: str, dimensions: int):
        self.client = client
        self.api_key = api_key
        self.model = model
        self.dimensions = dimensions
        self.usage = {"tokens": 0, "requests": 0}
        self.last_usage = {}

    async def embed(self, text: str) -> list[float]:
        response = await self.client.post(
            "https://openrouter.ai/api/v1/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "input": text, "dimensions": self.dimensions},
        )
        response.raise_for_status()
        payload = response.json()
        data = payload["data"]
        if len(data) != 1 or data[0].get("index", 0) != 0:
            raise ValueError("Unexpected embedding response cardinality")
        vector = [float(value) for value in data[0]["embedding"]]
        if len(vector) != self.dimensions or not all(math.isfinite(v) for v in vector):
            raise ValueError("Embedding dimension mismatch or non-finite values")
        if not any(vector):
            raise ValueError("A zero embedding cannot be used for cosine similarity")
        self.usage["tokens"] += payload.get("usage", {}).get("total_tokens", 0)
        self.usage["requests"] += 1
        reported_cost = payload.get("usage", {}).get("cost")
        self.last_usage = {
            "input_tokens": payload.get("usage", {}).get("total_tokens", 0),
            "output_tokens": 0,
            "requests": 1,
            "cost": str(reported_cost) if reported_cost is not None else None,
            "model_id": self.model,
        }
        return vector
