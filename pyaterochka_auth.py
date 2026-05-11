"""
Авторизация в Пятёрочке через Playwright (обход WAF).

api.5ka.ru защищён WAF и блокирует прямые запросы.
Используем Playwright для имитации браузера и перехвата реальных API запросов.

Алгоритм:
  1. Playwright: открываем 5ka.ru, нажимаем "Войти", вводим телефон
  2. Перехватываем POST запрос к API авторизации
  3. Повторяем запрос с теми же заголовками для confirm

Хранение токена: файл .pyaterochka_token.json (рядом с модулем).
"""

import asyncio
import json
import os
import time
import uuid
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_TOKEN_FILE = os.path.join(os.path.dirname(__file__), ".pyaterochka_token.json")

_BASE = "https://5ka.ru"
_API  = "https://api.5ka.ru"


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
        print(f"[pyaterochka_auth] Не удалось сохранить токен: {e}")


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
    Использует Playwright для отправки запроса SMS через браузер.
    Перехватывает реальный API запрос и его заголовки.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"success": False, "error": "Playwright не установлен", "api_info": {}}

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
            if ("auth" in u or "login" in u or "otp" in u or "phone" in u) and req.method == "POST":
                if "5ka" in u or "x5" in u:
                    api_info["url"] = u
                    api_info["headers"] = dict(req.headers)
                    api_info["post_data"] = req.post_data
                    sms_sent = True

        async def on_response(resp):
            u = resp.url
            if ("auth" in u or "login" in u) and ("5ka" in u or "x5" in u):
                try:
                    body = await resp.text()
                    api_info["response_status"] = resp.status
                    api_info["response_body"] = body[:500]
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            await page.goto(f"{_BASE}/", timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(3)

            # Ищем кнопку входа через JS (безопасно при навигации)
            try:
                await page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll('button, a, [role=button]'));
                    const b = btns.find(b => {
                        const t = b.textContent.trim();
                        return t.includes('Войти') || t.includes('Вход') || t.includes('Личный кабинет');
                    });
                    if (b) b.click();
                }""")
            except Exception:
                pass
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
            ]:
                try:
                    el = await page.query_selector(sel)
                except Exception:
                    continue
                if el:
                    try:
                        await el.fill(phone)
                        phone_filled = True
                        await asyncio.sleep(1)

                        # Нажимаем кнопку отправки
                        for bs in [
                            "button[type=submit]",
                            "button:has-text('Далее')",
                            "button:has-text('Получить код')",
                            "button:has-text('Продолжить')",
                            "button:has-text('Войти')",
                            "button:has-text('Отправить')",
                        ]:
                            try:
                                b = await page.query_selector(bs)
                                if b:
                                    await b.click(force=True)
                                    await asyncio.sleep(3)
                                    break
                            except Exception:
                                continue
                    except Exception:
                        pass
                    break

            if not phone_filled:
                # Пробуем через JS
                try:
                    await page.evaluate(f"""() => {{
                        const inputs = Array.from(document.querySelectorAll('input'));
                        const phoneInput = inputs.find(i => i.type === 'tel' || (i.name && i.name.includes('phone')) || (i.placeholder && i.placeholder.toLowerCase().includes('телефон')));
                        if (phoneInput) {{
                            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                            nativeInputValueSetter.call(phoneInput, '{phone}');
                            phoneInput.dispatchEvent(new Event('input', {{bubbles: true}}));
                            phoneInput.dispatchEvent(new Event('change', {{bubbles: true}}));
                        }}
                    }}""")
                    await asyncio.sleep(1)
                except Exception:
                    pass

        except Exception as e:
            print(f"[pyaterochka_auth] Playwright ошибка: {e}")

        # Получаем cookies
        cookies_list = await ctx.cookies()
        cookies = {c["name"]: c["value"] for c in cookies_list}

        await browser.close()

        return {
            "success": sms_sent,
            "api_info": api_info,
            "cookies": cookies,
            "error": None if sms_sent else "Не удалось найти форму авторизации на сайте",
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
    Запрашивает SMS-код для авторизации в Пятёрочке.

    Returns:
        { "success": bool, "phone": str, "api_info": dict, "cookies": dict, "error": str | None }
    """
    phone = _normalize_phone(phone)

    # Сначала пробуем прямой API запрос с правильными заголовками
    session = requests.Session()
    device_id = uuid.uuid4().hex

    # Пробуем разные варианты заголовков
    for headers in [
        {
            "User-Agent": "okhttp/4.9.3",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Device-Id": device_id,
            "X-App-Version": "4.0.0",
            "X-Platform": "android",
        },
        {
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 13; Pixel 7 Build/TQ3A.230901.001)",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Device-Id": device_id,
        },
    ]:
        for url in [
            f"{_API}/api/v2/auth/",
            f"{_API}/api/v1/auth/",
            f"{_API}/api/v2/auth/phone/",
        ]:
            try:
                resp = session.post(url, headers=headers, json={"phone": phone}, verify=False, timeout=10)
                # Если не WAF-блокировка (не HTML ответ)
                if resp.status_code in (200, 201, 204) and "Request Rejected" not in resp.text:
                    return {
                        "success": True,
                        "phone": phone,
                        "api_info": {"url": url, "headers": headers},
                        "cookies": dict(session.cookies),
                        "error": None,
                    }
                if resp.status_code in (400, 422) and "Request Rejected" not in resp.text:
                    # API отвечает — значит запрос дошёл
                    try:
                        data = resp.json()
                        error_msg = data.get("detail") or data.get("message") or data.get("error") or str(data)
                        return {
                            "success": False,
                            "phone": phone,
                            "api_info": {"url": url, "headers": headers},
                            "cookies": dict(session.cookies),
                            "error": error_msg,
                        }
                    except Exception:
                        pass
            except Exception:
                continue

    # Если прямые запросы заблокированы — используем Playwright
    result = _playwright_request_sms(phone)
    result["phone"] = phone
    return result


def confirm_sms(phone: str, sms_code: str, api_info: dict | None = None, cookies: dict | None = None) -> dict:
    """
    Подтверждает SMS-код и получает токен Пятёрочки.

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
        # Строим URL для подтверждения
        confirm_url = base_url.replace("/auth/", "/auth/confirm/").replace("/phone/", "/phone/confirm/")
        if confirm_url == base_url:
            confirm_url = base_url + "confirm/"

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
        "User-Agent": "okhttp/4.9.3",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Device-Id": device_id,
        "X-App-Version": "4.0.0",
        "X-Platform": "android",
    }

    for url in [
        f"{_API}/api/v2/auth/",
        f"{_API}/api/v2/auth/confirm/",
        f"{_API}/api/v1/auth/",
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

            if "Request Rejected" in resp.text:
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

            error_msg = data.get("detail") or data.get("message") or data.get("error") or f"HTTP {resp.status_code}"
            return {"success": False, "access_token": "", "error": f"Неверный код: {error_msg}"}
        except Exception:
            continue

    return {"success": False, "access_token": "", "error": "Не удалось подтвердить код"}


if __name__ == "__main__":
    import sys
    print("=== Авторизация в Пятёрочке ===")
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
