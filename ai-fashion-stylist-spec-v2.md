# AI Fashion Stylist — Demo Specification

## 0. Purpose

This specification defines the first working demo of an AI fashion stylist for a real store catalog.

The demo is considered successful only if it demonstrates the core value:

> A user describes what they need, and the system returns 2–3 coherent complete outfits made only from real store products, with a short grounded explanation of why each outfit fits the request.

The demo is **not** intended to prove production scalability, advanced personalization, or Mindbox integration.

The development approach is **spec-driven**:

```text
spec
  ↓
implementation plan
  ↓
code
  ↓
tests + evals
  ↓
review against acceptance criteria
```

Each core component must have:
- explicit input schema;
- explicit output schema;
- invariants;
- failure behavior;
- acceptance criteria.

---

# 1. Demo Scope

## 1.1 Main user scenarios

The demo should support styling requests such as:

- outfit for a date or dinner;
- office / work;
- vacation;
- event with a specified formality level;
- specific aesthetic or style direction;
- explicit fit / silhouette / figure preferences;
- restrictions such as no heels, no oversized bottoms, cover shoulders, etc.

Example:

```text
"Нужен образ на ужин.
Хочу минимализм, не слишком нарядно.
Не люблю каблуки и хочу подчеркнуть талию."
```

Expected system behavior:

```text
dialogue
    ↓
StylingIntent + OutfitPlan
    ↓
candidate pool retrieved per outfit slot
    ↓
2–3 complete looks
    ↓
short grounded explanation
```

---

# 2. Architecture

## 2.1 Runtime flow

```text
Telegram
   ↓
Intent + Planning LLM
   ↓
StylingIntent + OutfitPlan
   ↓
Retriever per outfit slot
   ↓
CandidatePool + stored enrichment + product images
   ↓
VLM Stylist: composition + compatibility
   ↓
VLMStylingResponse
   ↓
Application validation + catalog lookup
   ↓
Response LLM
   ↓
Telegram
```

Intent extraction and outfit planning use one LLM call in the demo.
Outfit generation and compatibility judging use one runtime VLM call.
Offline product enrichment is a separate VLM task and is reused across styling requests.

If evaluation shows unstable quality, the VLM stage may later be split into:

```text
Outfit Generator
      ↓
VLM Judge
```

Do not introduce that split before it is needed.

## 2.2 Offline flow

```text
Store feed + product images
   ↓
catalog import: normalize model/color variants and size offers
   ↓
PostgreSQL: catalog facts + image references
   ↓
VLM enrichment once per visual product variant
   ↓
PostgreSQL: stored structured enrichment
   ↓
embedding generation
   ↓
pgvector
```

Enrichment is a required preparation step for the demo catalog.
Reuse stored enrichment for an unchanged product; do not run it for each user request.
Re-enrich only when images or relevant product data change, or when an explicit rebuild is requested.

## 2.3 Two VLM tasks

| Task | Input | Output | Lifetime |
|---|---|---|---|
| Catalog enrichment | One product's catalog facts and actual photos | Structured styling attributes and a short description, stored in the database | Once during catalog preparation; reused across requests |
| Outfit styling | User intent, outfit plan, retrieved products, stored enrichment, eligible offers, and actual photos | Complete looks, selected product/offer IDs, compatibility scores, explanations, and limitations | Per user styling request |

Enrichment describes individual products. Runtime styling evaluates combinations of products.

---

# 3. Tech Stack

- Python 3.12+
- aiogram
- Pydantic v2
- PydanticAI
- Jinja2
- OpenRouter
- PostgreSQL
- pgvector
- pytest
- uv
- Docker Compose

Optional later:
- LangGraph;
- Temporal;
- separate REST API;
- Mindbox;
- custom model training;
- generated outfit visualization.

---

# 4. Repository Structure

```text
src/
  ai_stylist/
    bot/
      handlers.py
      keyboards.py

    catalog/
      importer.py
      repository.py
      enrichment.py

    llm/
      intent.py
      response.py

    retriever/
      service.py
      query_builder.py

    stylist/
      service.py
      prompt.py

    schemas/
      intent.py
      planning.py
      catalog.py
      retrieval.py
      styling.py

    prompts/
      intent.md
      enrichment.md
      stylist.md
      response.md

    config.py

scripts/
  import_catalog.py
  enrich_catalog.py
  rebuild_embeddings.py

tests/
  unit/
  integration/

evals/
  scenarios/
  results/

db/
  migrations/

knowledge/
  stylist_rules.md
  compatibility_rubric.md

AGENTS.md
README.md
spec.md
pyproject.toml
uv.lock
compose.yaml
.env.example
```

Rules:
- `bot/` knows Telegram.
- core styling logic must not depend on Telegram objects.
- `retriever/` finds candidates but does not make the final styling decision.
- `stylist/` works only with already retrieved candidates.
- all LLM/VLM prompts must be Markdown files (`*.md`) containing Jinja2 templates in `src/ai_stylist/prompts/`.
- Python code loads and renders those templates with explicit context before each model call.
- prompts and stylist knowledge must not be hardcoded into Python.

---

# 5. Database Model

For the demo, PostgreSQL + pgvector is enough.

## 5.1 product_groups

Represents one visual product variant: one model in one color, independent of size variants.

Example: a black hoodie and the same hoodie in beige are different product groups.
Their size variants are offers within the corresponding group.

```text
product_groups
--------------
id                  UUID / BIGINT PK
external_id         TEXT UNIQUE NOT NULL
source_model_id     TEXT NULL
title               TEXT NOT NULL
description         TEXT
brand               TEXT
product_type        TEXT NULL
color               TEXT NULL
catalog_attributes  JSONB NOT NULL DEFAULT '{}'
product_url         TEXT
source_updated_at   TIMESTAMP NULL
created_at          TIMESTAMP NOT NULL
updated_at          TIMESTAMP NOT NULL
```

