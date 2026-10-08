# Implementation and acceptance status

The application implements the approved specification-v2 pipeline. The initial repository contained
only package placeholders. The full suitable feed replaces the proposed 200–500-product demo subset;
runtime input remains capped at 30 visual variants.

## Implemented

- Typed contracts, Decimal money, planning validation and closed-candidate output checks.
- PostgreSQL/pgvector schema, frozen initial Alembic migration and local Compose setup.
- Verified-mapping XML adapter, full-feed refresh, raw source retention and import diagnostics.
- Offline multimodal enrichment, persistence/reuse/invalidation and resumable preparation.
- Text embeddings and SQL-filtered role retrieval with complete-base coverage before truncation.
- Intent/planning, runtime stylist, response rewrite preservation and catalog card resolution.
- Stored sessions/runs, Telegram long polling and same-user message serialization.
- Complete catalog snapshot/empty-only restore, 15 eval scenarios and human verdict recording.

Small additions to the specification's storage/contracts: `product_images.is_usable` records actual
photo verification; `catalog_state` records the active snapshot/version; `styling_runs.metrics`
stores coverage/usage/latency/cost; runtime ProductGroup/Offer objects retain source model/offer IDs.
Catalog facts and inferred styling metadata remain separate. No generated look is automatically
classified as professionally approved.

## Verification performed

- 73 tests passed, including isolated PostgreSQL/pgvector integration tests, real-feed import
  cases and a two-look
  end-to-end pipeline using PydanticAI TestModel, mocked HTTP and actual image bytes.
- Ruff lint and formatting checks passed; `uv sync --locked` succeeded.
- Wheel built successfully and contains all four prompt templates; Docker Compose image built.
- CLI imported the complete synthetic fixture (8 visual products, 11 offers), reported sizes/
  audiences and exported a snapshot.
- The complete 12Storeez XML was profiled and mapped with no parse errors. It has 102,882 offers,
  of which 10,409 are available. The local database now contains 2,466 active visual groups and
  12,919 associated size offers. Category IDs missing from source definitions are retained with
  placeholder names. The 196 groups without a reliable normalized type are not styling candidates.
- The selected OpenRouter VLM enriched five real products from their source photos, and the
  embedding model wrote five corresponding vectors; both bounded smoke tests had zero failures.
  Full-catalog offline preparation is in progress and is resumable.

## Required external acceptance work

1. Finish full-catalog enrichment and embeddings, resolve any failed photo/provider rows, then
   freeze a snapshot. The original XML remains under the ignored `data/feeds` directory.
2. Run real-model end-to-end styling and evaluation scenarios on that snapshot, check latency/cost
   and adjust only observed quality failures.
3. Verify Telegram with the real bot token and have client stylists review knowledge and approve
   showcase outputs containing 2–3 complete looks where the catalog supports them.

Code and deterministic tests alone cannot establish the final data/model/quality criteria in
section 18. These items remain pending rather than being reported as completed demo acceptance.
