"""
Парсер условий доставки.

Сервисы:
  - Самокат      — прямой API (api-web.samokat.ru), требует DeviceId
  - Яндекс Лавка — прямой API (lavka.yandex.ru), через lavka_parser.py
  - Магнит       — прямой API (magnit.ru/webgate), проверка доступности доставки
  - Ozon Fresh   — публичные данные (WAF блокирует все запросы)
  - Пятёрочка    — прямой API (5d.5ka.ru) для проверки доступности
  - ВкусВилл     — публичные данные (API закрыт)
"""

import asyncio
import json
import re
import os
import uuid
import requests
import urllib3
from typing import Optional
from playwright.async_api import async_playwright, Page

from lavka_parser import parse_lavka

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_SAMOKAT_BASE = "https://api-web.samokat.ru"
_SAMOKAT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Origin": "https://samokat.ru",
    "Referer": "https://samokat.ru/",
    "X-Application-Platform": "web",
    "Content-Type": "application/json",
}

_MAGNIT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Origin": "https://magnit.ru",
    "Referer": "https://magnit.ru/dostavka",
    "Content-Type": "application/json",
}

# Путь к браузеру (ARM Mac / Linux / Windows)
_BROWSER_PATHS = [
    os.path.expanduser(
        "~/Library/Caches/ms-playwright/chromium_headless_shell-1217/"
        "chrome-headless-shell-mac-arm64/chrome-headless-shell"
    ),
    os.path.expanduser(
        "~/Library/Caches/ms-playwright/chromium-1217/"
        "chrome-mac/Chromium.app/Contents/MacOS/Chromium"
    ),
    os.path.expanduser(
        "~/.cache/ms-playwright/chromium_headless_shell-1217/"
        "chrome-headless-shell-linux/chrome-headless-shell"
    ),
]

def _find_browser() -> Optional[str]:
    for p in _BROWSER_PATHS:
        if os.path.exists(p):
            return p
    return None


# ─────────────────────────────────────────────
# ПУБЛИЧНЫЕ ДАННЫЕ (fallback, актуальны на 2025 год)
# ─────────────────────────────────────────────

PUBLIC_DATA = {
    "Самокат": {
        "delivery_price": 0,
        "min_order": 500,
        "free_from": None,
        "delivery_time": "15-30 мин",
        "packaging_price": 0,
        "assembly_price": 0,
    },
    "Ozon Fresh": {
        "delivery_price": 149,
        "min_order": 500,
        "free_from": 1999,
        "delivery_time": "15-30 мин",
        "packaging_price": 0,
        "assembly_price": 0,
    },
    "Пятёрочка": {
        # Стоимость доставки зависит от зоны магазина (0–99 ₽), определяется динамически
        "delivery_price": None,
        "min_order": 600,
        "free_from": None,       # зависит от зоны, не публикуется явно
        "delivery_time": "30-60 мин",
        "packaging_price": 39,   # сбор заказа — 39 ₽ (по данным 5ka.ru)
        "assembly_price": 39,    # упаковка — 39 ₽ (по данным 5ka.ru)
    },
    "Магнит": {
        "delivery_price": 0,
        "min_order": 500,
        "free_from": 1200,
        "delivery_time": "30-60 мин",
        "packaging_price": 29,   # упаковка — 29 ₽ (по данным magnit-dostavka.ru)
        "assembly_price": 0,
    },
    "ВкусВилл": {
        # Экспресс-доставка: 149 ₽, от 15 минут (единственная платная доставка)
        # Источник: vkusvill.ru/dostavka/ — "Это единственная наша платная доставка — за 149 ₽"
        "delivery_price": 149,
        "min_order": 500,
        "free_from": None,
        "delivery_time": "15-60 мин",
        "packaging_price": 0,
        "assembly_price": 0,
    },
    "Яндекс Лавка": {
        "delivery_price": 0,
        "min_order": 0,
        "free_from": None,
        "delivery_time": "15-30 мин",
        "packaging_price": 0,
        "assembly_price": 0,
    },
}

# Суммы корзины для тарифной сетки (₽)
TIER_CART_SUMS = [300, 400, 500, 600]