Source of truth:
- title;
- description;
- source category memberships;
- product URL;
- brand.

Color and `catalog_attributes` also come from the feed. Store supplied material composition,
measurements, or construction details here when available; do not fill missing facts with VLM guesses.
`external_id` uniquely identifies the visual variant. If the feed groups several colors under one
model ID, preserve that ID in `source_model_id` and use a stable model/color key for `external_id`.
Unknown color remains null; the importer must not invent a color or associate unrelated photos.
Preserve every source category membership through `product_categories` (section 5.3).
`product_type` is a normalized garment type, such as jeans, trousers, blazer, or suit_set,
assigned through an explicit mapping of verified source categories/attributes.
Source categories may describe garments, materials, audiences, or editorial collections.
Store those memberships independently; a collection such as "Новинки" is not a garment type.
If the garment type is unresolved, keep it null and exclude the product from role-based demo retrieval
until it is mapped. Do not infer a source category hierarchy from its display order or name alone.

## 5.2 offers

Represents a purchasable size SKU within a visual model/color variant.

```text
offers
------
id                  UUID / BIGINT PK
external_id         TEXT UNIQUE NOT NULL
product_group_id    FK product_groups.id NOT NULL

sku                 TEXT UNIQUE NULL
barcode             TEXT NULL
size                TEXT NULL
price               NUMERIC NOT NULL
old_price           NUMERIC NULL
currency            TEXT NOT NULL
available           BOOLEAN NOT NULL
source_attributes   JSONB NOT NULL DEFAULT '{}'

created_at          TIMESTAMP NOT NULL
updated_at          TIMESTAMP NOT NULL
```

Hard filtering must use `offers`.
Every offer and photo in a product group must refer to the same visual variant.
Use `NUMERIC` in PostgreSQL and `Decimal` in Python for monetary values.
Keep external IDs, commercial SKU values, and barcodes as separate string identifiers.
Populate `sku` or `barcode` only from confirmed XML fields; do not assign a repeated model/article code
to every size SKU or treat a 13-digit number as a confirmed offer ID.
If a commercial SKU is absent, leave it null and identify the offer by its confirmed external ID.
Preserve other source fields, including raw stock flags/quantities and the original currency code,
in `source_attributes` using their actual XML names. Derive `available` only from a verified source rule
for the intended purchase channel. Unresolved availability must not default to true or enter retrieval.
`price` is the confirmed current payable price. Populate `old_price` only if an XML field explicitly
identifies the previous/reference price; otherwise preserve an unexplained extra amount in `source_attributes`.
Budget filtering and outfit totals always use `price`, never the old/reference price.
Keep size labels as strings, including XS, "One size", numeric shoe labels, and accessory labels such as "midi".
Interpret them by product type and outfit role; do not apply clothing-size filters to bags or assume a shoe size system.

The model must never infer price, stock, or size from images.

## 5.3 categories

```text
categories
----------
id                  UUID / BIGINT PK
external_id         TEXT UNIQUE
name                TEXT NOT NULL
parent_id           FK categories.id NULL
```

Preserve source IDs, names, and parent links as supplied by XML; duplicate names do not imply identical categories.
A product can belong to several categories simultaneously:

```text
product_categories
------------------
product_group_id    FK product_groups.id NOT NULL
category_id         FK categories.id NOT NULL
PRIMARY KEY (product_group_id, category_id)
```

Use the normalized `product_type` for garment constraints and role mapping.
Material, audience, and editorial memberships may provide separate filters or weak retrieval context;
they must not be treated as interchangeable outfit roles.

## 5.4 product_images

```text
product_images
--------------
id                  UUID / BIGINT PK
product_group_id    FK product_groups.id NOT NULL
url                 TEXT NOT NULL
position            INT NOT NULL DEFAULT 0
is_primary          BOOLEAN NOT NULL DEFAULT FALSE
```

For VLM requests:
- images must show the exact model/color variant linked by `product_group_id`;
- preserve deterministic image order;
- normally send 1–2 images per product in the demo;
- prefer product photo + model photo when available.

## 5.5 product_enrichments

Stores reusable offline VLM enrichment for a visual product variant.

```text
product_enrichments
-------------------
id                      UUID / BIGINT PK
product_group_id        FK product_groups.id UNIQUE NOT NULL

styles                  JSONB NOT NULL DEFAULT '[]'
silhouette              TEXT NULL
fit                     TEXT NULL
formality               TEXT NULL
occasions               JSONB NOT NULL DEFAULT '[]'
texture                  TEXT NULL
visual_weight           TEXT NULL
color_characteristics   JSONB NOT NULL DEFAULT '[]'
layering_suitability    JSONB NOT NULL DEFAULT '[]'

enriched_description    TEXT NULL
model_id                TEXT NULL
updated_at              TIMESTAMP NOT NULL
```

This table contains inferred styling metadata only.

Original catalog data always has higher authority.
Keep the successful result until the product changes or an explicit enrichment rebuild is requested.
Availability, price, or size-only updates do not require visual enrichment again.
Missing or failed enrichment remains absent; it must not overwrite the source catalog facts.

## 5.6 product_embeddings

For the demo, one embedding per visual product variant is enough.

```text
product_embeddings
------------------
product_group_id    FK product_groups.id PRIMARY KEY
embedding           VECTOR(...)
embedding_model     TEXT NOT NULL
updated_at          TIMESTAMP NOT NULL
```

