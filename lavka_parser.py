"""
Парсер условий доставки Яндекс Лавки с авторизацией через мобильное API.

Использует OAuth access_token из lavka_auth.py для получения:
  - стоимости доставки (delivery_price)
  - стоимости упаковки (packaging_price / bag_price)
  - стоимости сборки (assembly_price / picking_fee)
  - минимальной суммы заказа (min_order)
  - порога бесплатной доставки (free_from)
  - времени доставки (delivery_time)
  - тарифной сетки по суммам корзины

API Яндекс Лавки (мобильное приложение):
  Base: https://api.lavka.yandex.net  (мобильный API)
  Alt:  https://lavka.yandex.ru/api   (веб API)
"""

import re
import json
import requests
import urllib3
from typing import Optional

from lavka_auth import get_valid_token, get_token_info

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_LAVKA_API_BASE  = "https://api.lavka.yandex.net"
_LAVKA_WEB_BASE  = "https://lavka.yandex.ru"

# Суммы корзины для тарифной сетки (₽)
TIER_CART_SUMS = [300, 400, 500, 600, 800, 1000, 1500]

# Заголовки мобильного приложения Яндекс Лавки
_APP_HEADERS = {
    "User-Agent": "ru.yandex.lavka/3.100.0 (Android 13; Pixel 7)",
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "X-Yandex-Client-ID": "c0ebe342af7d48fbbbfcf2d2eedb8f9e",
    "X-Platform": "android",
    "X-App-Version": "3.100.0",
}

# Заголовки веб-версии (fallback)
_WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Origin": "https://lavka.yandex.ru",
    "Referer": "https://lavka.yandex.ru/",
}


def _make_result() -> dict:
    return {
        "service": "Яндекс Лавка",
        "logo": "🔵",
        "available": False,
        "delivery_price": None,
        "min_order": None,
        "free_from": None,
        "delivery_time": None,
        "packaging_price": None,
        "assembly_price": None,
        "delivery_tiers": [],
        "note": "",
        "error": None,
        "data_source": "api",
        "auth_used": False,
    }


def _compute_tiers(delivery_price, min_order, free_from,
                   cart_sums=None) -> list:
    """Вычисляет тарифную сетку доставки для заданных сумм корзины."""
    if cart_sums is None:
        cart_sums = TIER_CART_SUMS
    tiers = []
    dp = delivery_price if delivery_price is not None else 0
    mo = min_order if min_order is not None else 0
    ff = free_from
    for s in cart_sums:
        if mo > 0 and s < mo:
            tiers.append({"cart_sum": s, "delivery_price": None, "available": False})
        elif ff is not None and s >= ff:
            tiers.append({"cart_sum": s, "delivery_price": 0, "available": True})
        else:
            tiers.append({"cart_sum": s, "delivery_price": dp, "available": True})
    return tiers


def _extract_price_from_template(tmpl_str: str) -> Optional[int]:
    """
    Извлекает числовую цену из строки шаблона Лавки.
    Примеры: "109 $SIGN$$CURRENCY$", "0 ₽", "109 ₽"
    """
    if not tmpl_str:
        return None
    # Убираем шаблонные переменные и пробелы
    cleaned = tmpl_str.replace("$SIGN$", "").replace("$CURRENCY$", "")
    cleaned = cleaned.replace("\u202f", "").replace("\u00a0", "").replace(" ", "")
    nums = re.findall(r'\d+', cleaned)
    return int(nums[0]) if nums else None


