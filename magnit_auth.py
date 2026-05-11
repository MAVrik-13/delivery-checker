"""
Авторизация в Магните через magnit-id API.

Алгоритм:
  1. Playwright: GET https://magnit.ru/ → получаем анонимный JWT из localStorage/cookies
  2. POST https://magnit.ru/magnit-id/api/v1/auth/otp/send  { phone }  → запрос SMS
  3. POST https://magnit.ru/magnit-id/api/v1/auth/otp/confirm { phone, code } → access_token

Хранение токена: файл .magnit_token.json (рядом с модулем).
"""

import asyncio
import json
import os
import time
import uuid
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_TOKEN_FILE = os.path.join(os.path.dirname(__file__), ".magnit_token.json")

_BASE = "https://magnit.ru"
_MAGNIT_ID = "https://magnit.ru/magnit-id"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://magnit.ru",
    "Referer": "https://magnit.ru/",
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
        print(f"[magnit_auth] Не удалось сохранить токен: {e}")


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


async def _get_magnit_jwt_async() -> dict:
    """
    Использует Playwright для получения анонимного JWT токена Магнита.
    JWT генерируется при загрузке страницы и хранится в localStorage.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"jwt": "", "cookies": {}}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            viewport={"width": 390, "height": 844},
        )
        page = await ctx.new_page()

        jwt_token = ""
        api_calls = []

        async def on_request(req):
            u = req.url
            if "magnit-id" in u or "auth" in u.lower():
                h = dict(req.headers)
                auth = h.get("authorization", "")
                if auth.startswith("Bearer "):
                    nonlocal jwt_token
                    jwt_token = auth[7:]
                api_calls.append({"url": u, "auth": auth, "post_data": req.post_data})

        page.on("request", on_request)

        try:
            await page.goto(f"{_BASE}/", timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(4)

            # Ищем JWT в localStorage
            jwt_from_storage = await page.evaluate("""() => {
                // Ищем в localStorage
                for (let i = 0; i < localStorage.length; i++) {
                    const key = localStorage.key(i);
                    const val = localStorage.getItem(key);
                    if (val && val.startsWith('eyJ')) return val;
                    if (key.toLowerCase().includes('token') || key.toLowerCase().includes('jwt')) {
                        if (val && val.length > 20) return val;
                    }
                }
                // Ищем в sessionStorage
                for (let i = 0; i < sessionStorage.length; i++) {
                    const key = sessionStorage.key(i);
                    const val = sessionStorage.getItem(key);
                    if (val && val.startsWith('eyJ')) return val;
                }
                return '';
            }""")

            if jwt_from_storage:
                jwt_token = jwt_from_storage

            # Нажимаем Войти чтобы получить JWT из запросов
            if not jwt_token:
                await page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll('button, a, [role=button]'));
                    const b = btns.find(b => b.textContent.trim().startsWith('Войти'));
                    if (b) b.click();
                }""")
                await asyncio.sleep(3)

                # Снова ищем в localStorage
                jwt_from_storage = await page.evaluate("""() => {
                    for (let i = 0; i < localStorage.length; i++) {
                        const key = localStorage.key(i);
                        const val = localStorage.getItem(key);
                        if (val && val.startsWith('eyJ')) return val;
                    }
                    return '';
                }""")
                if jwt_from_storage:
                    jwt_token = jwt_from_storage

        except Exception as e:
            print(f"[magnit_auth] Playwright ошибка: {e}")

        # Получаем cookies
        cookies_list = await ctx.cookies()
        cookies = {c["name"]: c["value"] for c in cookies_list}

        await browser.close()

        return {"jwt": jwt_token, "cookies": cookies, "api_calls": api_calls}