Embedding input contains the original product description and relevant structured facts,
plus the stored enrichment. Include product images if the selected embedding model supports them:

```text
product images
+
original description
+
stored structured enrichment + enriched description
```

Price, size, availability, stock, and SKU must not exist only in the vector.

## 5.7 related_products

Optional signal from the source feed, e.g. "complete the look".

```text
related_products
----------------
source_product_group_id     FK product_groups.id
target_product_group_id     FK product_groups.id
relation_type               TEXT
```

This is only an extra weak signal.

It must not override the VLM stylist decision.

## 5.8 styling_sessions

Minimal dialogue state.

```text
styling_sessions
----------------
id                  UUID PK
telegram_user_id    BIGINT NOT NULL
status              TEXT NOT NULL
current_intent      JSONB NULL
current_outfit_plan JSONB NULL
dialogue_history    JSONB NOT NULL DEFAULT '[]'
created_at          TIMESTAMP NOT NULL
updated_at          TIMESTAMP NOT NULL
```

Possible statuses:

```text
active
finished
```

Long-term memory is not required.

## 5.9 styling_runs

Stores one styling request for evaluation/debugging.

```text
styling_runs
------------
id                  UUID PK
session_id          FK styling_sessions.id
user_request        TEXT NOT NULL
catalog_version     TEXT NOT NULL
intent_json         JSONB NOT NULL
outfit_plan_json    JSONB NOT NULL
vlm_request_json    JSONB NULL
result_json         JSONB NULL
error               TEXT NULL
created_at          TIMESTAMP NOT NULL
```

`vlm_request_json` stores the candidate facts, eligible offers, image URLs, and stored enrichment
supplied to the stylist. Store image references, not image bytes.
`result_json` stores the validated styling result, including any limitations.
Use `error` for import-independent runtime failures; clarification or an empty pool is a normal outcome.
These records and the catalog version are enough for comparable demo evals and debugging.

## 5.10 Data boundaries

| Information | Database storage | Runtime VLM input/output |
|---|---|---|
| Original title, description, color, brand, attributes, URL; normalized garment type | product_groups | Relevant candidate facts and garment type are input; final links and titles are resolved by the application |
| All source category memberships and hierarchy | categories + product_categories | Relevant category names may be input; garment constraints use the separately normalized product type |
| Commercial SKU, barcode, size, current/previous price, currency, availability, raw source flags | offers | Eligible offer IDs, sizes, current payable prices/currencies, and role eligibility are input; selected offer IDs are output |
| Exact variant photos and their order | product_images | Actual image attachments are input; final photos are resolved by the application |
| Inferred styling attributes and enriched description | product_enrichments | Saved enrichment is input to the stylist; it is produced by the separate offline VLM task |
| Search vectors | product_embeddings | Used by retrieval; not sent to the stylist |
| Explicit user context, intent, and compositions | styling_sessions + styling_runs | Normalized intent and outfit plan are input |
| Selected roles/products/offers, compatibility scores, explanations, limitations | styling_runs.result_json | Produced by the stylist and stored after application validation |

Catalog lookup and monetary calculations remain application responsibilities.

---

# 6. Catalog Schemas

## 6.1 ProductGroup

```python
from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class CatalogCategory(BaseModel):
    id: str
    external_id: str
    name: str
    parent_id: str | None = None


class ProductGroup(BaseModel):
    id: str
    external_id: str
    title: str
    description: str | None = None
    brand: str | None = None
    product_type: str | None = None
    source_categories: list[CatalogCategory] = Field(default_factory=list)
    color: str | None = None
    catalog_attributes: dict[str, Any] = Field(default_factory=dict)
    product_url: HttpUrl | None = None
    image_urls: list[HttpUrl]
```

## 6.2 Offer

```python
class Offer(BaseModel):
    id: str
    sku: str | None = None
    barcode: str | None = None
    product_group_id: str

    size: str | None = None

    price: Decimal = Field(ge=0)
    old_price: Decimal | None = Field(default=None, ge=0)
    currency: str
    available: bool
    source_attributes: dict[str, Any] = Field(default_factory=dict)
```

## 6.3 CatalogProduct

Runtime object used by retrieval/styling.

```python
class CatalogProduct(BaseModel):
    product: ProductGroup
    offers: list[Offer]
    enrichment: ProductEnrichment | None = None
```

Invariant:
- every returned product must have at least one available offer after hard filters.
- `enrichment` contains the stored result from section 9, kept separate from original product facts.
- offer sizes, prices, and currencies remain linked to their individual offer IDs.

---

# 7. Styling Intent Contract

## 7.1 Input

Full or recent dialogue history, supported outfit roles, and the normalized product-type vocabulary.
The LLM extracts intent and plans compositions in one call; it does not receive the full catalog.

## 7.2 Output

```python
from typing import Literal


OutfitRole = Literal[
    "top", "bottom", "dress", "set", "outer_layer", "shoes", "bag", "accessory"
]


class StylingBudget(BaseModel):
    currency: str = "RUB"
    max_outfit_price: Decimal | None = Field(default=None, ge=0)
    min_item_price: Decimal | None = Field(default=None, ge=0)
    max_item_price: Decimal | None = Field(default=None, ge=0)


class StylingIntent(BaseModel):
    occasion: str | None = None
    styles: list[str] = Field(default_factory=list)
    formality: str | None = None

    product_types: list[str] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=list)

    budget: StylingBudget | None = None
    sizes_by_role: dict[OutfitRole, list[str]] = Field(default_factory=dict)
    user_context: dict[str, str] = Field(default_factory=dict)

    fit_preferences: list[str] = Field(default_factory=list)
    figure_preferences: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)

    needs_clarification: bool = False
    clarification_question: str | None = None
```

