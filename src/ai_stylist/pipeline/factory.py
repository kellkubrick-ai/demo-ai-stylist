from contextlib import asynccontextmanager

import httpx

from ai_stylist.catalog.repository import CatalogRepository
from ai_stylist.db import create_engine
from ai_stylist.llm.intent import IntentService
from ai_stylist.llm.response import ResponseService
from ai_stylist.pipeline.service import StylingPipeline
from ai_stylist.providers.agents import make_agent
from ai_stylist.providers.embeddings import EmbeddingClient
from ai_stylist.providers.images import ImageLoader
from ai_stylist.providers.prompts import PromptRenderer
from ai_stylist.retriever.service import Retriever
from ai_stylist.schemas.planning import StylingUnderstanding
from ai_stylist.schemas.styling import StylingResponse, VLMStylingResponse
from ai_stylist.stylist.service import StylistService


def make_embedder(settings, client):
    settings.require("openrouter_api_key", "embedding_model")
    return EmbeddingClient(
        client,
        settings.openrouter_api_key.get_secret_value(),
        settings.embedding_model,
        settings.embedding_dimensions,
    )


@asynccontextmanager
async def runtime(settings):
    settings.require(
        "openrouter_api_key",
        "intent_model",
        "stylist_model",
        "response_model",
        "embedding_model",
    )
    engine = create_engine(settings)
    agents = []
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            repository = CatalogRepository(engine)
            renderer = PromptRenderer(settings.knowledge_dir)
            renderer.knowledge()
            intent_agent = make_agent(settings, "intent", StylingUnderstanding, renderer)
            stylist_agent = make_agent(settings, "stylist", VLMStylingResponse, renderer)
            response_agent = make_agent(settings, "response", StylingResponse, renderer)
            agents.extend((intent_agent, stylist_agent, response_agent))
            images = ImageLoader(client, settings.image_max_bytes, repository)
            pipeline = StylingPipeline(
                repository,
                IntentService(intent_agent, renderer, repository),
                Retriever(repository, make_embedder(settings, client)),
                StylistService(stylist_agent, images),
                ResponseService(response_agent),
                renderer,
                max_products=settings.max_candidates,
            )
            yield pipeline
    finally:
        for agent in agents:
            await agent.model.client.close()
        await engine.dispose()