def _make_result(service: str, logo: str) -> dict:
    return {
        "service": service,
        "logo": logo,
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
    }


def _compute_tiers(delivery_price, min_order, free_from,
                   cart_sums=None) -> list:
    """
    Вычисляет тарифную сетку доставки для заданных сумм корзины.
    """
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


def _apply_fallback(result: dict, service: str, api_got_data: bool) -> dict:
    pub = PUBLIC_DATA.get(service, {})
    result["available"] = True
    for field in ["delivery_price", "min_order", "free_from", "delivery_time",
                  "packaging_price", "assembly_price"]:
        if result[field] is None:
            result[field] = pub.get(field)
    if not api_got_data:
        result["note"] = "Данные из публичных источников (API недоступен)"
        result["data_source"] = "public"
    else:
        result["note"] = "Данные из API сервиса"
        result["data_source"] = "api"
    if not result.get("delivery_tiers"):
        result["delivery_tiers"] = _compute_tiers(
            result["delivery_price"],
            result["min_order"],
            result["free_from"],
        )
    return result


# ─────────────────────────────────────────────
# ЯНДЕКС ЛАВКА (через lavka_parser.py)
# ─────────────────────────────────────────────

def parse_yandex_lavka(lat: float, lon: float) -> dict:
    """Парсит условия доставки Яндекс Лавки через lavka_parser."""
    return parse_lavka(lat, lon)


# ─────────────────────────────────────────────
# САМОКАТ (прямой API)
# ─────────────────────────────────────────────

