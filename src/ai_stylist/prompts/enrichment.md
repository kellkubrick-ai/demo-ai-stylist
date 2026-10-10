Describe one exact visual product variant from the supplied catalog facts and attached actual photos.
Return ProductEnrichment only. This is offline product description, not outfit generation.
Original catalog facts have higher authority than inferred visual attributes.

Populate two distinct groups of fields:

1. Observation fields must be grounded in the photos: silhouette, fit, texture, visual_weight and
   color_characteristics. Catalog color may confirm color. Describe only visible shape, proportions,
   draping and surface character.
2. Interpretation fields may interpret the observed item: styles, formality, occasions and
   layering_suitability. Catalog categories may inform occasions, but must not override the photos.

Calibrate construction claims to the available evidence. Do not call overlapping panels a wrap,
a visible line a functional pocket, or a small fitting a working closure unless that construction is
clearly visible or supplied by catalog text. When uncertain, describe the observed geometry instead,
for example "перекрывающиеся асимметричные передние полотнища". Do not infer a lining, fastening mechanism,
detachable part or hidden construction.

layering_suitability describes visual layering role and compatibility only. It may say that an item
can be the main outer element or visually works with a thin, low-volume lower layer. It must not claim
physical room, warmth, stretch, freedom of movement or whether another garment can actually fit
under it. Return an empty list when layering is not visually relevant, including most shoes and bags.

Field contract:
- styles: 0-4 concise visual style labels; do not use construction details;
- silhouette: one evidence-based visual phrase, or null;
- fit: one phrase about the visible cut of the item itself, or null when uncertain/not applicable;
- formality: one concise visual assessment, or null;
- occasions: 0-4 plausible situations supported by appearance and optional catalog categories;
- texture: one visible surface description, or null; never infer fiber composition from appearance;
- visual_weight: exactly "лёгкий", "средний", "тяжёлый", or null;
- color_characteristics: 0-4 visible color/tonal phrases;
- layering_suitability: 0-3 visual layering observations as defined above;
- enriched_description: 2-3 factual sentences synthesizing supported facts and visible attributes.

Use null for an uncertain or inapplicable singular field and [] for an uncertain or inapplicable list.
Write every textual value in Russian using Cyrillic. Translate fashion terms naturally; do not
return English tags or Latin abbreviations. Technical JSON field names remain unchanged.
Do not infer a future user's body or exact fit. Do not invent price, stock, SKU, sizes, measurements,
hidden material composition, brand, URL or bundle purchasability. Material facts must be supplied.
The product_id accompanying every photo identifies the same exact visual variant.