def _parse_reward_block(reward_block: dict, result: dict) -> bool:
    """
    Разбирает rewardBlock из API Лавки.
    Извлекает тарифную сетку, порог бесплатной доставки и полную стоимость доставки.

    Структура item:
      - cartCostThresholdValue: порог суммы корзины (строка)
      - isFreeDeliveryStep: True если доставка бесплатна при этом пороге
      - templateVars.deliveryCost: текущая стоимость доставки (может быть "0 ₽" при акции)
      - templateVars.fullDeliveryCost: полная стоимость без акции ("109 $SIGN$$CURRENCY$")
    """
    items = reward_block.get("items", [])
    if not items:
        return False

    lavka_tiers_raw = []
    for item in items:
        if item.get("type") != "delivery":
            continue
        threshold_str = item.get("cartCostThresholdValue", "0")
        try:
            threshold = int(float(threshold_str))
        except (ValueError, TypeError):
            threshold = 0

        tmpl = item.get("templateVars", {})
        is_free = item.get("isFreeDeliveryStep", False)

        if is_free:
            step_price = 0
            if threshold > 0 and result["free_from"] is None:
                result["free_from"] = threshold
            # Извлекаем полную стоимость доставки (без акции) из fullDeliveryCost
            full_cost_str = tmpl.get("fullDeliveryCost", "")
            full_price = _extract_price_from_template(full_cost_str)
            if full_price is not None and full_price > 0:
                # Сохраняем как "реальную" стоимость доставки (без акции)
                result["_full_delivery_cost"] = full_price
        else:
            full_cost_str = tmpl.get("fullDeliveryCost", "")
            nums = re.findall(r'\d+', full_cost_str.replace("\u202f", "").replace(" ", ""))
            step_price = int(nums[0]) if nums else (result["delivery_price"] or 0)

        lavka_tiers_raw.append({"threshold": threshold, "price": step_price})

    if not lavka_tiers_raw:
        return False

    # Сортируем по убыванию порога
    lavka_tiers_raw.sort(key=lambda x: x["threshold"], reverse=True)
    tiers = []
    mo = result["min_order"] or 0
    for s in TIER_CART_SUMS:
        if mo > 0 and s < mo:
            tiers.append({"cart_sum": s, "delivery_price": None, "available": False})
            continue
        price = None
        for tier_raw in lavka_tiers_raw:
            if s >= tier_raw["threshold"]:
                price = tier_raw["price"]
                break
        if price is None:
            price = lavka_tiers_raw[-1]["price"] if lavka_tiers_raw else 0
        tiers.append({"cart_sum": s, "delivery_price": price, "available": True})

    result["delivery_tiers"] = tiers
    return True


def _parse_service_info(data: dict, result: dict) -> bool:
    """Разбирает ответ /api/v1/providers/v2/service-info."""
    found = False

    service_meta = data.get("serviceMetadata", {})
    status = service_meta.get("status", "")
    if status not in ("open", ""):
        return False

    # Время доставки — сохраняем как есть (может содержать тире: "15–35 мин")
    delivery_time = service_meta.get("deliveryTime")
    if delivery_time:
        dt_str = str(delivery_time).strip()
        # Нормализуем тире (en-dash → дефис) для единообразия
        dt_str = dt_str.replace("\u2013", "-").replace("\u2014", "-")
        # Добавляем " мин" если нет
        if dt_str and "мин" not in dt_str.lower() and dt_str.replace("-", "").isdigit():
            dt_str = f"{dt_str} мин"
        result["delivery_time"] = dt_str

    # Стоимость доставки и мин. заказ
    pricing = data.get("pricingConditions", {})
    delivery_cost = pricing.get("deliveryCost")
    if delivery_cost is not None:
        result["delivery_price"] = int(delivery_cost)
        found = True

    min_cart = pricing.get("minimalCartPrice")
    if min_cart is not None:
        result["min_order"] = int(min_cart)
        found = True

    # Упаковка (bag_price / packagingPrice)
    bag_price = pricing.get("bagPrice") or pricing.get("bag_price") or pricing.get("packagingPrice")
    if bag_price is not None:
        result["packaging_price"] = int(bag_price)

    # Сборка (picking_fee / assemblyPrice / serviceFee)
    picking_fee = (pricing.get("pickingFee") or pricing.get("picking_fee") or
                   pricing.get("assemblyPrice") or pricing.get("serviceFee") or
                   pricing.get("service_fee"))
    if picking_fee is not None:
        result["assembly_price"] = int(picking_fee)

    # Тарифная сетка из rewardBlock
    reward_block = data.get("rewardBlock", {})
    if reward_block:
        _parse_reward_block(reward_block, result)

    # personalLogisticInfo
    personal_info = data.get("personalLogisticInfo", {})
    delivery_conditions = personal_info.get("deliveryConditions", [])
    for cond in delivery_conditions:
        if cond.get("type") == "min_cart":
            threshold_val = cond.get("thresholdValue")
            if threshold_val is not None and result["min_order"] is None:
                result["min_order"] = int(threshold_val)
                found = True
        elif cond.get("isFreeDeliveryStep") and cond.get("type") == "delivery":
            threshold_val = cond.get("thresholdValue")
            if threshold_val is not None and result["free_from"] is None:
                try:
                    result["free_from"] = int(threshold_val)
                except (ValueError, TypeError):
                    pass

    return found


