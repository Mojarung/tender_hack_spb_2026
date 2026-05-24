from __future__ import annotations

from pricepulse.enrichment.attributes import (
    attribute_match_score,
    extract_attributes,
    extract_query_attributes,
)


def test_extracts_iphone_query_attributes() -> None:
    attrs = extract_query_attributes("iphone 15 черный 128 GB")

    assert attrs.category == "smartphone"
    assert attrs.brand == "apple"
    assert attrs.model == "iphone 15"
    assert attrs.color == "black"
    assert attrs.storage_gb == 128
    assert attrs.confidence > 0.7


def test_extracts_tyre_attributes() -> None:
    attrs = extract_query_attributes("шины 205/55 r16 зимние шипованные")

    assert attrs.category == "tyre"
    assert attrs.tyre_width_mm == 205
    assert attrs.tyre_profile == 55
    assert attrs.tyre_rim_inch == 16
    assert attrs.season == "winter"
    assert attrs.studs is True


def test_extracts_non_studded_tyre_before_studded_substring() -> None:
    attrs = extract_query_attributes("шины 205/55 r16 зимние нешипованные липучка")

    assert attrs.category == "tyre"
    assert attrs.studs is False


def test_extracts_office_equipment_attributes() -> None:
    attrs = extract_query_attributes("принтер лазерный цветной wifi")

    assert attrs.category == "office_equipment"
    assert attrs.device_type == "printer"
    assert attrs.print_technology == "laser"
    assert attrs.color_print is True
    assert attrs.wifi is True


def test_extracts_office_supply_stapler_attributes() -> None:
    attrs = extract_query_attributes("степлер №24/6 до 30 листов")

    assert attrs.category == "office_supply"
    assert attrs.device_type == "stapler"
    assert attrs.staple_size == "24/6"
    assert attrs.sheet_capacity == 30


def test_office_supply_ranking_prefers_requested_stapler() -> None:
    query = extract_query_attributes("степлер 24/6")
    stapler = extract_query_attributes("Степлер металлический №24/6 до 30 листов")
    staples = extract_query_attributes("Скобы для степлера №24/6")
    paper = extract_query_attributes("Бумага офисная A4 80 г/м2 500 листов")

    assert attribute_match_score(query, stapler) > attribute_match_score(query, staples)
    assert attribute_match_score(query, paper) == 0.0


def test_extracts_samsung_and_xiaomi_phone_attributes() -> None:
    samsung = extract_query_attributes("самсунг s24 ultra 256 серый")
    xiaomi = extract_query_attributes("xiaomi redmi note 13 8/256 синий")

    assert samsung.category == "smartphone"
    assert samsung.brand == "samsung"
    assert samsung.model == "galaxy s24 ultra"
    assert samsung.color == "gray"
    assert samsung.storage_gb == 256
    assert xiaomi.category == "smartphone"
    assert xiaomi.model == "redmi note 13"
    assert xiaomi.ram_gb == 8
    assert xiaomi.storage_gb == 256


def test_extracts_consumables_and_paper_attributes() -> None:
    cartridge = extract_query_attributes("картридж hp 12a")
    paper = extract_query_attributes("бумага A4 80 gsm 500 листов")

    assert cartridge.category == "cartridge"
    assert cartridge.brand == "hp"
    assert cartridge.model == "hp 12a"
    assert paper.category == "paper"
    assert paper.paper_format == "A4"
    assert paper.density_gm2 == 80
    assert paper.sheets_count == 500


def test_extracts_laptop_audio_and_vacuum_attributes() -> None:
    macbook = extract_query_attributes("макбук эйр м3 512 темно-синий")
    headphones = extract_query_attributes("наушники sony wh-1000xm5 черные")
    airpods = extract_query_attributes("airpods pro 2 белые")
    vacuum = extract_query_attributes("робот пылесос xiaomi черный")

    assert macbook.category == "laptop"
    assert macbook.brand == "apple"
    assert macbook.model == "macbook air m3"
    assert macbook.storage_gb == 512
    assert macbook.color == "blue"
    assert headphones.category == "headphones"
    assert headphones.brand == "sony"
    assert headphones.model == "wh-1000xm5"
    assert airpods.category == "headphones"
    assert airpods.brand == "apple"
    assert airpods.model == "airpods pro 2"
    assert vacuum.category == "robot_vacuum"
    assert vacuum.brand == "xiaomi"


