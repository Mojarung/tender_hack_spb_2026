from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from pricepulse.domain.enums import SourceKind
from pricepulse.domain.models import NormalizedQuery, ProductOffer
from pricepulse.enrichment.normalization import (
    canonical_fields,
    canonicalize_characteristics,
    normalize_structured_characteristics,
)
from pricepulse.orchestrator.search import SearchOrchestrator
from pricepulse.scrapers.base import ScrapeResult


def test_marketplace_memory_keys_normalize_to_same_canonical_attribute() -> None:
    samples = [
        {"Объем встроенной памяти": "256 ГБ"},
        {"Встроенная память": "256GB"},
        {"Память": "256 GB"},
        {"ROM": "256"},
    ]

    for sample in samples:
        canonical = canonicalize_characteristics(sample, category="smartphone")
        fields = canonical_fields(canonical)

        assert fields["storage_gb"] == 256
        assert canonical.attributes["storage_gb"].unit == "GB"
        assert canonical.attributes["storage_gb"].source_key in sample
        assert canonical.confidence > 0.8


def test_canonical_characteristics_preserve_provenance_and_extra() -> None:
    canonical = canonicalize_characteristics(
        {
            "Бренд": "Apple",
            "Цвет товара": "синий титан",
            "Маркетинговое описание": "новый оригинальный товар",
        },
        category="smartphone",
    )

    assert canonical.category == "smartphone"
    assert canonical.attributes["brand"].value == "apple"
    assert canonical.attributes["brand"].source_value == "Apple"
    assert canonical.attributes["color"].value == "blue"
    assert canonical.extra == {"Маркетинговое описание": "новый оригинальный товар"}


def test_legacy_structured_normalizer_uses_canonical_view() -> None:
    fields, extra = normalize_structured_characteristics({
        "Объем памяти устройства": "1 ТБ",
        "Производитель": "Samsung",
        "Незнакомый ключ": "abc",
    })

    assert fields["storage_gb"] == 1024
    assert fields["brand"] == "samsung"
    assert extra == {"Незнакомый ключ": "abc"}


def test_office_supply_characteristics_normalize_to_comparable_fields() -> None:
    canonical = canonicalize_characteristics(
        {
            "Размер скоб": "№24/6",
            "Пробивная способность, листов": "30",
            "Максимальная глубина закладки бумаги": "65 мм",
        },
        category="office_supply",
    )
    fields = canonical_fields(canonical)

    assert fields["staple_size"] == "24/6"
    assert fields["sheet_capacity"] == 30
    assert fields["binding_depth_mm"] == 65


def test_printer_characteristics_normalize_to_comparable_fields() -> None:
    canonical = canonicalize_characteristics(
        {
            "Цветность печати": "цветная",
            "Беспроводные интерфейсы": "Wi-Fi",
            "Технология печати": "лазерная",
            "Максимальный формат печати": "A4",
            "Скорость печати А4": "18 стр/мин",
        },
        category="office_equipment",
    )
    fields = canonical_fields(canonical)

    assert fields["color_print"] is True
    assert fields["wifi"] is True
    assert fields["print_technology"] == "laser"
    assert fields["paper_format"] == "A4"
    assert fields["print_speed_ppm"] == 18


def test_cartridge_characteristics_normalize_to_comparable_fields() -> None:
    canonical = canonicalize_characteristics(
        {
            "Цвет тонера/чернил": "черный",
            "Ресурс": "2000 страниц",
            "Количество в упаковке, шт": "2",
            "Оригинальность расходника": "совместимый",
            "Совместимость картриджа": "Canon 725",
        },
        category="cartridge",
    )
    fields = canonical_fields(canonical)

    assert fields["color"] == "black"
    assert fields["page_yield"] == 2000
    assert fields["pack_count"] == 2
    assert fields["original_consumable"] is False
    assert fields["compatible_models"] == "Canon 725"


def test_apparel_characteristics_normalize_to_comparable_fields() -> None:
    canonical = canonicalize_characteristics(
        {
            "Вид одежды": "Куртка",
            "Пол": "женский",
            "Размер": "M",
            "Состав": "полиэстер 100%",
            "Рост": "170 см",
            "Утеплитель": "синтепон",
        },
        category="apparel",
    )
    fields = canonical_fields(canonical)

    assert fields["apparel_type"] == "jacket"
    assert fields["gender"] == "women"
    assert fields["size"] == "M"
    assert fields["material"] == "polyester"
    assert fields["height_cm"] == 170
    assert fields["insulation"] == "synthetic"


def test_other_office_characteristics_normalize_to_comparable_fields() -> None:
    monitor = canonical_fields(canonicalize_characteristics(
        {
            "Диагональ экрана": "27\"",
            "Частота обновления": "144 Гц",
            "Разрешение экрана": "1920x1080",
            "Тип матрицы": "IPS",
        },
        category="monitor",
    ))
    shredder = canonical_fields(canonicalize_characteristics(
        {
            "Уровень секретности": "P-4",
            "Пробивная способность": "10 листов",
            "Объем корзины": "20 л",
        },
        category="office_equipment",
    ))

    assert monitor["screen_size_inch"] == 27
    assert monitor["refresh_rate_hz"] == 144
    assert monitor["resolution"] == "1920x1080"
    assert monitor["matrix_type"] == "IPS"
    assert shredder["security_level"] == "P-4"
    assert shredder["sheet_capacity"] == 10
    assert shredder["bin_volume_l"] == 20


class _Stub:
    source = SourceKind.WB

    def __init__(self, offer: ProductOffer) -> None:
        self._offer = offer

    async def search(
        self,
        query: NormalizedQuery,
        limit: int,
        on_offer: Any = None,
        *,
        region_id: int = 213,
    ) -> ScrapeResult:
        if on_offer is not None:
            await on_offer(self._offer)
        return ScrapeResult(source=self.source, offers=[self._offer])


@pytest.mark.asyncio
async def test_orchestrator_enriches_offer_with_canonical_characteristics() -> None:
    offer = ProductOffer(
        source=SourceKind.WB,
        name="Apple iPhone 15 Pro",
        price=Decimal("99999"),
        url="https://www.wildberries.ru/catalog/1/detail.aspx",
        characteristics={
            "Бренд": "Apple",
            "Модель": "iPhone 15 Pro",
            "Объем встроенной памяти": "256 ГБ",
        },
        fetched_at=datetime.now(tz=UTC),
    )
    orch = SearchOrchestrator(adapters={SourceKind.WB: _Stub(offer)})

    _, groups, _, _ = await orch.run("айфон 15 про 256 гб", max_per_source=1)

    enriched = groups[0].offers[0]
    assert enriched.attributes is not None
    assert enriched.attributes.storage_gb == 256
    assert enriched.canonical_characteristics is not None
    assert canonical_fields(enriched.canonical_characteristics)["storage_gb"] == 256
