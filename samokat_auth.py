"""
Авторизация в Самокате через мобильное API api-web.samokat.ru.

Алгоритм:
  1. GET https://sting.samokat.ru/api/v2/get_token → sting-токен (используется как DeviceId)
  2. POST /oauth/token/anonymous  { DeviceId: <sting> }  → анонимный access_token
  3. POST /oauth/token/phone      { phone }              → запрос SMS
  4. POST /oauth/token/phone/confirm { phone, code }     → access_token пользователя

Ключевое: DeviceId — это sting-токен из sting.samokat.ru, не UUID.
DeviceId передаётся в ЗАГОЛОВКЕ, не в теле запроса.

Хранение токена: файл .samokat_token.json (рядом с модулем).
"""

import json
import os
import time
import uuid
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_TOKEN_FILE = os.path.join(os.path.dirname(__file__), ".samokat_token.json")

_BASE  = "https://api-web.samokat.ru"
_STING = "https://sting.samokat.ru"

_HEADERS = {
    "User-Agent": "ru.samokat.app/5.0.0 (Android 13; Pixel 7)",
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Content-Type": "application/json",
    "X-Application-Platform": "android",
    "X-Application-Version": "5.0.0",
    "Origin": "https://samokat.ru",
    "Referer": "https://samokat.ru/",
}

_HEADERS_WEB = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://samokat.ru",
    "Referer": "https://samokat.ru/",
}


