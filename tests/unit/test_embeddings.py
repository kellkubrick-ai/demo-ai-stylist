from ai_stylist.catalog.embeddings import embedding_text
from ai_stylist.schemas.catalog import CatalogCategory, CatalogProduct, ProductGroup
from ai_stylist.schemas.enrichment import ProductEnrichment


def test_embedding_text_is_a_short_description_without_field_names():
    record = CatalogProduct(
        product=ProductGroup(
            id="dress-1",
            external_id="dress-1",
            title="Платье с шарфом из шерсти и шелка",
            color="Пыльно-бирюзовый",
            source_categories=[
                CatalogCategory(
                    id="missing",
                    external_id="100084",
                    name="[Название отсутствует в XML: 100084]",
                )
            ],
        ),
        offers=[],
        enrichment=ProductEnrichment(
            silhouette="приталенное",
            styles=["минималистичное", "элегантное"],
            occasions=["ужина"],
            enriched_description="С V-образным вырезом и разрезом.",
        ),
    )

    assert embedding_text(record) == (
        "Платье с шарфом из шерсти и шелка в оттенке «Пыльно-бирюзовый». "
        "С V-образным вырезом и разрезом. "
        "Приталенное, минималистичное, элегантное. Подходит для ужина."
    )


def test_embedding_text_uses_feed_facts_without_enrichment():
    record = CatalogProduct(
        product=ProductGroup(
            id="shirt-1",
            external_id="shirt-1",
            title="Рубашка",
            description="Из хлопка.",
            color="Белый",
            catalog_attributes={"features": ["свободный крой"], "title_en": "Shirt"},
        ),
        offers=[],
    )

    assert embedding_text(record) == ("Рубашка в оттенке «Белый». Из хлопка. Свободный крой.")
