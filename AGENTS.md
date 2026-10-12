## Runtime VLM image inputs

- For runtime stylist requests, pass publicly reachable exact-variant image URLs as image inputs
  and let the model provider fetch them.
- Do not download or resize product photos in the application before sending them to the stylist.
- This rule is for runtime styling; other flows may download images when they explicitly need bytes.

## AI model integrations

- Implement generative LLM/VLM calls (intent, enrichment, styling, and response) with Pydantic AI,
  using the project's typed schemas and shared provider configuration.
- Do not add direct OpenAI/OpenRouter chat-completions calls or a parallel agent framework.
- Embeddings are a current exception: `EmbeddingClient` calls the OpenRouter embeddings endpoint
  directly over HTTP. Preserve that separate path unless an explicit migration is requested.

## Fresh test catalog (100 products)

To initialize a new local/test database and run the curated 100-product sample, execute from the
repository root in PowerShell. Configure `.env` first with the database connection, OpenRouter key,
required model IDs, embedding model/dimensions, and (for Telegram) the bot token.

```powershell
uv sync
docker compose up -d db --wait
uv run ai-stylist migrate

uv run ai-stylist import "https://12storeez.com/export/export_mindbox.xml" `
  --mapping catalog_mappings/12storeez.json `
  --report data/reports/12storeez-import.json

uv run ai-stylist enrich `
  --selection evals/catalog/demo-100.json `
  --report data/logs/enrichment/demo-100-server.md

uv run ai-stylist embeddings `
  --selection evals/catalog/demo-100.json `
  --report data/reports/demo-100-server-embeddings.jsonl

uv run ai-stylist catalog-report
uv run ai-stylist style "Подбери минималистичный женский образ для летнего ужина, без высокого каблука"
```

- The selection manifest is based on stable product IDs and does not require `catalog_version`.
  The CLI still verifies that every selected ID exists in the imported catalog before making model
  calls; stop and review/regenerate the sample if products are missing.
- On an empty database, do not pass `--force`; enrichment and embeddings will be created as missing.
- Enrichment and embedding calls may incur provider charges. Run them only when explicitly requested.
- For local Telegram testing after catalog preparation, run `uv run ai-stylist bot` in the
  foreground so logs are visible and code changes do not require rebuilding an image. Ensure the
  host `.env` `DATABASE_URL` points to the published database port (normally `localhost:5432`) and
  `TELEGRAM_BOT_TOKEN` is set.
- Use `docker compose --profile bot up -d --build bot` for background/server deployment; inspect
  it with `docker compose logs -f bot`. The container connects to the Compose database service as
  `db`, not `localhost`.