def _parse_cart_response(data: dict, result: dict) -> bool:
    """
    Разбирает ответ корзины для извлечения стоимости упаковки и сборки.
    Структура: cart.services[] → type: DELIVERY/PACKAGING/ASSEMBLY/PICKING
    """
    found = False
    cart = data.get("cart", data)

    services = cart.get("services", [])
    for svc in services:
        svc_type = svc.get("type", "").upper()
        price = svc.get("totalPrice") or svc.get("price") or svc.get("cost") or 0
        try:
            price = int(float(price))
        except (ValueError, TypeError):
            price = 0

        if "DELIVERY" in svc_type:
            result["delivery_price"] = price
            found = True
            # Ищем порог бесплатной доставки в условиях
            for cond in svc.get("conditions", []):
                label = cond.get("label", "").lower()
                amount_label = cond.get("amountLabel", "")
                if "бесплатн" in label or "free" in label:
                    nums = re.findall(r'\d+', amount_label.replace(" ", "").replace("\u202f", ""))
                    if nums:
                        result["free_from"] = int(nums[0])

        elif any(kw in svc_type for kw in ["PACK", "BAG", "PACKAGING"]):
            result["packaging_price"] = price
            found = True

        elif any(kw in svc_type for kw in ["ASSEMBLY", "PICKING", "PICK", "COLLECT", "SERVICE"]):
            result["assembly_price"] = price
            found = True

    # Также ищем в fees/charges
    fees = cart.get("fees", cart.get("charges", []))
    for fee in fees:
        fee_type = fee.get("type", "").upper()
        fee_name = fee.get("name", "").lower()
        price = fee.get("amount") or fee.get("price") or fee.get("value") or 0
        try:
            price = int(float(price))
        except (ValueError, TypeError):
            price = 0

        if any(kw in fee_type or kw in fee_name for kw in ["pack", "bag", "упаков"]):
            result["packaging_price"] = price
        elif any(kw in fee_type or kw in fee_name for kw in ["assembl", "pick", "сборк", "комплект"]):
            result["assembly_price"] = price

    return found