def _get_magnit_jwt() -> dict:
    """Синхронная обёртка."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_get_magnit_jwt_async())
        loop.close()
        return result
    except Exception as e:
        print(f"[magnit_auth] Ошибка получения JWT: {e}")
        return {"jwt": "", "cookies": {}}


def request_sms(phone: str) -> dict:
    """
    Запрашивает SMS-код для авторизации в Магните.

    Returns:
        { "success": bool, "phone": str, "jwt": str, "cookies": dict, "error": str | None }
    """
    phone = _normalize_phone(phone)

    # Получаем анонимный JWT через Playwright
    jwt_data = _get_magnit_jwt()
    jwt = jwt_data.get("jwt", "")
    cookies = jwt_data.get("cookies", {})

    session = requests.Session()
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=".magnit.ru")

    headers = {**_HEADERS}
    if jwt:
        headers["Authorization"] = f"Bearer {jwt}"

    # Пробуем разные endpoints magnit-id
    endpoints = [
        f"{_MAGNIT_ID}/api/v1/auth/otp/send",
        f"{_MAGNIT_ID}/api/v2/auth/otp/send",
        f"{_MAGNIT_ID}/api/v1/auth/phone",
        f"{_MAGNIT_ID}/api/v1/auth/login",
        f"{_BASE}/webgate/auth/v1/otp/send",
        f"{_BASE}/webgate/auth/v1/phone",
    ]

    last_error = "Все endpoints недоступны"
    for endpoint in endpoints:
        try:
            resp = session.post(
                endpoint,
                headers=headers,
                json={"phone": phone},
                verify=False,
                timeout=15,
            )
            data = {}
            try:
                data = resp.json()
            except Exception:
                pass

            if resp.status_code in (200, 201, 204):
                return {
                    "success": True,
                    "phone": phone,
                    "jwt": jwt,
                    "cookies": cookies,
                    "error": None,
                }

            if data.get("success") or data.get("status") == "ok":
                return {
                    "success": True,
                    "phone": phone,
                    "jwt": jwt,
                    "cookies": cookies,
                    "error": None,
                }

            last_error = (
                data.get("message")
                or data.get("detail")
                or data.get("error")
                or data.get("code")
                or f"HTTP {resp.status_code}"
            )

            # 401 — JWT не подошёл, пробуем следующий endpoint
            if resp.status_code == 401:
                continue

        except Exception as e:
            last_error = str(e)
            continue

    return {
        "success": False,
        "phone": phone,
        "jwt": jwt,
        "cookies": cookies,
        "error": last_error,
    }


def confirm_sms(phone: str, sms_code: str, jwt: str = "", cookies: dict | None = None) -> dict:
    """
    Подтверждает SMS-код и получает токен Магнита.

    Returns:
        { "success": bool, "access_token": str, "error": str | None }
    """
    phone = _normalize_phone(phone)
    session = requests.Session()

    if cookies:
        for name, value in cookies.items():
            session.cookies.set(name, value, domain=".magnit.ru")

    headers = {**_HEADERS}
    if jwt:
        headers["Authorization"] = f"Bearer {jwt}"

    endpoints = [
        f"{_MAGNIT_ID}/api/v1/auth/otp/confirm",
        f"{_MAGNIT_ID}/api/v2/auth/otp/confirm",
        f"{_MAGNIT_ID}/api/v1/auth/phone/confirm",
        f"{_BASE}/webgate/auth/v1/otp/confirm",
    ]

    last_error = "Все endpoints недоступны"
    for endpoint in endpoints:
        try:
            resp = session.post(
                endpoint,
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

            last_error = (
                data.get("message")
                or data.get("detail")
                or data.get("error")
                or data.get("code")
                or f"HTTP {resp.status_code}"
            )

        except Exception as e:
            last_error = str(e)
            continue

    return {"success": False, "access_token": "", "error": f"Ошибка: {last_error}"}


if __name__ == "__main__":
    import sys
    print("=== Авторизация в Магните ===")
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
        jwt=result.get("jwt", ""),
        cookies=result.get("cookies"),
    )
    if auth_result["success"]:
        print(f"✅ Авторизация успешна! Token: {auth_result['access_token'][:20]}...")
    else:
        print(f"❌ Ошибка: {auth_result['error']}")
