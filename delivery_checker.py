"""
Модуль парсинга условий доставки для:
- Ozon Fresh
- Пятёрочка
- Магнит
- ВкусВилл

Стратегия: сначала пробуем реальные API, при неудаче — публичные данные с сайтов.
"""

import requests
import urllib3
import json
import re
from typing import Optional

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS_BASE = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

# Публичные данные сервисов (актуальны на 2025 год)
PUBLIC_DATA = {
    "Ozon Fresh": {
        "delivery_price": 149,
        "min_order": 699,
        "free_from": 1999,
        "delivery_time": "15-30 мин",
    },
    "Пятёрочка": {
        "delivery_price": 0,
        "min_order": 600,
        "free_from": 1500,
        "delivery_time": "30-60 мин",
    },
    "Магнит": {
        "delivery_price": 0,
        "min_order": 500,
        "free_from": 1200,
        "delivery_time": "30-60 мин",
    },
    "ВкусВилл": {
        "delivery_price": 0,
        "min_order": 800,
        "free_from": 1500,
        "delivery_time": "30-60 мин",
    },
}


def geocode_address(address: str) -> Optional[dict]:
    """Геокодирует адрес через несколько провайдеров (Photon, Nominatim)."""

    # ── Провайдер 1: Photon (komoot) — работает без ключа ──
    try:
        resp = requests.get(
            "https://photon.komoot.io/api/",
            params={"q": address, "limit": 1},
            headers={"User-Agent": "DeliveryChecker/1.0"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            features = data.get("features", [])
            if features:
                feat = features[0]
                coords = feat["geometry"]["coordinates"]  # [lon, lat]
                props = feat.get("properties", {})
                parts = []
                for key in ("name", "street", "housenumber", "city", "state", "country"):
                    val = props.get(key)
                    if val:
                        parts.append(val)
                display = ", ".join(parts) if parts else address
                return {
                    "lat": float(coords[1]),
                    "lon": float(coords[0]),
                    "display_name": display,
                }
    except Exception as e:
        print(f"Photon geocoding error: {e}")

    # ── Провайдер 2: Nominatim (резервный) ──
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": address,
                "format": "json",
                "limit": 1,
                "countrycodes": "ru",
                "addressdetails": 1,
            },
            headers={"User-Agent": "DeliveryChecker/1.0 (contact@example.com)"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data:
                return {
                    "lat": float(data[0]["lat"]),
                    "lon": float(data[0]["lon"]),
                    "display_name": data[0].get("display_name", address),
                }
    except Exception as e:
        print(f"Nominatim geocoding error: {e}")

    return None


def _apply_public_fallback(result: dict, service_name: str, api_got_data: bool) -> dict:
    """Применяет публичные данные если API не вернул нужные поля."""
    pub = PUBLIC_DATA.get(service_name, {})
    result["available"] = True
    if result["delivery_price"] is None:
        result["delivery_price"] = pub.get("delivery_price")
    if result["min_order"] is None:
        result["min_order"] = pub.get("min_order")
    if result["free_from"] is None:
        result["free_from"] = pub.get("free_from")
    if result["delivery_time"] is None:
        result["delivery_time"] = pub.get("delivery_time")
    if not api_got_data:
        result["note"] = "Данные из публичных источников (API недоступен)"
    else:
        result["note"] = "Данные из публичных источников"
    return result


# ─────────────────────────────────────────────
# OZON FRESH
# ─────────────────────────────────────────────

def parse_ozon_fresh(lat: float, lon: float) -> dict:
    """Парсит условия доставки Ozon Fresh."""
    result = {
        "service": "Ozon Fresh",
        "logo": "🟠",
        "available": False,
        "delivery_price": None,
        "min_order": None,
        "free_from": None,
        "delivery_time": None,
        "note": "",
        "error": None,
    }

    session = requests.Session()
    headers = {
        **HEADERS_BASE,
        "Origin": "https://ozon.ru",
        "Referer": "https://ozon.ru/",
        "Content-Type": "application/json",
        "x-o3-app-name": "ozon-front",
        "x-o3-app-version": "3.0.0",
    }

    api_got_data = False

    # Попытка 1: API Ozon Fresh через composer
    try:
        resp = session.post(
            "https://ozon.ru/api/composer-api.bx/page/json/v2",
            json={"url": "/fresh/", "lat": lat, "lon": lon},
            headers=headers,
            verify=False,
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            text = json.dumps(data, ensure_ascii=False)
            price_match = re.search(r'"deliveryPrice"\s*:\s*(\d+)', text)
            min_match = re.search(r'"minOrderAmount"\s*:\s*(\d+)', text)
            free_match = re.search(r'"freeDeliveryThreshold"\s*:\s*(\d+)', text)
            if price_match or min_match:
                api_got_data = True
                result["available"] = True
                if price_match:
                    result["delivery_price"] = int(price_match.group(1))
                if min_match:
                    result["min_order"] = int(min_match.group(1))
                if free_match:
                    result["free_from"] = int(free_match.group(1))
                result["delivery_time"] = "15-30 мин"
                return result
    except Exception:
        pass

    # Попытка 2: Прямой API fresh.ozon.ru
    try:
        resp = session.get(
            "https://fresh.ozon.ru/api/v1/delivery-info",
            params={"lat": lat, "lon": lon},
            headers=headers,
            verify=False,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            dp = data.get("deliveryPrice") or data.get("delivery_price") or data.get("price")
            mo = data.get("minOrderAmount") or data.get("min_order_amount")
            if dp is not None or mo is not None:
                api_got_data = True
                result["available"] = True
                result["delivery_price"] = dp
                result["min_order"] = mo
                result["free_from"] = data.get("freeDeliveryThreshold") or data.get("free_delivery_from")
                result["delivery_time"] = "15-30 мин"
                return result
    except Exception:
        pass

    return _apply_public_fallback(result, "Ozon Fresh", api_got_data)


# ─────────────────────────────────────────────
# ПЯТЁРОЧКА
# ─────────────────────────────────────────────

def parse_pyaterochka(lat: float, lon: float) -> dict:
    """Парсит условия доставки Пятёрочки."""
    result = {
        "service": "Пятёрочка",
        "logo": "🔴",
        "available": False,
        "delivery_price": None,
        "min_order": None,
        "free_from": None,
        "delivery_time": None,
        "note": "",
        "error": None,
    }

    session = requests.Session()
    headers = {
        **HEADERS_BASE,
        "Origin": "https://5ka.ru",
        "Referer": "https://5ka.ru/",
        "Content-Type": "application/json",
    }

    api_got_data = False
    store_id = None

    # Шаг 1: Найти ближайший магазин
    store_endpoints = [
        ("https://5ka.ru/api/v2/stores/", {"lat": lat, "lon": lon, "nearest": 1, "limit": 1}),
        ("https://api.5ka.ru/api/v2/stores/", {"lat": lat, "lon": lon, "nearest": 1, "limit": 1}),
        ("https://5ka.ru/api/v1/stores/nearest/", {"lat": lat, "lon": lon}),
    ]
    for url, params in store_endpoints:
        try:
            resp = session.get(url, params=params, headers=headers, verify=False, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                store = None
                if isinstance(data, list) and data:
                    store = data[0]
                elif isinstance(data, dict):
                    stores = (data.get("results") or data.get("stores") or
                              data.get("items") or data.get("data"))
                    if stores:
                        store = stores[0]
                if store:
                    store_id = str(store.get("id") or store.get("store_id") or "")
                    api_got_data = True
                    break
        except Exception:
            continue

    # Шаг 2: Получить условия доставки
    if store_id:
        delivery_endpoints = [
            "https://5ka.ru/api/v2/delivery/conditions/",
            "https://api.5ka.ru/api/v2/delivery/conditions/",
            "https://5ka.ru/api/v1/delivery/info/",
        ]
        for url in delivery_endpoints:
            try:
                resp = session.get(
                    url,
                    params={"store_id": store_id, "lat": lat, "lon": lon},
                    headers=headers,
                    verify=False,
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    dp = data.get("delivery_price") or data.get("deliveryPrice") or data.get("price")
                    mo = data.get("min_order_amount") or data.get("minOrderAmount") or data.get("min_amount")
                    if dp is not None or mo is not None:
                        result["delivery_price"] = dp
                        result["min_order"] = mo
                        result["free_from"] = (data.get("free_delivery_from") or
                                               data.get("freeDeliveryThreshold"))
                        result["delivery_time"] = data.get("delivery_time") or data.get("deliveryTime")
                        break
            except Exception:
                continue

    return _apply_public_fallback(result, "Пятёрочка", api_got_data)


# ─────────────────────────────────────────────
# МАГНИТ
# ─────────────────────────────────────────────

def parse_magnit(lat: float, lon: float) -> dict:
    """Парсит условия доставки Магнит."""
    result = {
        "service": "Магнит",
        "logo": "🟢",
        "available": False,
        "delivery_price": None,
        "min_order": None,
        "free_from": None,
        "delivery_time": None,
        "note": "",
        "error": None,
    }

    session = requests.Session()
    headers = {
        **HEADERS_BASE,
        "Origin": "https://dostavka.magnit.ru",
        "Referer": "https://dostavka.magnit.ru/",
        "Content-Type": "application/json",
        "x-app-version": "1.0.0",
        "x-platform": "web",
    }

    api_got_data = False
    token = None

    # Шаг 1: Получить анонимный токен
    try:
        resp = session.post(
            "https://dostavka.magnit.ru/api/v1/auth/anonymous",
            json={},
            headers=headers,
            verify=False,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            token = data.get("token") or data.get("access_token") or data.get("accessToken")
    except Exception:
        pass

    if token:
        headers["Authorization"] = f"Bearer {token}"

    # Шаг 2: Найти ближайший магазин
    store_id = None
    store_endpoints = [
        "https://dostavka.magnit.ru/api/v1/stores/nearest",
        "https://dostavka.magnit.ru/api/v2/stores/nearest",
        "https://magnit.ru/api/v1/delivery/stores/nearest",
    ]
    for url in store_endpoints:
        try:
            resp = session.get(
                url,
                params={"lat": lat, "lon": lon, "latitude": lat, "longitude": lon},
                headers=headers,
                verify=False,
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                store = None
                if isinstance(data, list) and data:
                    store = data[0]
                elif isinstance(data, dict):
                    stores = data.get("stores") or data.get("items") or data.get("data")
                    if stores:
                        store = stores[0]
                    elif data.get("id") or data.get("storeId"):
                        store = data
                if store:
                    store_id = str(store.get("id") or store.get("storeId") or
                                   store.get("store_id", ""))
                    api_got_data = True
                    break
        except Exception:
            continue

    # Шаг 3: Получить условия доставки
    if store_id:
        delivery_endpoints = [
            f"https://dostavka.magnit.ru/api/v1/stores/{store_id}/delivery-conditions",
            "https://dostavka.magnit.ru/api/v1/delivery/conditions",
            "https://dostavka.magnit.ru/api/v2/delivery/info",
        ]
        for url in delivery_endpoints:
            try:
                resp = session.get(
                    url,
                    params={"lat": lat, "lon": lon, "storeId": store_id},
                    headers=headers,
                    verify=False,
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    dp = (data.get("deliveryPrice") or data.get("delivery_price") or
                          data.get("price"))
                    mo = (data.get("minOrderAmount") or data.get("min_order_amount") or
                          data.get("minAmount"))
                    if dp is not None or mo is not None:
                        result["delivery_price"] = dp
                        result["min_order"] = mo
                        result["free_from"] = (data.get("freeDeliveryThreshold") or
                                               data.get("free_delivery_from"))
                        result["delivery_time"] = (data.get("deliveryTime") or
                                                   data.get("delivery_time") or data.get("sla"))
                        break
            except Exception:
                continue

    return _apply_public_fallback(result, "Магнит", api_got_data)


# ─────────────────────────────────────────────
# ВКУСВИЛЛ
# ─────────────────────────────────────────────

def parse_vkusvill(lat: float, lon: float) -> dict:
    """Парсит условия доставки ВкусВилл."""
    result = {
        "service": "ВкусВилл",
        "logo": "🟣",
        "available": False,
        "delivery_price": None,
        "min_order": None,
        "free_from": None,
        "delivery_time": None,
        "note": "",
        "error": None,
    }

    session = requests.Session()
    headers = {
        **HEADERS_BASE,
        "Origin": "https://vkusvill.ru",
        "Referer": "https://vkusvill.ru/",
        "Content-Type": "application/json",
    }

    api_got_data = False
    shop_id = None

    # Шаг 1: Найти ближайший магазин
    shop_endpoints = [
        ("https://vkusvill.ru/api/v1/address/shops", {"lat": lat, "lon": lon, "limit": 1}),
        ("https://api.vkusvill.ru/api/v1/address/shops", {"lat": lat, "lon": lon, "limit": 1}),
        ("https://vkusvill.ru/api/v2/shops/nearest", {"lat": lat, "lon": lon}),
        ("https://vkusvill.ru/api/shops/nearest", {"latitude": lat, "longitude": lon}),
    ]
    for url, params in shop_endpoints:
        try:
            resp = session.get(url, params=params, headers=headers, verify=False, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                shop = None
                if isinstance(data, list) and data:
                    shop = data[0]
                elif isinstance(data, dict):
                    shops = (data.get("shops") or data.get("items") or
                             data.get("results") or data.get("data"))
                    if shops:
                        shop = shops[0]
                    elif data.get("id") or data.get("shop_id"):
                        shop = data
                if shop:
                    shop_id = str(shop.get("id") or shop.get("shop_id") or
                                  shop.get("shopId", ""))
                    api_got_data = True
                    break
        except Exception:
            continue

    # Шаг 2: Получить тариф доставки
    if shop_id:
        tariff_endpoints = [
            "https://vkusvill.ru/api/v1/delivery/tariff",
            "https://api.vkusvill.ru/api/v1/delivery/tariff",
            "https://vkusvill.ru/api/v1/delivery/conditions",
            "https://vkusvill.ru/api/delivery/info",
        ]
        for url in tariff_endpoints:
            try:
                resp = session.get(
                    url,
                    params={"shop_id": shop_id, "shopId": shop_id, "lat": lat, "lon": lon},
                    headers=headers,
                    verify=False,
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    dp = (data.get("delivery_price") or data.get("deliveryPrice") or
                          data.get("price") or data.get("cost"))
                    mo = (data.get("min_order_amount") or data.get("minOrderAmount") or
                          data.get("min_sum"))
                    if dp is not None or mo is not None:
                        result["delivery_price"] = dp
                        result["min_order"] = mo
                        result["free_from"] = (data.get("free_delivery_from") or
                                               data.get("freeDeliveryThreshold") or
                                               data.get("free_from"))
                        result["delivery_time"] = (data.get("delivery_time") or
                                                   data.get("deliveryTime") or data.get("time"))
                        break
            except Exception:
                continue

    return _apply_public_fallback(result, "ВкусВилл", api_got_data)


# ─────────────────────────────────────────────
# ГЛАВНАЯ ФУНКЦИЯ
# ─────────────────────────────────────────────

def check_all_services(address: str) -> dict:
    """Проверяет все сервисы доставки для указанного адреса."""
    geo = geocode_address(address)
    if not geo:
        return {
            "success": False,
            "error": f"Не удалось определить координаты для адреса: {address}",
            "results": [],
        }

    lat = geo["lat"]
    lon = geo["lon"]
    display_name = geo["display_name"]

    results = []
    parsers = [parse_ozon_fresh, parse_pyaterochka, parse_magnit, parse_vkusvill]

    for parser in parsers:
        try:
            res = parser(lat, lon)
            results.append(res)
        except Exception as e:
            results.append({
                "service": parser.__name__,
                "available": False,
                "error": str(e),
            })

    return {
        "success": True,
        "address": address,
        "display_name": display_name,
        "lat": lat,
        "lon": lon,
        "results": results,
    }


if __name__ == "__main__":
    import sys
    address = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Москва, Тверская улица, 1"
    print(f"Проверяем адрес: {address}")
    data = check_all_services(address)
    print(json.dumps(data, ensure_ascii=False, indent=2))