def parse_samokat(lat: float, lon: float) -> dict:
    """
    Парсит условия доставки Самоката через открытый API api-web.samokat.ru.
    Алгоритм:
      1. POST /oauth/token/anonymous  → JWT-токен (требует DeviceId в заголовке)
      2. GET  /showcases/list         → ближайший магазин + sla (время)
      3. GET  /showcases/{id}/settings → minimalAmount (мин. заказ в копейках)
      4. POST /search/products + PUT /v2/carts/{id} → стоимость доставки
    """
    result = _make_result("Самокат", "🟡")
    api_got_data = False
    PRICE_DIV = 100  # цены в копейках

    session = requests.Session()
    # DeviceId обязателен — без него API возвращает 400 "device id not found"
    device_id = uuid.uuid4().hex[:20]
    headers = {**_SAMOKAT_HEADERS, "DeviceId": device_id}

    # 1. Анонимный токен
    token = None
    try:
        resp = session.post(
            f"{_SAMOKAT_BASE}/oauth/token/anonymous",
            headers=headers,
            data="",
            verify=False,
            timeout=10,
        )
        if resp.status_code == 200:
            token = resp.json().get("access_token")
    except Exception:
        pass

    if not token:
        return _apply_fallback(result, "Самокат", False)

    auth_headers = {**headers, "Authorization": f"Bearer {token}"}

    # 2. Список магазинов → ближайший + время доставки
    showcase_id = None
    try:
        resp = session.get(
            f"{_SAMOKAT_BASE}/showcases/list",
            params={"lat": lat, "lon": lon},
            headers=auth_headers,
            verify=False,
            timeout=10,
        )
        if resp.status_code == 200:
            showcases = resp.json()
            if isinstance(showcases, list) and showcases:
                sc = showcases[0]
                showcase_id = sc.get("showcaseId")
                sla = sc.get("sla")
                if sla:
                    result["delivery_time"] = f"{sla} мин"
                result["available"] = True
                api_got_data = True
    except Exception:
        pass

    if not showcase_id:
        return _apply_fallback(result, "Самокат", False)

    # 3. Настройки магазина → мин. заказ, уточнённое время
    try:
        resp = session.get(
            f"{_SAMOKAT_BASE}/showcases/{showcase_id}/settings",
            params={"lat": lat, "lon": lon},
            headers=auth_headers,
            verify=False,
            timeout=10,
        )
        if resp.status_code == 200:
            settings = resp.json()
            order_settings = settings.get("orderSettings", {})
            min_amount = order_settings.get("minimalAmount")
            if min_amount is not None:
                result["min_order"] = int(min_amount / PRICE_DIV)
            geo_sla = settings.get("geoSla")
            if geo_sla:
                result["delivery_time"] = f"{geo_sla} мин"
    except Exception:
        pass

    # 4. Стоимость доставки — добавляем товар в корзину
    try:
        product_id = None
        resp = session.post(
            f"{_SAMOKAT_BASE}/search/products",
            json={"query": "молоко", "showcaseIds": [showcase_id]},
            headers=auth_headers,
            verify=False,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            results_list = data.get("result", data if isinstance(data, list) else [])
            for sc_result in results_list:
                for item in sc_result.get("items", []):
                    if item.get("type") == "PRODUCT":
                        product_id = item.get("id")
                        break
                if product_id:
                    break

        if product_id:
            resp = session.post(
                f"{_SAMOKAT_BASE}/v2/carts/get",
                json={
                    "showcaseId": showcase_id,
                    "deliveryOption": "DELIVERY",
                    "embedProductsToResponse": True,
                },
                headers=auth_headers,
                verify=False,
                timeout=10,
            )
            if resp.status_code == 200:
                cart_data = resp.json()
                cart = cart_data.get("cart", {})
                cart_id = cart.get("cartId")
                version = cart.get("version", 0)

                resp2 = session.put(
                    f"{_SAMOKAT_BASE}/v2/carts/{cart_id}",
                    json={
                        "showcaseId": showcase_id,
                        "version": version,
                        "deliveryOption": "DELIVERY",
                        "embedProductsToResponse": True,
                        "products": [{"productId": product_id, "quantity": 1}],
                    },
                    headers=auth_headers,
                    verify=False,
                    timeout=10,
                )
                if resp2.status_code == 200:
                    updated = resp2.json()
                    services = updated.get("cart", {}).get("services", [])
                    delivery_price = None
                    for svc in services:
                        svc_type = svc.get("type", "").upper()
                        price = svc.get("totalPrice")

                        if "DELIVERY" in svc_type:
                            if price is not None:
                                delivery_price = int(price / PRICE_DIV)
                            for cond in svc.get("conditions", []):
                                label = cond.get("label", "")
                                amount_label = cond.get("amountLabel", "")
                                if "бесплатн" in label.lower() or "free" in label.lower():
                                    nums = re.findall(r'\d+', amount_label.replace(" ", ""))
                                    if nums:
                                        result["free_from"] = int(nums[0])

                        elif "PACKING" in svc_type or "PACK" in svc_type or "BAG" in svc_type:
                            # Упаковка заказа (тип PACKING, цена в копейках)
                            if price is not None:
                                result["packaging_price"] = int(price / PRICE_DIV)

                        elif any(kw in svc_type for kw in ["ASSEMBLY", "PICKING", "COLLECT", "SERVICE"]):
                            if price is not None:
                                result["assembly_price"] = int(price / PRICE_DIV)

                    if delivery_price is None and services is not None:
                        delivery_price = 0
                    result["delivery_price"] = delivery_price
    except Exception:
        pass

    # Если упаковка/сборка не найдены в корзине — ставим 0
    # (в некоторых городах/магазинах упаковка не взимается отдельно)
    if result["packaging_price"] is None:
        result["packaging_price"] = 0
    if result["assembly_price"] is None:
        result["assembly_price"] = 0

    return _apply_fallback(result, "Самокат", api_got_data)


# ─────────────────────────────────────────────
# OZON FRESH (публичные данные — WAF блокирует все запросы)
# ─────────────────────────────────────────────

def parse_ozon_fresh(lat: float, lon: float) -> dict:
    """
    Ozon Fresh полностью блокирует прямые запросы (403 WAF) и headless браузеры.
    Возвращаем публичные данные с актуальными значениями для СПб/Москвы.
    min_order=500 ₽ (реальное значение для СПб, не 699 ₽ как было раньше).
    """
    result = _make_result("Ozon Fresh", "🟠")
    pub = PUBLIC_DATA["Ozon Fresh"]
    result["available"] = True
    result["delivery_price"] = pub["delivery_price"]
    result["min_order"] = pub["min_order"]
    result["free_from"] = pub["free_from"]
    result["delivery_time"] = pub["delivery_time"]
    result["packaging_price"] = pub["packaging_price"]
    result["assembly_price"] = pub["assembly_price"]
    result["note"] = "Данные из публичных источников (API защищён WAF)"
    result["data_source"] = "public"
    result["delivery_tiers"] = _compute_tiers(
        result["delivery_price"],
        result["min_order"],
        result["free_from"],
    )
    return result


# ─────────────────────────────────────────────
# ПЯТЁРОЧКА
# ─────────────────────────────────────────────

def parse_pyaterochka(lat: float, lon: float) -> dict:
    """
    Парсит условия доставки Пятёрочки через API 5d.5ka.ru.
    Проверяет доступность доставки по координатам.
    Тарифы доставки недоступны без авторизации — показываем "0–99 ₽*".
    """
    result = _make_result("Пятёрочка", "🔴")
    api_got_data = False

    stores_url = f"https://5d.5ka.ru/api/orders/v1/orders/stores/?lon={lon}&lat={lat}"
    stores_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        ),
        "Accept": "application/json",
        "Origin": "https://5ka.ru",
        "Referer": "https://5ka.ru/",
    }
    try:
        resp = requests.get(stores_url, headers=stores_headers, timeout=8)
        if resp.status_code == 200:
            stores_data = resp.json()
            has_delivery = stores_data.get("has_delivery", True)
            api_got_data = True
            pub = PUBLIC_DATA["Пятёрочка"]
            result["available"] = has_delivery
            result["delivery_price"] = None   # зависит от зоны
            result["min_order"] = pub["min_order"]
            result["free_from"] = pub["free_from"]
            result["delivery_time"] = pub["delivery_time"]
            result["packaging_price"] = pub["packaging_price"]
            result["assembly_price"] = pub["assembly_price"]
            shop_addr = stores_data.get("shop_address", "н/д")
            result["note"] = (
                f"Доставка {'доступна' if has_delivery else 'недоступна'} "
                f"(магазин: {shop_addr}). "
                f"Стоимость доставки зависит от зоны — уточните на сайте."
            )
            result["data_source"] = "api"
            result["delivery_tiers"] = []
            return result
    except Exception:
        pass

    return _apply_fallback(result, "Пятёрочка", False)