`max_outfit_price` limits the sum of selected offers in each look, including optional items.
Item bounds apply to each offer individually. Validate that the minimum does not exceed the maximum.
Use the budget currency consistently; do not perform implicit currency conversion.
`sizes_by_role` keeps clothing and shoe sizes separate. Preserve the size system when supplied;
do not assume that the same size label fits all roles or brands.
`user_context` contains only explicitly supplied facts, such as height with its unit.
If the budget scope or size mapping is materially ambiguous, ask for clarification.

Examples of useful explicit preferences:

```text
emphasize waist
avoid oversized bottom
prefer relaxed fit
cover shoulders
avoid high heels
prefer low contrast
```

Avoid relying on broad body-type labels such as:
- pear;
- apple;
- hourglass;

when concrete preferences are available.

## 7.3 Intent Agent Invariants

The Intent LLM must:
- not invent catalog products;
- not invent user measurements;
- not infer body characteristics the user did not provide;
- mark clarification when the request is insufficient to produce a meaningful outfit.

When clarification is required, return a question and stop before retrieval or runtime styling.

## 7.4 Outfit planning output

```python
class OutfitSlot(BaseModel):
    role: OutfitRole
    required: bool = True
    product_type_constraints: list[str] = Field(default_factory=list)
    retrieval_query: str


class OutfitComposition(BaseModel):
    plan_id: str
    slots: list[OutfitSlot] = Field(min_length=1)


class OutfitPlan(BaseModel):
    compositions: list[OutfitComposition] = Field(default_factory=list)


class StylingUnderstanding(BaseModel):
    intent: StylingIntent
    outfit_plan: OutfitPlan
```

The Intent + Planning LLM returns `StylingUnderstanding`.
For the demo, start with one or two plausible alternative compositions, for example:

| Composition | Required roles | Optional roles |
|---|---|---|
| Separates | top, bottom, shoes | outer_layer, bag, accessory |
| Dress | dress, shoes | outer_layer, bag, accessory |
| Purchasable set | set, shoes | outer_layer, bag, accessory |

Roles describe the function of a product in the outfit; product-type constraints use the normalized garment vocabulary.
Map one-piece garments to the dress role when appropriate. Do not require a top and bottom alongside a dress.
Use `set` for a catalog-confirmed bundle sold as one purchasable product, such as a jacket-and-trousers suit.
Select it once, with one offer ID and one price; do not split it into invented component products or charge it twice.
A styled photo or a "complete the look" relation is not evidence that its pictured garments are sold together.
Bundle contents and purchasability must be supported by source catalog data; enrichment may describe only visible styling.
Outer layers or accessories become required only when the request needs them.
One composition may produce several looks; the number of compositions is not the requested look count.

Planning invariants:

- use unique `plan_id` values and unique roles within each composition;
- include a complete base: top + bottom + shoes, dress + shoes, or a confirmed set + shoes;
- preserve explicit user constraints and do not invent product IDs;
- return at least one composition unless clarification is required;
- mark optional roles explicitly; missing optional candidates do not invalidate a composition.

---

# 8. Retrieval Contract

## 8.1 Input

```python
class RetrievalRequest(BaseModel):
    intent: StylingIntent
    outfit_plan: OutfitPlan
    max_products: int = Field(default=30, ge=1, le=30)
```

## 8.2 Output

```python
class RetrievalCandidate(BaseModel):
    product: CatalogProduct
    eligible_slots: list[OutfitRole] = Field(min_length=1)
    retrieval_score: float


class MissingRequiredSlot(BaseModel):
    plan_id: str
    slot: OutfitRole


class CandidatePool(BaseModel):
    products: list[RetrievalCandidate]
    missing_required_slots: list[MissingRequiredSlot] = Field(default_factory=list)
```

## 8.3 Hard filters

Must be applied before VLM styling where relevant:

- available;
- item budget bounds and currency;
- size for the applicable outfit role;
- explicit exclusions supported by authoritative catalog attributes;
- normalized product-type constraints and requested colors from the catalog.

Stock, sizes, and prices are checked on offers. Product type, color, and other supplied facts are checked
on the corresponding visual product. Stored enrichment can guide ranking, but is not authoritative
proof of a hard constraint. Preferences requiring visual judgment are checked by the VLM using the photos.
An outfit budget can exclude an individually unaffordable offer before styling; its final sum is checked after composition.

## 8.4 Retrieval responsibility

Retriever should answer:

> Which real store products are plausible candidates for this request?

Retriever must **not** answer:

> Which complete outfit is best?

Run retrieval per outfit slot using the intent, role query, catalog facts, and stored enrichment/embeddings.
Merge and deduplicate results into one shared pool. Allocate candidates so that required roles in viable
compositions retain coverage. The configured limit applies to the entire pool, not to each role or composition.

Target pool:

```text
20–30 products total
```

Example:

```text
6 tops
6 bottoms
5 dresses
5 shoes
4 bags/accessories
```

Exact distribution may depend on the request.
`eligible_slots` identifies the roles for which each candidate passed the relevant filters.
`missing_required_slots` records roles with no candidates after filtering and pool truncation, per composition.
If no composition has all required roles covered, return a grounded limitation instead of calling the runtime VLM
with an impossible plan. Do not silently relax constraints or invent a missing item.

## 8.5 Retrieval invariants

- only real products;
- only available offers after hard filtering;
- original facts and inferred enrichment remain separate;
- no more than configured candidate limit;
- output must preserve product/offer IDs, source SKU/barcode values when present, prices, image URLs, product type, and source categories;
- candidates must have usable photos of their exact visual variant;
- every offer must satisfy the constraints of at least one declared eligible role; repeat role-specific checks after selection.
- coverage limitations describe the retrieved pool, not a claim about the entire store inventory.

