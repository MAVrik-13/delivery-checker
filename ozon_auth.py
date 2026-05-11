"""
Авторизация в Ozon Fresh через Playwright (обход Cloudflare).

Ozon использует Cloudflare и блокирует прямые API запросы.
Используем Playwright для имитации браузера.

Алгоритм:
  1. Playwright: открываем ozon.ru/my/login/, вводим телефон
  2. Перехватываем реальный API запрос авторизации
  3. Используем полученные cookies/токены для confirm

Хранение токена: файл .ozon_token.json (рядом с модулем).
"""

import asyncio
import json
import os
import time
import uuid
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_TOKEN_FILE = os.path.join(os.path.dirname(__file__), ".ozon_token.json")

_BASE = "https://www.ozon.ru"
_API  = "https://api.ozon.ru"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://www.ozon.ru",
    "Referer": "https://www.ozon.ru/",
}


def load_token() -> dict | None:
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
        print(f"[ozon_auth] Не удалось сохранить токен: {e}")


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


async def _playwright_request_sms_async(phone: str) -> dict:
    """
    Использует Playwright для отправки запроса SMS через браузер Ozon.
    Перехватывает реальный API запрос и его заголовки/cookies.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"success": False, "error": "Playwright не установлен", "api_info": {}, "cookies": {}}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            viewport={"width": 390, "height": 844},
        )
        page = await ctx.new_page()

        api_info = {}
        sms_sent = False

        async def on_request(req):
            nonlocal sms_sent
            u = req.url
            if req.method == "POST" and ("auth" in u or "login" in u or "otp" in u or "phone" in u):
                if "ozon" in u:
                    api_info["url"] = u
                    api_info["headers"] = dict(req.headers)
                    api_info["post_data"] = req.post_data
                    sms_sent = True

        async def on_response(resp):
            u = resp.url
            if "ozon" in u and ("auth" in u or "login" in u):
                try:
                    body = await resp.text()
                    api_info["response_status"] = resp.status
                    api_info["response_body"] = body[:500]
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            # Открываем страницу входа
            await page.goto(f"{_BASE}/my/login/", timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(3)

            # Ищем поле телефона
            phone_filled = False
            for sel in [
                "input[type=tel]",
                "input[name*=phone]",
                "input[placeholder*='телефон']",
                "input[placeholder*='Телефон']",
                "input[placeholder*='номер']",
                "input[placeholder*='Номер']",
                "input[placeholder*='phone']",
            ]:
                el = await page.query_selector(sel)
                if el:
                    await el.fill(phone)
                    phone_filled = True
                    await asyncio.sleep(1)

                    for bs in [
                        "button[type=submit]",
                        "button:has-text('Войти')",
                        "button:has-text('Далее')",
                        "button:has-text('Получить код')",
                        "button:has-text('Продолжить')",
                        "button:has-text('Отправить')",
                    ]:
                        b = await page.query_selector(bs)
                        if b:
                            await b.click(force=True)
                            await asyncio.sleep(3)
                            break
                    break

            if not phone_filled:
                # Пробуем через JS
                await page.evaluate(f"""() => {{
                    const inputs = Array.from(document.querySelectorAll('input'));
                    const phoneInput = inputs.find(i =>
                        i.type === 'tel' ||
                        (i.placeholder && i.placeholder.toLowerCase().includes('телефон')) ||
                        (i.name && i.name.toLowerCase().includes('phone'))
                    );
                    if (phoneInput) {{
                        phoneInput.value = '{phone}';
                        phoneInput.dispatchEvent(new Event('input', {{bubbles: true}}));
                        phoneInput.dispatchEvent(new Event('change', {{bubbles: true}}));
                    }}
                }}""")
                await asyncio.sleep(2)

        except Exception as e:
            print(f"[ozon_auth] Playwright ошибка: {e}")

        # Получаем cookies
        cookies_list = await ctx.cookies()
        cookies = {c["name"]: c["value"] for c in cookies_list}

        await browser.close()

        return {
            "success": sms_sent,
            "api_info": api_info,
            "cookies": cookies,
            "error": None if sms_sent else "Не удалось найти форму авторизации на сайте Ozon",
        }


def _playwright_request_sms(phone: str) -> dict:
    """Синхронная обёртка."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_playwright_request_sms_async(phone))
        loop.close()
        return result
    except Exception as e:
        return {"success": False, "error": str(e), "api_info": {}, "cookies": {}}