# ─────────────────────────────────────────────
# МАГНИТ (прямой API webgate)
# ─────────────────────────────────────────────

def parse_magnit(lat: float, lon: float) -> dict:
    """
    Парсит условия доставки Магнита через API magnit.ru/webgate.
    Алгоритм:
      1. POST /webgate/v1/stores-facade/search → ближайшие магазины с доставкой
      2. Если магазины найдены — доставка доступна
      3. Тарифы доставки недоступны без авторизации — используем публичные данные
    """
    result = _make_result("Магнит", "🟢")
    api_got_data = False

    delta = 0.05  # ~5 км радиус поиска

    body = {
        "filters": {
            "geo": {
                "typeName": "box",
                "leftTopPoint": {"latitude": lat + delta, "longitude": lon - delta},
                "rightBottomPoint": {"latitude": lat - delta, "longitude": lon + delta},
            },
            "deliveryTypeList": ["DELIVERY_TYPE_DELIVERY"],
            "storeTypeListV2": ["MM", "GM", "DARKSTORE", "MO", "ME", "MT", "MC"],
        }
    }

    try:
        resp = requests.post(
            "https://magnit.ru/webgate/v1/stores-facade/search",
            json=body,
            headers=_MAGNIT_HEADERS,
            timeout=10,
            verify=False,
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", {}).get("items", [])
            if items:
                # Нашли магазины с доставкой
                api_got_data = True
                result["available"] = True
                pub = PUBLIC_DATA["Магнит"]
                result["delivery_price"] = pub["delivery_price"]
                result["min_order"] = pub["min_order"]
                result["free_from"] = pub["free_from"]
                result["delivery_time"] = pub["delivery_time"]
                result["packaging_price"] = pub["packaging_price"]
                result["assembly_price"] = pub["assembly_price"]
                # Берём адрес ближайшего магазина из детального запроса
                nearest = items[0]
                store_code = nearest.get("externalId", {}).get("storeCode", "")
                note_parts = [f"Доставка доступна"]
                if store_code:
                    # Получаем адрес магазина
                    try:
                        r2 = requests.post(
                            "https://magnit.ru/webgate/v1/stores-facade/store",
                            json={"externalId": {"owner": "OWNER_MAGNIT", "storeCode": store_code}},
                            headers=_MAGNIT_HEADERS,
                            timeout=6,
                            verify=False,
                        )
                        if r2.status_code == 200:
                            store_data = r2.json()
                            addr = store_data.get("address", "")
                            if addr:
                                note_parts.append(f"магазин: {addr}")
                    except Exception:
                        pass
                result["note"] = (
                    f"{', '.join(note_parts)}. "
                    f"Тарифы доставки из публичных источников."
                )
                result["data_source"] = "api"
                result["delivery_tiers"] = _compute_tiers(
                    result["delivery_price"],
                    result["min_order"],
                    result["free_from"],
                )
                return result
            else:
                # Магазинов с доставкой нет в радиусе
                result["available"] = False
                result["note"] = "Доставка недоступна в данном районе"
                result["data_source"] = "api"
                result["delivery_tiers"] = []
                return result
    except Exception:
        pass

    return _apply_fallback(result, "Магнит", False)


# ─────────────────────────────────────────────
# ВКУСВИЛЛ (экспресс-доставка)
# ─────────────────────────────────────────────

_VKUSVILL_BASE = "https://vkusvill.ru"

_VKUSVILL_HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

_VKUSVILL_HEADERS_AJAX = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": _VKUSVILL_BASE,
    "Referer": f"{_VKUSVILL_BASE}/dostavka/",
}