def test_extracts_apparel_category_specific_attributes() -> None:
    attrs = extract_query_attributes("женская зимняя куртка размер M черная полиэстер 170 см")

    assert attrs.category == "apparel"
    assert attrs.apparel_type == "jacket"
    assert attrs.gender == "women"
    assert attrs.size == "M"
    assert attrs.color == "black"
    assert attrs.material == "polyester"
    assert attrs.season == "winter"
    assert attrs.height_cm == 170


def test_extracts_other_office_and_it_attributes() -> None:
    monitor = extract_query_attributes("монитор 27 дюймов 144hz ips 1920x1080")
    projector = extract_query_attributes("проектор epson full hd 3000 лм")
    shredder = extract_query_attributes("шредер p-4 до 10 листов 20 л")
    laminator = extract_query_attributes("ламинатор A4 125 мкм")

    assert monitor.category == "monitor"
    assert monitor.screen_size_inch == 27
    assert monitor.refresh_rate_hz == 144
    assert monitor.matrix_type == "IPS"
    assert monitor.resolution == "1920x1080"
    assert projector.category == "projector"
    assert projector.resolution == "1920x1080"
    assert projector.brightness_lm == 3000
    assert shredder.category == "office_equipment"
    assert shredder.device_type == "shredder"
    assert shredder.security_level == "P-4"
    assert shredder.sheet_capacity == 10
    assert shredder.bin_volume_l == 20
    assert laminator.device_type == "laminator"
    assert laminator.paper_format == "A4"
    assert laminator.laminating_thickness_microns == 125


def test_extracts_broad_categories_without_overfitting_specs() -> None:
    assert extract_query_attributes("монитор 27 144hz ips").category == "monitor"
    ssd = extract_query_attributes("ssd 1tb samsung 980 pro")
    assert ssd.category == "storage"
    assert ssd.storage_gb == 1024
    assert extract_query_attributes("клавиатура logitech беспроводная").category == "input_device"
    assert extract_query_attributes("мышь logitech mx master 3s").brand == "logitech"
    assert extract_query_attributes("проектор epson full hd").category == "projector"
    laminator = extract_query_attributes("ламинатор A4")
    shredder = extract_query_attributes("шредер офисный")
    assert laminator.category == "office_equipment"
    assert laminator.device_type == "laminator"
    assert shredder.device_type == "shredder"
    assert extract_query_attributes("перчатки нитриловые размер m").category == "apparel"
    assert extract_query_attributes("кофемашина delonghi magnifica").category == "coffee_machine"


def test_extracts_hyphenated_robot_vacuum_and_brown_color() -> None:
    attrs = extract_query_attributes("робот-пылесос xiaomi коричневый")

    assert attrs.category == "robot_vacuum"
    assert attrs.brand == "xiaomi"
    assert attrs.color == "brown"


def test_extract_attributes_filters_noisy_characteristics() -> None:
    attrs = extract_attributes(
        "Футболка черная хлопок",
        {
            "subject_parent_id": "192",
            "stock": "57",
            "warehouse_id": "507",
            "eta_max_hours": "24",
            "colors": "черный",
        },
    )

    assert attrs.category == "apparel"
    assert attrs.color == "black"
    assert attrs.material == "cotton"
    assert attrs.size is None


def test_attribute_score_penalizes_wrong_model_and_category() -> None:
    query = extract_query_attributes("iphone 15 black")
    exact = extract_query_attributes("Apple iPhone 15 128GB Black")
    wrong_model = extract_query_attributes("Apple iPhone 15 Pro 128GB Black")
    accessory = extract_query_attributes("Чехол для Apple iPhone 15 Black")

    assert attribute_match_score(query, exact) > attribute_match_score(query, wrong_model)
    assert attribute_match_score(query, accessory) == 0.0


def test_missing_offer_attributes_get_partial_score_not_zero() -> None:
    query = extract_query_attributes("iphone 15 black 128 GB")

    assert 0 < attribute_match_score(query, None) < 0.5