def _get_sting_device_id() -> str:
    """
    Получает sting-токен с sting.samokat.ru — используется как DeviceId.
    Это реальный DeviceId который использует браузер Самоката.
    """
    try:
        resp = requests.post(
            f"{_STING}/api/v2/get_token",
            headers=_HEADERS_WEB,
            json={},
            verify=False,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            sting = data.get("sting", "")
            if sting:
                print(f"[samokat_auth] sting device_id: {sting[:20]}...")
                return sting
    except Exception as e:
        print(f"[samokat_auth] Ошибка получения sting: {e}")

    # Fallback: UUID
    return str(uuid.uuid4())


def load_token() -> dict | None:
    """Загружает сохранённый токен. Возвращает None если нет или истёк."""
    if not os.path.exists(_TOKEN_FILE):
        return None
    try:
        with open(_TOKEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        expires_at = data.get("expires_at", 0)
        if expires_at and time.time() > expires_at - 300:
            return None
        return data
    except Exception:
        return None


def save_token(token_data: dict) -> None:
    try:
        with open(_TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(token_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[samokat_auth] Не удалось сохранить токен: {e}")


def clear_token() -> None:
    if os.path.exists(_TOKEN_FILE):
        os.remove(_TOKEN_FILE)


def get_valid_token() -> str | None:
    data = load_token()
    if data:
        return data.get("access_token")
    return None


def get_token_info() -> dict:
    data = load_token()
    if not data:
        return {"authorized": False, "phone": None, "expires_at": None}
    expires_at = data.get("expires_at", 0)
    remaining = max(0, int(expires_at - time.time()))
    days = remaining // 86400
    return {
        "authorized": True,
        "phone": data.get("phone", ""),
        "expires_at": expires_at,
        "expires_in_days": days,
    }


def _normalize_phone(phone: str) -> str:
    phone = phone.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if phone.startswith("8"):
        phone = "+7" + phone[1:]
    elif phone.startswith("7") and not phone.startswith("+"):
        phone = "+" + phone
    elif not phone.startswith("+"):
        phone = "+7" + phone
    return phone


def request_sms(phone: str) -> dict:
    """
    Шаг 1: Запрашивает SMS-код для авторизации в Самокате.

    Returns:
        { "success": bool, "device_id": str, "anon_token": str, "phone": str, "error": str | None }
    """
    phone = _normalize_phone(phone)

    # Получаем sting device_id
    device_id = _get_sting_device_id()

    session = requests.Session()

    # DeviceId ОБЯЗАТЕЛЬНО в заголовке
    headers = {**_HEADERS, "DeviceId": device_id}

    # Шаг 1a: Получаем анонимный токен
    anon_token = None
    try:
        resp = session.post(
            f"{_BASE}/oauth/token/anonymous",
            headers=headers,
            json={},
            verify=False,
            timeout=15,
        )
        print(f"[samokat_auth] anonymous: {resp.status_code} {resp.text[:200]}")
        if resp.status_code == 200:
            anon_token = resp.json().get("access_token")
        else:
            return {
                "success": False,
                "device_id": device_id,
                "phone": phone,
                "error": f"Ошибка получения анонимного токена: HTTP {resp.status_code} — {resp.text[:200]}",
            }
    except Exception as e:
        return {
            "success": False,
            "device_id": device_id,
            "phone": phone,
            "error": f"Ошибка соединения: {e}",
        }

    if not anon_token:
        return {
            "success": False,
            "device_id": device_id,
            "phone": phone,
            "error": "Не получен анонимный токен",
        }

    # Шаг 1b: Запрашиваем SMS
    auth_headers = {**headers, "Authorization": f"Bearer {anon_token}"}
    try:
        resp = session.post(
            f"{_BASE}/oauth/token/phone",
            headers=auth_headers,
            json={"phone": phone},
            verify=False,
            timeout=15,
        )
        print(f"[samokat_auth] phone: {resp.status_code} {resp.text[:200]}")

        if resp.status_code in (200, 201, 204):
            return {
                "success": True,
                "device_id": device_id,
                "phone": phone,
                "anon_token": anon_token,
                "error": None,
            }

        data = {}
        try:
            data = resp.json()
        except Exception:
            pass

        error_msg = (
            data.get("message")
            or data.get("error_description")
            or data.get("error")
            or data.get("code")
            or f"HTTP {resp.status_code}"
        )

        # 500 INTERNAL_SERVER_ERROR — номер не зарегистрирован в Самокате
        if resp.status_code == 500:
            error_msg = "Номер телефона не зарегистрирован в Самокате или сервис временно недоступен"

        return {
            "success": False,
            "device_id": device_id,
            "phone": phone,
            "anon_token": anon_token,
            "error": error_msg,
        }

    except Exception as e:
        return {
            "success": False,
            "device_id": device_id,
            "phone": phone,
            "error": f"Ошибка запроса SMS: {e}",
        }


def confirm_sms(phone: str, sms_code: str, device_id: str, anon_token: str = "") -> dict:
    """
    Шаг 2: Подтверждает SMS-код и получает токен Самоката.

    Returns:
        { "success": bool, "access_token": str, "error": str | None }
    """
    phone = _normalize_phone(phone)
    session = requests.Session()
    headers = {**_HEADERS, "DeviceId": device_id}

    if anon_token:
        headers["Authorization"] = f"Bearer {anon_token}"

    try:
        resp = session.post(
            f"{_BASE}/oauth/token/phone/confirm",
            headers=headers,
            json={"phone": phone, "code": sms_code},
            verify=False,
            timeout=15,
        )
        data = {}
        try:
            data = resp.json()
        except Exception:
            pass

        print(f"[samokat_auth] confirm: {resp.status_code} {str(data)[:200]}")

        if resp.status_code == 200 and "access_token" in data:
            access_token = data["access_token"]
            expires_in = data.get("expires_in", 2592000)  # 30 дней

            token_data = {
                "access_token": access_token,
                "refresh_token": data.get("refresh_token", ""),
                "expires_in": expires_in,
                "expires_at": time.time() + expires_in,
                "phone": phone,
                "device_id": device_id,
            }
            save_token(token_data)

            return {
                "success": True,
                "access_token": access_token,
                "error": None,
            }
        else:
            error_msg = (
                data.get("message")
                or data.get("error_description")
                or data.get("error")
                or data.get("code")
                or f"HTTP {resp.status_code}"
            )
            return {
                "success": False,
                "access_token": "",
                "error": f"Неверный код: {error_msg}",
            }

    except Exception as e:
        return {
            "success": False,
            "access_token": "",
            "error": f"Ошибка подтверждения: {e}",
        }


if __name__ == "__main__":
    import sys
    print("=== Авторизация в Самокате ===")
    phone = input("Введите номер телефона (+7XXXXXXXXXX): ").strip()
    result = request_sms(phone)
    if not result["success"]:
        print(f"❌ Ошибка: {result['error']}")
        sys.exit(1)
    print(f"✅ SMS отправлено на {result['phone']}")
    code = input("Введите код из SMS: ").strip()
    auth_result = confirm_sms(
        phone=result["phone"],
        sms_code=code,
        device_id=result["device_id"],
        anon_token=result.get("anon_token", ""),
    )
    if auth_result["success"]:
        print(f"✅ Авторизация успешна! Token: {auth_result['access_token'][:20]}...")
    else:
        print(f"❌ Ошибка: {auth_result['error']}")