---

# 9. Offline VLM Catalog Enrichment Contract

This VLM task enriches individual products once during demo catalog preparation.
Its structured output is stored in `product_enrichments` and reused by retrieval and runtime styling.
It does not generate outfits.

## 9.1 Input

```python
class EnrichmentRequest(BaseModel):
    product_id: str
    title: str
    description: str | None
    product_type: str | None = None
    source_category_names: list[str] = Field(default_factory=list)
    color: str | None = None
    catalog_attributes: dict[str, Any] = Field(default_factory=dict)
    image_urls: list[str] = Field(min_length=1, max_length=2)
```

The importer/repository supplies these original facts for the exact visual variant.
Attach the photos as actual multimodal inputs and label them with `product_id`.
Render `src/ai_stylist/prompts/enrichment.md` as a Jinja2 template before the call.

## 9.2 Output

```python
class ProductEnrichment(BaseModel):
    styles: list[str] = Field(default_factory=list)
    silhouette: str | None = None
    fit: str | None = None
    formality: str | None = None
    occasions: list[str] = Field(default_factory=list)
    texture: str | None = None
    visual_weight: str | None = None
    color_characteristics: list[str] = Field(default_factory=list)
    layering_suitability: list[str] = Field(default_factory=list)
    enriched_description: str | None = None
```

## 9.3 Enrichment rules

The VLM may infer visible styling characteristics.
Describe visible fit and silhouette of the garment, not its exact fit on a future user.
Visible texture may be inferred; exact fiber composition remains an original catalog fact.

The VLM must not infer:
- price;
- stock;
- size availability;
- exact hidden measurements;
- exact material composition unless supplied by catalog text;
- brand;
- product URL.

Unknown values should remain null or empty.
Save the validated output under the requested product ID; the application supplies `model_id` and `updated_at`.
The VLM must not overwrite catalog facts or return price, size, stock, SKU, brand, or URL fields.

## 9.4 Reuse and rebuild

- one successful enrichment result per visual variant is enough for the demo;
- reuse it unchanged across user sessions and requests;
- re-enrich when photos or relevant descriptive data change, or on an explicit rebuild;
- price, availability, and size changes update offers without triggering enrichment;
- changing the enrichment model or prompt may be followed by an explicit rebuild;
- rebuild the corresponding embedding after changing the stored enrichment.

## 9.5 Failure behavior

If photos are missing, the provider fails, or the output is invalid, keep the source product intact
and leave enrichment absent. Report the failed product during catalog preparation and retry or exclude
it from the showcase dataset explicitly. Products without usable photos cannot enter runtime VLM styling.
An absent enrichment is represented as null; do not trigger enrichment inside a user styling request.

---

# 10. VLM Stylist Contract

This is the most important contract in the demo.

## 10.1 Input

The VLM receives:

- `StylingIntent`;
- `OutfitPlan` with required and optional roles;
- a small `CandidatePool`;
- product images;
- original product facts and structured stored enrichment, supplied separately;
- eligible offers with their linked size, price, currency, and supported roles;
- stylist rules and compatibility rubric.

The VLM does **not** receive the full catalog.

Recommended input size:
- ~20–30 candidate products;
- 1–2 images per product.

The 20–30 limit counts visual variants across all slots and compositions, not size SKUs.
If provider limits make this too large, reduce the candidate pool before the VLM call while preserving
required-role coverage. Recompute coverage limitations after reducing the pool.
Embeddings, feed technical IDs, commercial SKU strings, and Telegram user IDs are not needed by the stylist.

## 10.2 VLM Request Schema

```python
class VLMOfferCandidate(BaseModel):
    offer_id: str
    eligible_slots: list[OutfitRole] = Field(min_length=1)
    size: str | None = None
    price: Decimal = Field(ge=0)
    currency: str


class VLMProductCandidate(BaseModel):
    product_id: str
    eligible_slots: list[OutfitRole] = Field(min_length=1)
    title: str
    product_type: str
    source_category_names: list[str] = Field(default_factory=list)
    color: str | None = None
    brand: str | None = None
    original_description: str | None = None
    catalog_attributes: dict[str, Any] = Field(default_factory=dict)
    inferred_styling_attributes: ProductEnrichment | None = None
    eligible_offers: list[VLMOfferCandidate] = Field(min_length=1)
    image_urls: list[str] = Field(min_length=1, max_length=2)


class VLMStylingRequest(BaseModel):
    intent: StylingIntent
    outfit_plan: OutfitPlan
    products: list[VLMProductCandidate] = Field(min_length=1, max_length=30)
    missing_required_slots: list[MissingRequiredSlot] = Field(default_factory=list)
    stylist_rules: str
    compatibility_rubric: str
```

Every `eligible_offer` is available and passed the hard filters for its declared roles.
Populate offer-level roles when size requirements differ between roles; keep the size/price relationship intact.
The application attaches each image as a separate multimodal input alongside its product ID and position.
`image_urls` is the attachment manifest; URLs rendered into prompt text alone are insufficient.
Structured enrichment supplements the photos and never replaces them.
Serialize monetary values as decimal strings, not binary floating-point estimates.

## 10.3 VLM Output Schema

