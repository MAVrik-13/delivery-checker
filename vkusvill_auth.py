"""
Авторизация в ВкусВилле через Bitrix API.

ВкусВилл использует Bitrix CMS. Авторизация через форму на /personal/.

Алгоритм:
  1. GET https://vkusvill.ru/personal/ → получаем sessid (CSRF) из HTML
  2. POST https://vkusvill.ru/personal/ { sessid, USER_PHONE, SMS_TYPE=sms } → запрос SMS
  3. POST https://vkusvill.ru/personal/ { sessid, PHONE, CODE } → подтверждение → cookies сессии

Хранение токена: файл .vkusvill_token.json (рядом с модулем).
"""

import json
import os
import re
import time
import uuid
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_TOKEN_FILE = os.path.join(os.path.dirname(__file__), ".vkusvill_token.json")

_BASE = "https://vkusvill.ru"

_HEADERS_BROWSER = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

_HEADERS_AJAX = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://vkusvill.ru",
    "Referer": "https://vkusvill.ru/personal/",
}


def load_token() -> dict | None:
    if not os.path.exists(_TOKEN_FILE):
        return None
    try:
        with open(_TOKEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Для Bitrix сессии нет expires — проверяем по времени (7 дней)
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
        print(f"[vkusvill_auth] Не удалось сохранить токен: {e}")


def clear_token() -> None:
    if os.path.exists(_TOKEN_FILE):
        os.remove(_TOKEN_FILE)


def get_valid_token() -> str | None:
    data = load_token()
    if data:
        return data.get("access_token") or data.get("session_id")
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


def _get_sessid(session: requests.Session) -> str:
    """Получает sessid (CSRF-токен Bitrix) со страницы /personal/."""
    try:
        resp = session.get(
            f"{_BASE}/personal/",
            headers=_HEADERS_BROWSER,
            verify=False,
            timeout=15,
        )
        # Ищем sessid в HTML
        m = re.search(r'name=["\']sessid["\'][^>]*value=["\']([a-f0-9]+)["\']', resp.text, re.I)
        if m:
            return m.group(1)
        # Альтернативный поиск
        m = re.search(r'["\']sessid["\']\s*:\s*["\']([a-f0-9]+)["\']', resp.text)
        if m:
            return m.group(1)
        # Ищем в JS
        m = re.search(r'BX\.message\([^)]*sessid["\']?\s*:\s*["\']([a-f0-9]+)', resp.text)
        if m:
            return m.group(1)
    except Exception as e:
        print(f"[vkusvill_auth] Ошибка получения sessid: {e}")
    return ""


def request_sms(phone: str) -> dict:
    """
    Запрашивает SMS-код для авторизации в ВкусВилле.

    Returns:
        { "success": bool, "phone": str, "sessid": str, "cookies": dict, "error": str | None }
    """
    phone = _normalize_phone(phone)
    # Убираем + для Bitrix (он ожидает 7XXXXXXXXXX или +7XXXXXXXXXX)
    phone_for_bitrix = phone  # оставляем с +

    session = requests.Session()

    # Получаем sessid
    sessid = _get_sessid(session)
    if not sessid:
        return {
            "success": False,
            "phone": phone,
            "sessid": "",
            "cookies": {},
            "error": "Не удалось получить CSRF-токен (sessid) с сайта ВкусВилла",
        }

    # Отправляем запрос SMS через Bitrix форму
    try:
        # Вариант 1: через AJAX endpoint авторизации
        resp = session.post(
            f"{_BASE}/personal/",
            headers=_HEADERS_AJAX,
            data={
                "sessid": sessid,
                "USER_PHONE": phone_for_bitrix,
                "SMS_TYPE": "sms",
                "action": "sendSms",
                "FUSER_ID": "",
                "USER_NAME": "",
            },
            verify=False,
            timeout=15,
        )

        # Проверяем ответ
        try:
            data = resp.json()
            if data.get("success") or data.get("status") == "ok" or data.get("result") == "ok":
                return {
                    "success": True,
                    "phone": phone,
                    "sessid": sessid,
                    "cookies": dict(session.cookies),
                    "error": None,
                }
            error_msg = data.get("message") or data.get("error") or str(data)
        except Exception:
            # HTML ответ — проверяем на успех
            if "код" in resp.text.lower() or "sms" in resp.text.lower() or resp.status_code == 200:
                return {
                    "success": True,
                    "phone": phone,
                    "sessid": sessid,
                    "cookies": dict(session.cookies),
                    "error": None,
                }
            error_msg = f"HTTP {resp.status_code}"

        # Вариант 2: через API endpoint
        resp2 = session.post(
            f"{_BASE}/api/v1/users/auth/",
            headers={
                **_HEADERS_AJAX,
                "Content-Type": "application/json",
            },
            json={"phone": phone_for_bitrix},
            verify=False,
            timeout=15,
        )

        if resp2.status_code == 200:
            try:
                data2 = resp2.json()
                if not data2.get("error"):
                    return {
                        "success": True,
                        "phone": phone,
                        "sessid": sessid,
                        "cookies": dict(session.cookies),
                        "error": None,
                    }
            except Exception:
                pass

        return {
            "success": False,
            "phone": phone,
            "sessid": sessid,
            "cookies": dict(session.cookies),
            "error": error_msg,
        }

    except Exception as e:
        return {
            "success": False,
            "phone": phone,
            "sessid": sessid,
            "cookies": {},
            "error": f"Ошибка запроса SMS: {e}",
        }


def confirm_sms(phone: str, sms_code: str, sessid: str = "", cookies: dict | None = None) -> dict:
    """
    Подтверждает SMS-код и получает сессию ВкусВилла.

    Returns:
        { "success": bool, "access_token": str, "error": str | None }
    """
    phone = _normalize_phone(phone)
    session = requests.Session()

    if cookies:
        for name, value in cookies.items():
            session.cookies.set(name, value, domain="vkusvill.ru")

    # Если sessid не передан — получаем новый
    if not sessid:
        sessid = _get_sessid(session)

    try:
        # Вариант 1: через Bitrix форму
        resp = session.post(
            f"{_BASE}/personal/",
            headers=_HEADERS_AJAX,
            data={
                "sessid": sessid,
                "PHONE": phone,
                "SMS": sms_code,
                "action": "checkSms",
                "NEW_CARD": "N",
                "FUSER_ID": "",
                "USER_NAME": "",
                "AUTH_TYPE": "sms",
            },
            verify=False,
            timeout=15,
        )

        try:
            data = resp.json()
            if data.get("success") or data.get("status") == "ok" or data.get("result") == "ok":
                # Сохраняем сессионные cookies как токен
                session_cookies = dict(session.cookies)
                session_id = session_cookies.get("PHPSESSID", "") or session_cookies.get("__Host-PHPSESSID", "")

                token_data = {
                    "access_token": session_id,
                    "session_id": session_id,
                    "cookies": session_cookies,
                    "expires_at": time.time() + 7 * 86400,  # 7 дней
                    "phone": phone,
                }
                save_token(token_data)
                return {"success": True, "access_token": session_id, "error": None}

            error_msg = data.get("message") or data.get("error") or str(data)
        except Exception:
            if resp.status_code == 200:
                session_cookies = dict(session.cookies)
                session_id = session_cookies.get("PHPSESSID", "") or session_cookies.get("__Host-PHPSESSID", "")
                if session_id:
                    token_data = {
                        "access_token": session_id,
                        "session_id": session_id,
                        "cookies": session_cookies,
                        "expires_at": time.time() + 7 * 86400,
                        "phone": phone,
                    }
                    save_token(token_data)
                    return {"success": True, "access_token": session_id, "error": None}
            error_msg = f"HTTP {resp.status_code}"

        # Вариант 2: через API
        resp2 = session.post(
            f"{_BASE}/api/v1/users/auth/",
            headers={**_HEADERS_AJAX, "Content-Type": "application/json"},
            json={"phone": phone, "code": sms_code},
            verify=False,
            timeout=15,
        )

        try:
            data2 = resp2.json()
            token = (
                data2.get("access_token")
                or data2.get("token")
                or (data2.get("user") or {}).get("token")
            )
            if resp2.status_code == 200 and token:
                token_data = {
                    "access_token": token,
                    "expires_at": time.time() + 7 * 86400,
                    "phone": phone,
                }
                save_token(token_data)
                return {"success": True, "access_token": token, "error": None}
        except Exception:
            pass

        return {"success": False, "access_token": "", "error": f"Неверный код: {error_msg}"}

    except Exception as e:
        return {"success": False, "access_token": "", "error": f"Ошибка подтверждения: {e}"}


if __name__ == "__main__":
    import sys
    print("=== Авторизация в ВкусВилле ===")
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
        sessid=result.get("sessid", ""),
        cookies=result.get("cookies"),
    )
    if auth_result["success"]:
        print(f"✅ Авторизация успешна! Token: {auth_result['access_token'][:20]}...")
    else:
        print(f"❌ Ошибка: {auth_result['error']}")
