# AI Fashion Stylist

Telegram demo that extracts styling intent, retrieves real catalog products per outfit role, and
assembles up to three complete looks using actual product photos. Catalog IDs, stock, sizes and
prices are validated by application code; monetary calculations use `Decimal`.

The implementation follows [specification v2](ai-fashion-stylist-spec-v2.md), with the approved
extension to process the entire suitable catalog rather than a 200–500-product subset.
The 12Storeez XML adapter has been verified against the complete feed. Professional stylist
approval and Telegram end-to-end verification are still required before demo acceptance.

## Requirements and setup

- Python 3.13 and `uv`.
- Docker Compose with a Linux engine.
- OpenRouter credentials/model IDs and a Telegram bot token. The verified 12Storeez feed URL is
  already recorded in the mapping below.

Populate `.env` using [.env.example](.env.example) before running migrations. Preserve any existing
credentials; the application never fills or overwrites them for you.

```powershell
uv sync
docker compose up -d db --wait
uv run ai-stylist migrate
```

Model settings use OpenRouter IDs such as `upstream-provider/model-name`, without the
`openrouter:` prefix. `INTENT_MODEL` interprets the request and plans outfit roles;
`ENRICHMENT_MODEL` describes catalog photos offline; `STYLIST_MODEL` evaluates and assembles
looks from retrieved candidates; `RESPONSE_MODEL` writes the final user-facing explanation.
The current local `.env` uses `openai/gpt-6-luna` for intent and response,
`google/gemini-3.8-flash` for enrichment and styling, and
`openai/text-embedding-3-small` for vectors.
Installing and running tests do not call the paid models; `enrich`, `embeddings`, `style`, `eval`
and the bot do.

Reasoning effort is configured independently per agent. The current defaults use `medium` for
intent interpretation, `low` for catalog enrichment, `high` for visual styling and compatibility,
and `none` for final response wording. Some providers support only a subset of effort levels;
Gemini 3.8 Flash supports `low`, `medium`, and `high`, so enrichment uses its lowest level.

Set `EMBEDDING_MODEL` and its `EMBEDDING_DIMENSIONS` before migrating a new database. Stored document
and query vectors must use the same model/dimension. A dimension change needs a matching database
migration or a new database and regenerated vectors; it is not an automatic conversion.

On this Windows machine the working Docker engine is `desktop-linux`. If the default engine is
unavailable, use `docker --context desktop-linux compose ...` for the commands below.

## Verify and import the full feed

The verified mapping is [catalog_mappings/12storeez.json](catalog_mappings/12storeez.json).
`inspect` prints a bounded number of complete elements, retaining attributes and XML tags;
downloading a URL retains the full raw feed under `data/feeds/<sha256>.xml` for reproducibility.

```powershell
uv run ai-stylist inspect "https://12storeez.com/export/export_mindbox.xml" --offer-tag offer --category-tag category --limit 5
uv run ai-stylist import "https://12storeez.com/export/export_mindbox.xml" --mapping catalog_mappings/12storeez.json --report data/reports/12storeez-import.json
uv run ai-stylist catalog-report
```

The checked feed contains 102,882 offers in 23,695 visual groups. The 12Storeez mapping imports only
the 9,482 offers with `available=true` and `visibility=all`, in 2,031 groups. Reimport removes
previously stored offers and groups that no longer meet this rule. `--all-groups` does not bypass
the offer filter; importing does not call VLM or embedding models. The source `article` repeats across
sizes, so the distinct XML offer ID is the
commercial key. The feed references category IDs without category definitions; the importer keeps
those memberships with explicit placeholder names. Editorial category `Комплекты` does not prove
a purchasable set, so `bundle_confirmed` remains unset. The mapping leaves 196 active groups with
an unresolved product type; they are stored but excluded from styling until classified safely.

Any other feed needs its own explicit JSON `FeedMapping` with `verified: true` and verification
notes. Confirm external offer IDs, optional commercial SKU/barcode, exact visual grouping, category
definitions/parents, size, photos, current/previous prices, currency and availability for the
intended purchase channel. [synthetic.mapping.json](tests/fixtures/synthetic.mapping.json) is
test-only and must not be used for 12Storeez.