```python
from pydantic import BaseModel, Field


class LookScore(BaseModel):
    style_match: float = Field(ge=0, le=1)
    color_compatibility: float = Field(ge=0, le=1)
    silhouette_compatibility: float = Field(ge=0, le=1)
    occasion_match: float = Field(ge=0, le=1)
    user_preferences_match: float = Field(ge=0, le=1)
    overall_score: float = Field(ge=0, le=1)


class SelectedLookItem(BaseModel):
    slot: OutfitRole
    product_id: str
    offer_id: str


class GeneratedLook(BaseModel):
    plan_id: str
    items: list[SelectedLookItem] = Field(min_length=1)
    score: LookScore
    explanation: str


class VLMStylingResponse(BaseModel):
    looks: list[GeneratedLook] = Field(default_factory=list, max_length=3)
    limitations: list[str] = Field(default_factory=list)
```

Each look selects one supplied composition and fills its required roles with real candidate products/offers.
Return 0–3 complete looks. `limitations` explains fewer than the target 2–3 looks or an empty result
using the supplied pool and constraints. It must not claim that an item is absent from the entire store.
The VLM returns IDs, scores, and explanations; catalog titles, URLs, photos, sizes, prices, and totals
for the final product cards are looked up or computed by the application.

## 10.4 VLM Stylist Rules

The VLM must:

- use only `product_id` values supplied in the request;
- select only `offer_id` values supplied for the corresponding product and role;
- use a supplied `plan_id` and preserve its required roles;
- never invent a product;
- never invent SKU/price/size/stock;
- never replace an input product with a visually similar imagined item;
- return maximum 3 looks;
- avoid duplicate looks;
- create complete outfits appropriate to the request;
- consider style coherence;
- consider color compatibility;
- consider silhouette and proportion balance;
- consider occasion/formality;
- consider explicit user fit/figure preferences;
- explain decisions only using visible/product/context information;
- give original catalog facts higher authority than inferred enrichment;
- keep enrichment's descriptions of individual products separate from judging the selected combination.

The VLM must not:
- infer the user's body from product images;
- claim an exact fit on the user's body without evidence;
- treat all candidate products as one outfit;
- assume that every item in the candidate pool must be mutually compatible.

Important:

> The candidate pool is a search space, not one capsule wardrobe.

The VLM should select subsets from it.

## 10.5 Application validation and failure behavior

Validate the structured result before showing it or sending it to the Response LLM:

- selected plan, product, and offer IDs belong to the request;
- each offer belongs to the selected product and is eligible for the selected role;
- every selected role belongs to the chosen composition, and its normalized product-type constraints are satisfied;
- required roles are filled exactly once; optional roles are selected at most once;
- the same product is not repeated within a look;
- selected offers satisfy availability, role-specific size, currency, and item price constraints;
- each look's total, computed with `Decimal`, satisfies the outfit budget;
- a catalog-confirmed set is represented by one item/offer and contributes its price exactly once;
- there are at most three looks and each score is in [0, 1];
- looks are distinct by their role/product composition, not merely by size SKU;
- an empty or reduced result includes a grounded limitation.

Different looks may reuse some products. If the user did not supply a size, selecting an available offer
does not establish that its size fits the user; ask for the size when a concrete purchase selection is needed.
Reject invalid outputs and record the failure. Do not show unsupported IDs or silently relax user constraints.
An empty pool, or no composition with all its required roles covered, produces a limitation without
a runtime VLM call. A covered composition may still be used when another alternative has missing roles.
Resolve final commercial SKU strings, catalog links, images, prices, and size labels through the repository;
compute totals in application code. Provider failures must produce a user-facing failure message and an error record.

---

# 11. VLM Styling Rubric

The rubric used by the VLM should define these dimensions.

## 11.1 Style match

Question:

> Does the outfit match the requested aesthetic?

Examples:
- minimal;
- classic;
- relaxed;
- feminine;
- smart casual;
- evening.

## 11.2 Color compatibility

Question:

> Do the selected items form a coherent color combination?

The model should judge the actual selected outfit only.

## 11.3 Silhouette compatibility

Question:

> Do the shapes and volumes of the selected items work together?

Examples:
- oversized top + oversized bottom may create too much visual volume;
- fitted top + wide-leg trousers may create balanced proportions.

## 11.4 Occasion match

Question:

> Is the complete outfit appropriate for the requested occasion and formality?

## 11.5 User preferences match

Question:

> Does the outfit respect explicit preferences such as emphasize waist, avoid heels, cover shoulders, relaxed fit?

## 11.6 Overall score

For the demo, `overall_score` may be returned by the VLM.

Later, if score calibration becomes important, compute it in Python.

The explanation matters more than the exact mathematical weighting in the first demo.

---

# 12. VLM Prompt Requirements

Prompt source is stored in `src/ai_stylist/prompts/stylist.md` as a Jinja2 template.
Render it with the normalized intent, outfit plan, original candidate facts, eligible offers,
stored enrichment, and stylist knowledge as template context.
Product images must be attached separately as actual multimodal inputs; rendering image URLs into Markdown is not sufficient.

The stylist prompt must explicitly state:

```text
You are evaluating and assembling outfits from a CLOSED candidate set.

You may only use the supplied plan, product, and offer IDs.
An offer must belong to its selected product and be eligible for its selected role.

The candidate products are NOT one outfit and do NOT need to all match each other.

Select subsets of products that form complete coherent looks.
Fill every required role of the selected composition. Optional roles may be omitted.
Treat a catalog-confirmed set as one purchasable item; do not invent or separately price its components.
Use the actual photos and the stored enrichment; original catalog facts have higher authority.

Do not invent missing products, colors, sizes, prices, materials, or availability.

Use user context only when it is explicitly provided.

If 2–3 strong complete looks cannot be built from the candidate pool, return fewer complete looks,
including zero if necessary, and explain why in limitations.
Describe limitations of the supplied candidate pool, not of the entire store inventory.
```

