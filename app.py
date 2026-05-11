"""
Flask бэкенд для проверки условий доставки продуктов.
Сервисы: Самокат, Яндекс Лавка, Ozon Fresh, Пятёрочка, Магнит, ВкусВилл

Парсинг через Playwright (перехват запросов мобильных приложений).
Самокат и Яндекс Лавка — прямой API (requests).
Fallback — публичные данные с сайтов сервисов.
"""

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import traceback

from delivery_checker import geocode_address
from playwright_parser import parse_all_sync, PUBLIC_DATA

app = Flask(__name__)
CORS(app)

_SERVICE_ORDER = ["Самокат", "Яндекс Лавка", "Ozon Fresh", "Пятёрочка", "Магнит", "ВкусВилл"]
_SERVICE_LOGOS = {
    "Самокат":      "🟡",
    "Яндекс Лавка": "🔵",
    "Ozon Fresh":   "🟠",
    "Пятёрочка":    "🔴",
    "Магнит":       "🟢",
    "ВкусВилл":     "🟣",
}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/check", methods=["POST"])
def check_delivery():
    """API endpoint для проверки условий доставки."""
    data = request.get_json()
    if not data or not data.get("address"):
        return jsonify({"success": False, "error": "Адрес не указан"}), 400

    address = data["address"].strip()
    if not address:
        return jsonify({"success": False, "error": "Адрес не может быть пустым"}), 400

    geo = geocode_address(address)
    if not geo:
        return jsonify({
            "success": False,
            "error": (
                f"Не удалось определить координаты для адреса: «{address}». "
                "Попробуйте указать более точный адрес (город, улица, дом)."
            ),
        }), 400

    lat = geo["lat"]
    lon = geo["lon"]
    display_name = geo["display_name"]

    try:
        results = parse_all_sync(lat, lon)
    except Exception as e:
        print(f"[ERROR] parse_all_sync failed: {e}\n{traceback.format_exc()}")
        results = []
        for svc in _SERVICE_ORDER:
            pub = PUBLIC_DATA.get(svc, {})
            results.append({
                "service":         svc,
                "logo":            _SERVICE_LOGOS.get(svc, "🛒"),
                "available":       True,
                "delivery_price":  pub.get("delivery_price"),
                "min_order":       pub.get("min_order"),
                "free_from":       pub.get("free_from"),
                "delivery_time":   pub.get("delivery_time"),
                "packaging_price": pub.get("packaging_price"),
                "assembly_price":  pub.get("assembly_price"),
                "note":            "Данные из публичных источников (ошибка парсера)",
                "error":           None,
                "data_source":     "public",
            })

    results.sort(
        key=lambda x: _SERVICE_ORDER.index(x["service"])
        if x["service"] in _SERVICE_ORDER else 99
    )

    return jsonify({
        "success":      True,
        "address":      address,
        "display_name": display_name,
        "lat":          lat,
        "lon":          lon,
        "results":      results,
    })


@app.route("/api/geocode", methods=["POST"])
def geocode():
    data = request.get_json()
    if not data or not data.get("address"):
        return jsonify({"success": False, "error": "Адрес не указан"}), 400
    geo = geocode_address(data["address"])
    if not geo:
        return jsonify({"success": False, "error": "Адрес не найден"}), 404
    return jsonify({"success": True, **geo})


if __name__ == "__main__":
    print("🚀 Запуск сервера проверки доставки...")
    print("📍 Откройте браузер: http://localhost:5050")
    app.run(debug=True, host="0.0.0.0", port=5050)