def parse_lavka_authenticated(lat: float, lon: float) -> dict:
    """
    Парсит условия доставки Яндекс Лавки с авторизацией.

    Алгоритм:
      1. Проверяем наличие сохранённого токена
      2. GET /api/v1/providers/v2/service-info (с токеном) → delivery_price, min_order, tiers
      3. GET /api/v1/cart (с токеном) → packaging_price, assembly_price
      4. Fallback на публичный API без токена
    """
    result = _make_result()
    api_got_data = False

    # Проверяем токен
    access_token = get_valid_token()
    auth_headers = dict(_WEB_HEADERS)

    if access_token:
        auth_headers["Authorization"] = f"OAuth {access_token}"
        result["auth_used"] = True

    session = requests.Session()

    # ── Шаг 1: service-info (основные условия доставки) ──
    service_info_urls = [
        f"{_LAVKA_WEB_BASE}/api/v1/providers/v2/service-info",
        f"{_LAVKA_WEB_BASE}/api/v1/service-info",
    ]

    for depot_type in ("regular", "supermarket", "express"):
        if api_got_data:
            break
        for url in service_info_urls:
            params = {
                "position[location][0]": lon,
                "position[location][1]": lat,
                "fallbackCurrencySign": "₽",
                "depotType": depot_type,
            }
            try:
                resp = session.get(
                    url,
                    params=params,
                    headers=auth_headers,
                    timeout=12,
                    verify=False,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if _parse_service_info(data, result):
                        api_got_data = True
                        result["available"] = True
                        break
            except Exception:
                continue

    # ── Шаг 2: Мобильный API (если авторизован) ──
    if access_token and not api_got_data:
        mobile_headers = {**_APP_HEADERS, "Authorization": f"OAuth {access_token}"}
        mobile_urls = [
            f"{_LAVKA_API_BASE}/api/v1/service-info",
            f"{_LAVKA_API_BASE}/api/v2/service-info",
            f"{_LAVKA_API_BASE}/api/v1/delivery/info",
        ]
        for url in mobile_urls:
            try:
                resp = session.get(
                    url,
                    params={"lat": lat, "lon": lon, "latitude": lat, "longitude": lon},
                    headers=mobile_headers,
                    timeout=12,
                    verify=False,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if _parse_service_info(data, result):
                        api_got_data = True
                        result["available"] = True
                        break
                    # Пробуем прямой разбор
                    if _extract_fields_deep(data, result):
                        api_got_data = True
                        result["available"] = True
                        break
            except Exception:
                continue

    # ── Шаг 3: Получаем данные корзины (упаковка, сборка) ──
    if access_token and api_got_data:
        mobile_headers = {**_APP_HEADERS, "Authorization": f"OAuth {access_token}"}
        cart_urls = [
            f"{_LAVKA_WEB_BASE}/api/v1/cart",
            f"{_LAVKA_API_BASE}/api/v1/cart",
            f"{_LAVKA_API_BASE}/api/v2/cart",
        ]
        for url in cart_urls:
            try:
                resp = session.get(
                    url,
                    params={"lat": lat, "lon": lon},
                    headers=mobile_headers,
                    timeout=10,
                    verify=False,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    _parse_cart_response(data, result)
                    break
            except Exception:
                continue

    # ── Шаг 4: Запрос checkout/estimate для точных данных ──
    if access_token and api_got_data:
        mobile_headers = {**_APP_HEADERS, "Authorization": f"OAuth {access_token}"}
        estimate_urls = [
            f"{_LAVKA_API_BASE}/api/v1/checkout/estimate",
            f"{_LAVKA_WEB_BASE}/api/v1/checkout/estimate",
            f"{_LAVKA_API_BASE}/api/v1/order/estimate",
        ]
        for url in estimate_urls:
            try:
                payload = {
                    "lat": lat,
                    "lon": lon,
                    "cart": {"items": []},
                }
                resp = session.post(
                    url,
                    json=payload,
                    headers={**mobile_headers, "Content-Type": "application/json"},
                    timeout=10,
                    verify=False,
                )
                if resp.status_code in (200, 400):  # 400 может содержать структуру с ценами
                    data = resp.json()
                    _extract_fields_deep(data, result)
                    break
            except Exception:
                continue

    # ── Финализация ──
    # Устанавливаем дефолты для упаковки и сборки Лавки (обычно 0)
    if result["packaging_price"] is None:
        result["packaging_price"] = 0
    if result["assembly_price"] is None:
        result["assembly_price"] = 0
    if result["delivery_time"] is None:
        result["delivery_time"] = "15-30 мин"

    # Вычисляем тарифную сетку если не заполнена
    if not result["delivery_tiers"] and api_got_data:
        result["delivery_tiers"] = _compute_tiers(
            result["delivery_price"],
            result["min_order"],
            result["free_from"],
        )

    if api_got_data:
        result["available"] = True
        if result["auth_used"]:
            result["note"] = "Данные из API (авторизован)"
            result["data_source"] = "api"
        else:
            result["note"] = "Данные из API сервиса"
            result["data_source"] = "api"
    else:
        # Публичные данные
        result["available"] = True
        result["delivery_price"] = result["delivery_price"] or 0
        result["min_order"] = result["min_order"] or 0
        result["free_from"] = result["free_from"]
        result["delivery_tiers"] = _compute_tiers(
            result["delivery_price"],
            result["min_order"],
            result["free_from"],
        )
        if result["auth_used"]:
            result["note"] = "Публичные данные (API вернул пустой ответ)"
        else:
            result["note"] = "Данные из публичных источников (требуется авторизация)"
        result["data_source"] = "public"

    return result


def _extract_fields_deep(data, result: dict, depth: int = 0) -> bool:
    """Рекурсивно ищет поля доставки, упаковки, сборки в JSON."""
    if depth > 6:
        return False
    found = False

    if isinstance(data, dict):
        # Прямые поля
        for key, field in [
            ("deliveryCost", "delivery_price"), ("delivery_cost", "delivery_price"),
            ("deliveryPrice", "delivery_price"), ("delivery_price", "delivery_price"),
            ("deliveryFee", "delivery_price"), ("delivery_fee", "delivery_price"),
            ("minimalCartPrice", "min_order"), ("min_order", "min_order"),
            ("minOrderAmount", "min_order"), ("min_order_amount", "min_order"),
            ("freeDeliveryThreshold", "free_from"), ("free_delivery_from", "free_from"),
            ("freeFrom", "free_from"), ("free_from", "free_from"),
            # Упаковка
            ("bagPrice", "packaging_price"), ("bag_price", "packaging_price"),
            ("packagingPrice", "packaging_price"), ("packaging_price", "packaging_price"),
            ("packagingFee", "packaging_price"), ("packaging_fee", "packaging_price"),
            ("packPrice", "packaging_price"), ("pack_price", "packaging_price"),
            # Сборка
            ("pickingFee", "assembly_price"), ("picking_fee", "assembly_price"),
            ("assemblyPrice", "assembly_price"), ("assembly_price", "assembly_price"),
            ("assemblyFee", "assembly_price"), ("assembly_fee", "assembly_price"),
            ("serviceFee", "assembly_price"), ("service_fee", "assembly_price"),
            ("collectionFee", "assembly_price"), ("collection_fee", "assembly_price"),
        ]:
            val = data.get(key)
            if val is not None and isinstance(val, (int, float)):
                if result[field] is None:
                    result[field] = int(val)
                    if field in ("delivery_price", "min_order"):
                        found = True

        # Время доставки
        for key in ["deliveryTime", "delivery_time", "sla", "eta", "time",
                    "timeMinutes", "deliveryTimeMinutes", "estimatedTime"]:
            val = data.get(key)
            if val is not None and result["delivery_time"] is None:
                if isinstance(val, (int, float)):
                    result["delivery_time"] = f"{int(val)} мин"
                else:
                    result["delivery_time"] = str(val)
                break

        # Рекурсия в подструктуры
        for key in ["delivery", "deliveryInfo", "deliveryConditions", "conditions",
                    "tariff", "tariffs", "data", "result", "info", "shipping",
                    "pricingConditions", "pricing", "fees", "charges", "services",
                    "cart", "order", "checkout", "estimate"]:
            sub = data.get(key)
            if sub and isinstance(sub, (dict, list)):
                if _extract_fields_deep(sub, result, depth + 1):
                    found = True

    elif isinstance(data, list):
        for item in data[:10]:
            if _extract_fields_deep(item, result, depth + 1):
                found = True

    return found


def parse_lavka_public(lat: float, lon: float) -> dict:
    """
    Парсит условия доставки Яндекс Лавки через публичный API (без авторизации).
    Используется как fallback.
    """
    result = _make_result()
    api_got_data = False
    session = requests.Session()

    for depot_type in ("regular", "supermarket"):
        params = {
            "position[location][0]": lon,
            "position[location][1]": lat,
            "fallbackCurrencySign": "₽",
            "depotType": depot_type,
        }
        try:
            resp = session.get(
                f"{_LAVKA_WEB_BASE}/api/v1/providers/v2/service-info",
                params=params,
                headers=_WEB_HEADERS,
                timeout=12,
                verify=False,
            )
            if resp.status_code == 200:
                data = resp.json()
                if _parse_service_info(data, result):
                    api_got_data = True
                    result["available"] = True
                    break
        except Exception:
            continue

    result["packaging_price"] = result["packaging_price"] or 0
    result["assembly_price"] = result["assembly_price"] or 0
    if result["delivery_time"] is None:
        result["delivery_time"] = "15-35 мин"

    if not result["delivery_tiers"] and api_got_data:
        result["delivery_tiers"] = _compute_tiers(
            result["delivery_price"],
            result["min_order"],
            result["free_from"],
        )

    if api_got_data:
        # Если доставка бесплатна (акция), показываем полную стоимость в note
        full_cost = result.pop("_full_delivery_cost", None)
        if result["delivery_price"] == 0 and full_cost and full_cost > 0:
            result["note"] = f"Данные из API сервиса (полная стоимость доставки: {full_cost} ₽)"
        else:
            result["note"] = "Данные из API сервиса"
        result["data_source"] = "api"
    else:
        result.pop("_full_delivery_cost", None)
        result["available"] = True
        result["delivery_price"] = 0
        result["min_order"] = 0
        result["delivery_tiers"] = _compute_tiers(0, 0, None)
        result["note"] = "Данные из публичных источников (API недоступен)"
        result["data_source"] = "public"

    return result


def parse_lavka(lat: float, lon: float) -> dict:
    """
    Главная функция парсинга Яндекс Лавки.
    Автоматически выбирает авторизованный или публичный режим.
    """
    token = get_valid_token()
    if token:
        return parse_lavka_authenticated(lat, lon)
    else:
        return parse_lavka_public(lat, lon)


if __name__ == "__main__":
    import sys

    lat = float(sys.argv[1]) if len(sys.argv) > 1 else 55.7701
    lon = float(sys.argv[2]) if len(sys.argv) > 2 else 37.5950

    token_info = get_token_info()
    if token_info["authorized"]:
        print(f"✅ Авторизован: {token_info['phone']} (осталось {token_info['expires_in_days']} дней)")
    else:
        print("⚠️  Не авторизован — используем публичный API")

    print(f"\nПарсим Яндекс Лавку для координат: {lat}, {lon}")
    result = parse_lavka(lat, lon)

    print(f"\n🔵 Яндекс Лавка:")
    print(f"  Доступна:    {result['available']}")
    print(f"  Доставка:    {result['delivery_price']} ₽")
    print(f"  Мин. корзина:{result['min_order']} ₽")
    print(f"  Бесплатно от:{result['free_from']} ₽")
    print(f"  Время:       {result['delivery_time']}")
    print(f"  Упаковка:    {result['packaging_price']} ₽")
    print(f"  Сборка:      {result['assembly_price']} ₽")
    print(f"  Источник:    {result['data_source']} | {result['note']}")
    print(f"  Авторизован: {result['auth_used']}")
    if result["delivery_tiers"]:
        print(f"  Тарифная сетка:")
        for tier in result["delivery_tiers"]:
            if tier["available"]:
                price_str = f"{tier['delivery_price']} ₽" if tier["delivery_price"] else "0 ₽ (бесплатно)"
                print(f"    {tier['cart_sum']} ₽ → {price_str}")
            else:
                print(f"    {tier['cart_sum']} ₽ → ниже минимума")