The prompt should include:
- normalized intent JSON;
- outfit plan and required/optional roles;
- product and offer IDs;
- titles, normalized product types, and relevant source category names;
- relevant original catalog facts and structured stored enrichment in separate fields;
- eligible offers with size, price, currency, and role eligibility;
- product images as actual multimodal image inputs;
- stylist rubric.

---

# 13. Response LLM Contract

## 13.1 Input

```python
class ResponseRequest(BaseModel):
    intent: StylingIntent
    styling_result: VLMStylingResponse
```

## 13.2 Output

```python
class StylingResponse(BaseModel):
    message: str
    looks: list[GeneratedLook]
    limitations: list[str] = Field(default_factory=list)
    follow_up_question: str | None = None
```

Rules:
- receive only application-validated styling results;
- preserve selected plan IDs and exact slot/product/offer assignments;
- preserve limitations, including the reason for an empty result;
- no new products;
- no invented catalog facts;
- explanations should be short and user-facing.

The application adds catalog product cards and computes prices/totals after rewriting the explanation.
Check that the Response LLM has preserved the validated item assignments; it must not select or replace offers.

---

# 14. Telegram Flow

Telegram is the only required interface.

## 14.1 Entry

User enters AI stylist mode through:
- button;
- command;
- keyword.

## 14.2 Active session

While stylist mode is active:
- normal user text goes to the styling pipeline;
- the current intent and outfit plan may be updated over multiple messages;
- clarification questions are answered within the same stored dialogue;
- stored product enrichment is reused for every request.

## 14.3 Exit

Support:
- explicit "finish styling" button;
- switch back to main bot scenario;
- reset command.

No standalone API is required for the demo.

---

# 15. Stylist Knowledge

Store professional stylist knowledge in Markdown:

```text
knowledge/
  stylist_rules.md
  compatibility_rubric.md
```

Use it for:
- intent interpretation;
- VLM outfit creation;
- VLM judging;
- evals.

The first purpose of stylist involvement is **evaluation**, not fine-tuning.

---

# 16. Demo Dataset

## 16.1 Dataset selection

Use a bounded subset of the real catalog.

Recommended size:

```text
200–500 product groups
```

Requirements:
- enough tops;
- enough bottoms;
- dresses if relevant;
- outer layers;
- shoes;
- bags/accessories;
- enough product images;
- enough price/size availability to build complete looks.

The demo dataset must be versioned or snapshot-able so eval results remain comparable.
Count visual model/color variants, not size offer rows. Select the subset after verified availability,
audience, photo, and normalized product-type checks, with coverage across required outfit roles.
The first N feed rows are useful for format inspection but do not establish a balanced demo dataset.

## 16.2 12Storeez feed observations