Mapping selectors support element paths, `@attribute`, `element/@attribute`, `.` for element text,
and ElementTree predicates such as `param[@name='Size']`. Namespace-qualified tags/paths must be
specified explicitly when the source uses namespaces.

- `fields`: confirmed scalar selectors, including `offer_id`, `visual_id`, `title`, `price`,
  `currency`; optional fields include `sku`, `barcode`, `description`, `old_price`, `color`, `size`.
- `grouping`: `visual_id` when the source ID confirms one visual variant; `model_color` when the
  verified grouping key is model plus original color. Unknown color cannot form a model/color key.
- `category_fields`, `category_paths`, `image_paths`: retain all memberships and ordered photos.
- `availability`: verified equality values or a positive quantity from a confirmed stock channel.
  Missing/unrecognized values produce diagnostics and never default to available.
- `currency_map`: explicit source-to-normalized codes. `RUR` to `RUB` does not change the amount.
- `product_type_path`/maps: verified source garment types/categories to the supported vocabulary.
  `product_type_category_priority` resolves overlap; narrowly scoped title rules handle missing
  category definitions. Material/editorial categories remain independent memberships.
- `attributes`: verified facts with `text`, `bool`, `decimal` or `list` extraction. Use
  `bundle_confirmed` only for proven purchasable sets; `features` only for source-confirmed features;
  `audience` only for the source's audience label. Visual guesses belong to enrichment.

Imports stream offers and write within one transaction. A malformed XML or database failure rolls
back the refresh. Valid rows are imported with row diagnostics for unresolved mappings; offers
missing from a complete refresh become unavailable. A feed with no valid offers is rejected.
Stable external IDs keep repeat imports idempotent. Original source attributes remain on offers.

## Prepare the entire suitable catalog

```powershell
uv run ai-stylist enrich
uv run ai-stylist embeddings
uv run ai-stylist catalog-report
uv run ai-stylist snapshot --output data/catalog-snapshot.jsonl
```

Suitable products have a normalized garment type, available offers and exact-variant photo URLs.
Enrichment sends up to two labeled public image URLs for one product per model request. A successful
model response marks those URLs usable for runtime retrieval. Stylist requests still validate and
attach image bytes locally. Before sending them to the stylist, images are resized and JPEG-encoded
to at most 400 KB each, keeping the maximum 30-product, two-photo request below Gemini's 30 MB image
payload limit. Inaccessible images are reported explicitly.

Preparation persists each successful product immediately. Running the commands again reuses
unchanged successful results and retries missing results. Use `--force` for an explicit rebuild.
Enrichment writes readable Markdown traces to `data/logs/enrichment/*.md` by default. Each product
section contains the complete model instructions, normalized product input, clickable image URLs and
previews, structured model output, usage and errors. Pass a `.jsonl` path to `--report` when a
machine-readable trace is preferable. Add `--log-console` to also print each record as JSON while the
command runs. API keys and binary image data are never logged.
Embedding input is a short product description assembled from catalog facts and available visual
enrichment, without JSON field names or empty values. Changing this text format does not update
previously saved vectors; `uv run ai-stylist embeddings --force` rebuilds them with API calls.
Enrichment concurrency defaults to two. Price,
availability or size-only changes do not invalidate enrichment. Descriptive/photo changes do;
updating enrichment invalidates the corresponding embedding. No enrichment runs during user requests.

Before processing a large real catalog, run `uv run ai-stylist enrich --limit 5` and
`uv run ai-stylist embeddings --limit 5` to smoke-test the configured models. Then run both commands
without `--limit` to prepare the entire suitable catalog. The limit affects preparation only.
Preparation commands with per-product failures return failure counts and retain detailed reports.