def request_sms(phone: str) -> dict:
    """
    Запрашивает SMS-код для авторизации в Ozon.

    Returns:
        { "success": bool, "phone": str, "api_info": dict, "cookies": dict, "error": str | None }
    """
    phone = _normalize_phone(phone)

    # Сначала пробуем прямой API (иногда работает с правильными заголовками)
    session = requests.Session()
    device_id = uuid.uuid4().hex

    direct_headers = {
        **_HEADERS,
        "x-o3-app-name": "ozonapp_android",
        "x-o3-app-version": "17.0.1",
        "x-o3-device-type": "mobile",
        "x-o3-device-id": device_id,
    }

    for url in [
        f"{_API}/api/v1/auth/phone",
        f"{_API}/api/v2/auth/phone",
        f"{_BASE}/api/v1/auth/phone",
    ]:
        try:
            resp = session.post(url, headers=direct_headers, json={"phone": phone}, verify=False, timeout=10)
            if resp.status_code in (200, 201) and "incidentId" not in resp.text:
                return {
                    "success": True,
                    "phone": phone,
                    "api_info": {"url": url, "headers": direct_headers},
                    "cookies": dict(session.cookies),
                    "error": None,
                }
        except Exception:
            continue

    # Используем Playwright
    result = _playwright_request_sms(phone)
    result["phone"] = phone
    return result


def confirm_sms(phone: str, sms_code: str, api_info: dict | None = None, cookies: dict | None = None) -> dict:
    """
    Подтверждает SMS-код и получает токен Ozon.

    Returns:
        { "success": bool, "access_token": str, "error": str | None }
    """
    phone = _normalize_phone(phone)
    session = requests.Session()

    if cookies:
        for name, value in cookies.items():
            session.cookies.set(name, value)

    # Если есть информация о реальном API из Playwright
    if api_info and api_info.get("url"):
        base_url = api_info["url"]
        confirm_url = base_url.replace("/phone", "/phone/confirm").replace("/send", "/confirm")
        if confirm_url == base_url:
            confirm_url = base_url + "/confirm"

        headers = api_info.get("headers", {})
        try:
            resp = session.post(
                confirm_url,
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

            token = (
                data.get("access_token")
                or data.get("accessToken")
                or data.get("token")
                or (data.get("data") or {}).get("access_token")
            )
            if resp.status_code == 200 and token:
                expires_in = data.get("expires_in", 2592000)
                token_data = {
                    "access_token": token,
                    "refresh_token": data.get("refresh_token", ""),
                    "expires_in": expires_in,
                    "expires_at": time.time() + expires_in,
                    "phone": phone,
                }
                save_token(token_data)
                return {"success": True, "access_token": token, "error": None}
        except Exception:
            pass

    # Стандартные попытки
    device_id = uuid.uuid4().hex
    headers = {
        **_HEADERS,
        "x-o3-app-name": "ozonapp_android",
        "x-o3-app-version": "17.0.1",
        "x-o3-device-type": "mobile",
        "x-o3-device-id": device_id,
    }

    for url in [
        f"{_API}/api/v1/auth/phone/confirm",
        f"{_API}/api/v2/auth/phone/confirm",
        f"{_BASE}/api/v1/auth/phone/confirm",
    ]:
        try:
            resp = session.post(
                url,
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

            if "incidentId" in resp.text:
                continue

            token = (
                data.get("access_token")
                or data.get("accessToken")
                or data.get("token")
            )
            if resp.status_code == 200 and token:
                expires_in = data.get("expires_in", 2592000)
                token_data = {
                    "access_token": token,
                    "refresh_token": data.get("refresh_token", ""),
                    "expires_in": expires_in,
                    "expires_at": time.time() + expires_in,
                    "phone": phone,
                }
                save_token(token_data)
                return {"success": True, "access_token": token, "error": None}

            error_msg = data.get("message") or data.get("error") or f"HTTP {resp.status_code}"
            return {"success": False, "access_token": "", "error": f"Неверный код: {error_msg}"}
        except Exception:
            continue

    return {"success": False, "access_token": "", "error": "Не удалось подтвердить код"}


if __name__ == "__main__":
    import sys
    print("=== Авторизация в Ozon Fresh ===")
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
        api_info=result.get("api_info"),
        cookies=result.get("cookies"),
    )
    if auth_result["success"]:
        print(f"✅ Авторизация успешна! Token: {auth_result['access_token'][:20]}...")
    else:
        print(f"❌ Ошибка: {auth_result['error']}")