def parse_vkusvill(lat: float, lon: float) -> dict:
    """
    Парсит условия экспресс-доставки ВкусВилла.

    Алгоритм:
      1. GET /dostavka/ → получаем сессию (cookies)
      2. POST /ajax/address_way/get_Coords.php → проверяем доступность доставки
         по координатам (DELIVERY_AVAILABLE, TYPE)
      3. Возвращаем данные экспресс-доставки:
         - Стоимость: 149 ₽ (единственная платная доставка)
         - Время: от 15 минут
         - Источник: vkusvill.ru/dostavka/

    Примечание: ВкусВилл закрыл мобильный API (mobile.vkusvill.ru — 404 "Нет данных"),
    но /ajax/address_way/get_Coords.php работает без авторизации.
    """
    result = _make_result("ВкусВилл", "🟣")
    api_got_data = False

    session = requests.Session()

    try:
        # 1. Инициализируем сессию (получаем cookies)
        r = session.get(
            f"{_VKUSVILL_BASE}/dostavka/",
            headers=_VKUSVILL_HEADERS_BROWSER,
            timeout=12,
            verify=False,
        )

        # 2. Проверяем доступность доставки по координатам
        rp = session.post(
            f"{_VKUSVILL_BASE}/ajax/address_way/get_Coords.php",
            data={
                "set_by_coords": "Y",
                "addr": "",
                "lat": lat,
                "lon": lon,
                "isAddrFull": "Y",
                "isSavedAddr": "N",
                "curTypeService": "",
            },
            headers=_VKUSVILL_HEADERS_AJAX,
            timeout=10,
            verify=False,
        )

        if rp.status_code == 200:
            data = rp.json()
            if data.get("success") == "Y":
                addr_data = data.get("address_way_data", {})
                delivery_available = addr_data.get("DELIVERY_AVAILABLE", False)
                delivery_type = addr_data.get("TYPE", "")

                api_got_data = True
                result["available"] = bool(delivery_available)

                if result["available"]:
                    # Экспресс-доставка: 149 ₽, от 15 минут
                    # Источник: vkusvill.ru/dostavka/ — "единственная платная доставка — за 149 ₽"
                    pub = PUBLIC_DATA["ВкусВилл"]
                    result["delivery_price"] = pub["delivery_price"]
                    result["min_order"] = pub["min_order"]
                    result["free_from"] = pub["free_from"]
                    result["delivery_time"] = pub["delivery_time"]
                    result["packaging_price"] = pub["packaging_price"]
                    result["assembly_price"] = pub["assembly_price"]

                    addr = data.get("addr", "")
                    result["note"] = (
                        f"Экспресс-доставка доступна"
                        + (f" ({addr})" if addr else "")
                        + ". Тариф: 149 ₽, от 15 мин."
                    )
                    result["data_source"] = "api"
                    result["delivery_tiers"] = _compute_tiers(
                        result["delivery_price"],
                        result["min_order"],
                        result["free_from"],
                    )
                    return result
                else:
                    result["note"] = "Доставка недоступна в данном районе"
                    result["data_source"] = "api"
                    result["delivery_tiers"] = []
                    return result

    except Exception:
        pass

    # Fallback: публичные данные
    return _apply_fallback(result, "ВкусВилл", api_got_data)


