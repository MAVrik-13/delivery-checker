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
import os

from delivery_checker import geocode_address
from playwright_parser import parse_all_sync, PUBLIC_DATA
from pyaterochka_auth import request_sms, confirm_sms, get_token_info, clear_token

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


@app.route("/api/pyaterochka/send-sms", methods=["POST"])
def pyaterochka_send_sms():
    """Отправляет SMS-код для авторизации в Пятёрочке."""
    data = request.get_json()
    if not data or not data.get("phone"):
        return jsonify({"success": False, "error": "Номер телефона не указан"}), 400
    
    phone = data["phone"].strip()
    if not phone:
        return jsonify({"success": False, "error": "Номер телефона не может быть пустым"}), 400
    
    try:
        result = request_sms(phone)
        return jsonify(result)
    except Exception as e:
        print(f"[ERROR] pyaterochka_send_sms failed: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/pyaterochka/confirm-sms", methods=["POST"])
def pyaterochka_confirm_sms():
    """Подтверждает SMS-код и получает токен Пятёрочки."""
    data = request.get_json()
    if not data or not data.get("phone") or not data.get("sms_code"):
        return jsonify({"success": False, "error": "Номер телефона и SMS-код обязательны"}), 400
    
    phone = data["phone"].strip()
    sms_code = data["sms_code"].strip()
    api_info = data.get("api_info")
    cookies = data.get("cookies")
    
    if not phone or not sms_code:
        return jsonify({"success": False, "error": "Номер телефона и SMS-код не могут быть пустыми"}), 400
    
    try:
        result = confirm_sms(phone, sms_code, api_info, cookies)
        return jsonify(result)
    except Exception as e:
        print(f"[ERROR] pyaterochka_confirm_sms failed: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/pyaterochka/token-info", methods=["GET"])
def pyaterochka_token_info():
    """Возвращает информацию о текущем токене авторизации Пятёрочки."""
    try:
        info = get_token_info()
        return jsonify(info)
    except Exception as e:
        print(f"[ERROR] pyaterochka_token_info failed: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/pyaterochka/logout", methods=["POST"])
def pyaterochka_logout():
    """Удаляет токен авторизации Пятёрочки."""
    try:
        clear_token()
        return jsonify({"success": True})
    except Exception as e:
        print(f"[ERROR] pyaterochka_logout failed: {e}\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/health")
def health():
    """Healthcheck endpoint для Railway."""
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    debug = os.environ.get("FLASK_ENV") != "production"
    print(f"🚀 Запуск сервера проверки доставки на порту {port}...")
    if debug:
        print(f"📍 Откройте браузер: http://localhost:{port}")
    app.run(debug=debug, host="0.0.0.0", port=port)