Source: [12Storeez catalog feed](https://12storeez.com/export/export_mindbox.xml).
Initial inspection uses a user-provided plain-text sample. XML tags and attributes were lost when copying;
the observations below describe visible values, not verified XML field names or complete-catalog statistics.
The feed's filename does not require Mindbox integration; importing this public XML is the catalog adapter's responsibility.

| Observation in the sample | Contract consequence |
|---|---|
| Jeans code `22086532` repeats with XS, S, and M; title, blue color, photo, and URL stay the same | Candidate evidence for one visual product with multiple size offers; confirm the grouping key in XML before importing |
| Different 13-digit values accompany those sizes, such as `2000000054711` and `2000000054728` | They may be barcodes or another source identifier; keep strings and verify their XML fields before assigning offer IDs or SKU values |
| Several category-like IDs accompany a product, such as `100003`, `100017`, and `100082` | Support multiple source category memberships; confirm ID-to-name and parent mappings from XML |
| Category names include garments, materials, audiences, and collections: "Брюки", "Кашемир", "Женщинам", "Новинки" | Preserve source categories, then map garment types separately for retrieval; editorial/material nodes are not garment roles |
| "Костюм: жакет двубортный и брюки" appears with size variants and one listed price | Support a `set + shoes` composition once the source confirms the product is a purchasable bundle |
| Prices such as `6900` appear with the currency value `RUR` | Use an explicit feed-specific `RUR` to `RUB` normalization, preserving the numeric amount and raw source code |
| Russian names, short English names, colors, "женский", product URLs, and image URLs are visible | Preserve confirmed source facts; a short English name does not establish a detailed product description |
| The sample shows photo links repeated across size variants | Deduplicate images at the visual-product level and enrich that product once; other available photos must be checked in XML |
| Sequences such as `0 5`, "НЕТ", and `false false` appear without field names | Do not infer online availability, stock channels, preorders, or other flags from their position or value alone |

The larger plain-text sample adds these observations:

| Observation | Contract consequence |
|---|---|
| Skirt `8277238` has values `2980` and `5980`; top `8289471` has `900` and `2900` | A current/previous-price pair is plausible but not verified; map both XML fields before assigning `price` and `old_price` |
| Shoe `81849163` repeats with labels 36 through 41, separate long identifiers, and the same photo | Preserve individual shoe offers and filter shoes by their own requested size; the size system is not established by the copied numbers |
| Bag `8009799` is accompanied by the label "midi" where clothing rows show XS or "One size" | Size-like variant labels are strings interpreted by product type; a bag label is not a clothing fit size |
| Both "Черный" and "Чёрный" occur, alongside specific colors such as "Кофе с молоком" and "Коричнево-розовый в клетку" | Keep original color labels and use explicit aliases for search; preserve meaningful shade and pattern distinctions |
| "Топ на тонких бретелях" appears as `8289733` in milk white and `8289737` in black | Identical titles are not grouping keys; keep different source codes/colors as separate visual variants |
| Titles such as "Платье миди без рукавов из кашемира" and English text such as "Drawstring waist zip jacket" contain useful styling details | Pass original texts to enrichment/retrieval; supplied material words do not establish exact fiber percentages |
| Scarf `104238` links to an image path containing `outerwear/7015998`; many image paths contain historical year folders | Use the XML product-to-image association; paths do not prove a product ID, category, freshness, or that the image is incorrect |
| The expanded fragment includes tops, bottoms, dresses, outer layers, sets, footwear, knitwear, and accessories | Role coverage appears feasible for dataset selection, but availability still requires the verified source rule |

Store original color text in `product_groups.color`. Apply explicit aliases such as "Черный"/"Чёрный"
for matching, without rewriting source labels or merging products by a color alias or title alone.
The larger sample still lacks XML tags and attributes; it does not resolve offer identity,
availability, stock channels, or the meaning of every additional price-like value.

## 16.3 XML mapping to confirm before implementation

Inspect a bounded raw XML sample containing category definitions and several complete offer elements,
including multiple sizes of one visual variant. Record the exact element/attribute mapping for:

- the unique offer identifier, commercial SKU if present, barcode if present, and visual-product grouping key;
- all category memberships, category names, and supplied parent links;
- size, color, audience, original titles/descriptions, image URLs, and product URLs;
- current payable price, any previous/reference price, and currency;
- availability, each stock quantity/channel, and any preorder or promotional flags;
- bundle contents and whether the listed offer buys the complete set.

The illustrative numbers and category order above must not become hardcoded parsing rules.
Confirm that size variants share the same model/color and that different colors are not merged.
Resolve availability for the intended purchase channel using verified fields; positive unidentified numbers
are not sufficient evidence of purchasability. Report unresolved mappings rather than guessing their meaning.
The adapter must explicitly supply a normalized currency for every offer, preserving its source value in
`source_attributes`. The `RUR` to `RUB` rule is code normalization, with no price scaling or exchange-rate conversion;
other or missing currency codes require an explicit mapping or import diagnostic.
If additional descriptions or images are present, preserve them; if absent, leave the corresponding facts empty.
Use the confirmed raw XML elements as importer fixtures before importing the demo snapshot.

---

# 17. Evaluation

Prepare 10–20 representative scenarios.

Each scenario stores:

```text
scenario_id
user_request
expected hard constraints
catalog snapshot / version
outfit plan
candidate pool and required-role coverage
generated looks
stylist verdict
stylist comments
```

Stylist verdict:

```text
approve
revise
reject
```

Primary metric:

> Does a professional stylist approve the generated complete look for the user request?

Supporting metrics:
- scenario coverage;
- number of valid complete looks;
- retrieval coverage;
- required-role coverage and composition completeness;
- VLM failure rate;
- latency;
- cost per run.

Do not treat VLM self-score as proof of quality.

---

# 18. Acceptance Criteria

The demo is ready when:

1. real catalog data can be imported with field mappings verified against raw XML fixtures;
2. model/color variants, size offers, exact variant images, and all source category memberships are stored separately;
3. demo products are enriched offline from actual photos, and the structured results are persisted;
4. unchanged product enrichment is reused without per-request enrichment calls;
5. product embeddings can be generated from catalog data and saved enrichment;
6. user dialogue produces valid `StylingIntent` and `OutfitPlan`, or a clarification question;
7. retrieval runs per role and returns at most the configured 30 visual candidates in total;
8. missing required roles are reported explicitly and impossible plans are not sent to the stylist;
9. hard filters use verified availability and normalized garment types, preserving size/price/offer relationships;
10. both enrichment and runtime styling receive actual visual inputs;
11. the runtime stylist receives original facts, stored enrichment, and eligible offers;
12. selected plan, product, offer, and role assignments are validated against the request;
13. the VLM produces at most 3 complete, distinct looks, or grounded limitations;
14. item and outfit budgets use the confirmed current payable price and are checked in application code with decimal monetary values;
15. no price/stock/size/SKU is invented, and final catalog details are resolved by the application;
16. final output is shown through Telegram with selected assignments preserved;
17. showcase scenarios produce 2–3 strong looks where the catalog supports them;
18. client stylists approve the selected demo scenarios;
19. confirmed purchasable sets can fill a `set` role and are counted once in outfit pricing;
20. offer IDs, source SKU values, barcodes, and the feed currency normalization are not conflated or guessed.

---

# 19. Out of Scope

Do not build yet:

- Mindbox integration;
- behavioral personalization;
- purchase-history recommendation;
- RL;
- DPO;
- custom fashion model;
- manual compatibility graph;
- LangGraph;
- Temporal;
- microservices;
- separate internal REST API;
- virtual try-on;
- long-term memory;
- complex observability infrastructure;
- production-grade model routing;
- advanced retry/fingerprint/provenance system.

---

# 20. Development Order

Implement in this order:

```text
1. Database schema + verified raw XML field mapping + feed import
2. Catalog snapshot for demo
3. One-time VLM product enrichment + persistence
4. Minimal embeddings + retrieval per outfit slot
5. StylingIntent + OutfitPlan in one LLM call
6. Runtime VLM Stylist contract + application validation
7. Run the stylist against real candidate images and saved enrichment
8. Produce 2–3 complete looks with role/product/offer assignments
9. Review with stylists
10. Improve retrieval/enrichment only where evals show failures
11. Connect stable flow to Telegram
```

Priority:

```text
outfit quality
    >
correct user intent
    >
VLM styling
    >
retrieval sophistication
    >
integrations
```

The demo should optimize for visible styling quality, not infrastructure completeness.