# ─────────────────────────────────────────────
# ГЛАВНАЯ ФУНКЦИЯ
# ─────────────────────────────────────────────

async def parse_all_async(lat: float, lon: float) -> list:
    """
    Параллельный парсинг всех сервисов.
    Все парсеры синхронные (прямые API или публичные данные).
    """
    loop = asyncio.get_event_loop()

    # Запускаем все парсеры в thread executor (синхронные requests)
    futures = [
        loop.run_in_executor(None, parse_samokat, lat, lon),
        loop.run_in_executor(None, parse_yandex_lavka, lat, lon),
        loop.run_in_executor(None, parse_ozon_fresh, lat, lon),
        loop.run_in_executor(None, parse_pyaterochka, lat, lon),
        loop.run_in_executor(None, parse_magnit, lat, lon),
        loop.run_in_executor(None, parse_vkusvill, lat, lon),
    ]

    all_results = await asyncio.gather(*futures, return_exceptions=True)

    services = ["Самокат", "Яндекс Лавка", "Ozon Fresh", "Пятёрочка", "Магнит", "ВкусВилл"]
    logos    = ["🟡",      "🔵",            "🟠",         "🔴",         "🟢",     "🟣"]

    final = []
    for i, res in enumerate(all_results):
        if isinstance(res, Exception):
            pub = PUBLIC_DATA.get(services[i], {})
            final.append({
                "service": services[i],
                "logo": logos[i],
                "available": True,
                "delivery_price": pub.get("delivery_price"),
                "min_order": pub.get("min_order"),
                "free_from": pub.get("free_from"),
                "delivery_time": pub.get("delivery_time"),
                "packaging_price": pub.get("packaging_price"),
                "assembly_price": pub.get("assembly_price"),
                "note": f"Данные из публичных источников (ошибка: {str(res)[:80]})",
                "error": None,
                "data_source": "public",
                "delivery_tiers": _compute_tiers(
                    pub.get("delivery_price"), pub.get("min_order"), pub.get("free_from")
                ),
            })
        else:
            final.append(res)

    return final


def parse_all_sync(lat: float, lon: float) -> list:
    """Синхронная обёртка для вызова из Flask."""
    return asyncio.run(parse_all_async(lat, lon))


if __name__ == "__main__":
    import sys
    lat = float(sys.argv[1]) if len(sys.argv) > 1 else 59.9386
    lon = float(sys.argv[2]) if len(sys.argv) > 2 else 30.3141
    print(f"Парсим для координат: {lat}, {lon}")
    results = parse_all_sync(lat, lon)
    for r in results:
        print(f"\n{r['logo']} {r['service']}:")
        print(f"  Доступен:    {r['available']}")
        print(f"  Доставка:    {r['delivery_price']} ₽")
        print(f"  Мин. корзина:{r['min_order']} ₽")
        print(f"  Бесплатно от:{r['free_from']} ₽")
        print(f"  Время:       {r['delivery_time']}")
        print(f"  Упаковка:    {r['packaging_price']} ₽")
        print(f"  Сборка:      {r['assembly_price']} ₽")
        print(f"  Источник:    {r['data_source']} | {r['note']}")