The curated women's demo subset is recorded in `evals/catalog/demo-100.json`: 100 distinct
model/color products with available offers, previously checked photos and stored enrichment.
The stored enrichment for these 100 was produced by `openai/gpt-6-luna`, not the current Google
VLM configuration; a Google-specific comparison requires an explicit `enrich --force` run first.
`enrich` and `embeddings` accept `--selection evals/catalog/demo-100.json` to process only those
IDs; the manifest's catalog version must match the database. Selecting products does not call
models or delete catalog rows. With an otherwise empty embeddings table, embedding only this
selection also limits runtime search to the 100 selected products.
Eight manually labeled retrieval checks are in `evals/catalog/demo-100-search-probes.json`;
they are candidate-relevance targets, not mandatory final outfit items.

Snapshots contain all normalized products/offers/categories, saved enrichment and model IDs,
embeddings, photo verification status, source/version metadata and a completion marker. Existing
snapshot files are not overwritten. Restore requires an empty catalog and matching vector dimension:

```powershell
uv run ai-stylist restore data/catalog-snapshot.jsonl
```

## Run and evaluate styling

```powershell
uv run ai-stylist style "Нужен образ на ужин: минимализм, без каблуков, подчеркнуть талию."
uv run ai-stylist eval --scenarios evals/scenarios/demo.json
uv run ai-stylist review evals/results/RUN/SCENARIO.json --verdict approve --comments "Комментарий стилиста"
```

`style --dialogue path.json` accepts a JSON list of `{"role":"user|assistant","content":"..."}`
messages ending with a user message. Output includes the validated response and catalog-resolved
cards/totals. Missing user sizes do not mean that a selected offer fits the user.

The runtime pipeline is: intent/planning → SQL-filtered retrieval per role → one VLM styling call
→ catalog/application validation → response rewriting → catalog cards. Required-role coverage is
reserved before optional candidates; the entire pool is capped at 30 visual variants. Impossible
plans skip the VLM. Different looks may share products, but changing only size offers is not a new look.
Sets require confirmed purchasability and are priced once.

The response model cannot replace validated assignments, scores or limitations. A changed catalog
version or commercial offer causes rejection rather than displaying stale selections. Provider
failures produce a user-facing failure and a `styling_runs` error record.

All prompts are packaged Markdown/Jinja2 templates. Initial knowledge/rubric files require a
professional stylist's review. The 15 supplied eval scenarios are starting cases; calibrate budgets,
sizes and audiences against the actual frozen catalog. Results record request/plan/pool coverage,
validated looks, latency, model usage, reported/estimated cost when available, and human verdicts.
Unknown costs remain null. VLM self-scores never substitute for professional approval.

## Telegram

```powershell
uv run ai-stylist bot
# Or after preparing the database/catalog and setting .env:
docker compose --profile bot up -d --build bot
```

Commands: `/start`, `/stylist`, `/finish`, `/reset`. Buttons also enter, finish and reset styling.
Sessions and dialogue live in PostgreSQL. Messages from the same user are serialized so concurrent
updates cannot overwrite dialogue. The bot shows real product photos, URLs, offer sizes/prices and
application-computed totals. Core services do not depend on Telegram objects.

## Verification

```powershell
uv run ruff check src tests db scripts
uv run ruff format --check src tests db scripts
uv run pytest tests/unit
docker compose -p ai-stylist-tests -f compose.test.yaml up -d --wait
$env:TEST_DATABASE_URL = "postgresql+asyncpg://stylist:stylist@localhost:55432/stylist_test"
uv run pytest
docker compose -p ai-stylist-tests -f compose.test.yaml down
```

Integration tests apply the real Alembic migration in a new test-owned schema and clean up that
schema. The database name must end in `_test`. Unit tests use PydanticAI test models and mocked HTTP
images; they never contact Telegram/OpenRouter. Test PostgreSQL data is temporary.

Migrations, import grouping/currency/stock, enrichment reuse/invalidation, photo attachments,
retrieval coverage, offer/role checks, budgets, duplicate looks, response preservation, dialogue
transitions and snapshot round-trips have automated coverage. A bounded real-feed fixture and a
successful five-product OpenRouter enrichment/embedding smoke run have also been verified.
Telegram delivery and stylist-approved showcase scenarios remain the final acceptance work.

Generated outfit visualization, try-on, Mindbox, long-term memory, REST APIs, microservices,
LangGraph and Temporal are outside this demo's scope.
