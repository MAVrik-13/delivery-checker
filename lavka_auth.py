"""
Авторизация в Яндекс Лавке через Яндекс Паспорт (новый pwl-yandex flow).

Алгоритм (через Playwright):
  1. GET https://passport.yandex.ru/auth → редирект на /pwl-yandex
  2. Устанавливаем телефон через JS (nativeInputValueSetter) → без форматирования
  3. Нажимаем "Войти" → Яндекс валидирует номер и отправляет SMS/push
  4. Страница переходит на /pwl-yandex/auth/code
  5. Перехватываем csrf_token и track_id
  6. Подтверждаем код через /pwl-yandex/api/passport/commit_otp
  7. Обмениваем x_token → OAuth access_token для Лавки

Хранение токена: файл .lavka_token.json (рядом с модулем).
"""

import asyncio
import json
import os
import re
import time
import uuid
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_TOKEN_FILE = os.path.join(os.path.dirname(__file__), ".lavka_token.json")

_PASSPORT_HOST = "https://passport.yandex.ru"
_PWL_HOST      = "https://passport.yandex.ru/pwl-yandex"
_OAUTH_HOST    = "https://oauth.yandex.ru"

# client_id мобильного приложения Яндекс Лавки (Android)
_LAVKA_CLIENT_ID     = "c0ebe342af7d48fbbbfcf2d2eedb8f9e"
_LAVKA_CLIENT_SECRET = "ad0a908f0aa341a182a37ecd75bc319e"


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
        print(f"[lavka_auth] Не удалось сохранить токен: {e}")


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
        "uid": data.get("uid", ""),
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
    Использует Playwright для отправки SMS через Яндекс Паспорт (pwl-yandex flow).

    Ключевой трюк: устанавливаем значение телефона через nativeInputValueSetter
    чтобы избежать форматирования браузером (+7 (962) 682-36-99 → невалидный).

    Возвращает:
        {
            "success": bool,
            "csrf_token": str,
            "track_id": str,
            "cookies": dict,
            "process_uuid": str,
            "phone": str,
            "error": str | None,
        }
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "success": False, "error": "Playwright не установлен",
            "csrf_token": "", "track_id": "", "cookies": {}, "process_uuid": "",
        }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            viewport={"width": 390, "height": 844},
            locale="ru-RU",
        )
        page = await ctx.new_page()

        csrf_token = ""
        track_id = ""
        process_uuid = ""
        sms_sent = False
        sms_error = ""

        async def on_request(req):
            nonlocal csrf_token
            h = dict(req.headers)
            tok = h.get("x-csrf-token", "")
            if tok:
                csrf_token = tok

        async def on_response(resp):
            nonlocal track_id, sms_sent, sms_error
            if "passport.yandex.ru" not in resp.url:
                return
            if resp.request.method != "POST":
                return
            try:
                body = await resp.text()
                data = json.loads(body)
            except Exception:
                return

            url = resp.url
            # Получаем track_id
            if "track/create" in url:
                tid = data.get("id", "")
                if tid:
                    track_id = tid
                    print(f"[lavka_auth] track_id: {track_id}")

            # Проверяем успешную отправку SMS/push
            elif "suggest-send-push" in url or "send_otp" in url or "send-otp" in url:
                if resp.status == 200:
                    sms_sent = True
                    print(f"[lavka_auth] SMS/push отправлен: {url[-50:]}")
                else:
                    sms_error = data.get("message") or data.get("error") or f"HTTP {resp.status}"

            # Проверяем переход на страницу ввода кода
            elif "auth/code" in url or "commit_otp" in url:
                if resp.status == 200:
                    sms_sent = True

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            await page.goto(
                f"{_PASSPORT_HOST}/auth",
                timeout=20000,
                wait_until="domcontentloaded",
            )
            await asyncio.sleep(3)

            # Получаем process_uuid из URL
            current_url = page.url
            m = re.search(r"process_uuid=([a-f0-9-]+)", current_url)
            if m:
                process_uuid = m.group(1)
                print(f"[lavka_auth] process_uuid: {process_uuid}")

            # Ждём поле телефона
            try:
                await page.wait_for_selector("input[type=tel]", timeout=8000)
            except Exception:
                print("[lavka_auth] Поле телефона не найдено")

            phone_input = await page.query_selector("input[type=tel]")
            if phone_input:
                # Устанавливаем значение через nativeInputValueSetter
                # чтобы избежать автоформатирования браузером
                await page.evaluate(
                    """(phone) => {
                        const inp = document.querySelector("input[type=tel]");
                        if (inp) {
                            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, "value"
                            ).set;
                            nativeInputValueSetter.call(inp, phone);
                            inp.dispatchEvent(new Event("input", {bubbles: true}));
                            inp.dispatchEvent(new Event("change", {bubbles: true}));
                        }
                    }""",
                    phone,
                )
                await asyncio.sleep(1.5)

                # Нажимаем кнопку "Войти"
                for sel in [
                    'button:has-text("Войти")',
                    'button:has-text("Log in")',
                    "button[type=submit]",
                ]:
                    btn = await page.query_selector(sel)
                    if btn:
                        txt = await btn.inner_text()
                        print(f"[lavka_auth] Нажимаем: [{txt.strip()}]")
                        await btn.click()
                        break

                # Ждём перехода на страницу кода
                await asyncio.sleep(6)
                print(f"[lavka_auth] URL after submit: {page.url}")

                # Если перешли на /auth/code — SMS отправлено
                if "/auth/code" in page.url:
                    sms_sent = True
                    print("[lavka_auth] Перешли на страницу ввода кода ✅")

        except Exception as e:
            print(f"[lavka_auth] Playwright ошибка: {e}")
            sms_error = str(e)

        # Получаем cookies
        cookies_list = await ctx.cookies()
        cookies = {c["name"]: c["value"] for c in cookies_list}

        await browser.close()

        return {
            "success": sms_sent,
            "csrf_token": csrf_token,
            "track_id": track_id,
            "cookies": cookies,
            "process_uuid": process_uuid,
            "phone": phone,
            "error": None if sms_sent else (sms_error or "Не удалось отправить SMS через Яндекс Паспорт"),
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
        print(f"[lavka_auth] Ошибка Playwright: {e}")
        return {
            "success": False, "error": str(e),
            "csrf_token": "", "track_id": "", "cookies": {}, "process_uuid": "",
        }


def request_sms(phone: str) -> dict:
    """
    Шаг 1: Запрашивает SMS-код для авторизации в Яндекс Лавке.

    Returns:
        {
            "success": bool,
            "track_id": str,
            "csrf_token": str,
            "cookies": dict,
            "process_uuid": str,
            "phone": str,
            "error": str | None,
        }
    """
    phone = _normalize_phone(phone)
    result = _playwright_request_sms(phone)
    result["phone"] = phone
    return result


def confirm_sms(
    track_id: str,
    sms_code: str,
    phone: str,
    csrf_token: str = "",
    cookies: dict | None = None,
    process_uuid: str = "",
) -> dict:
    """
    Шаг 2: Подтверждает SMS-код и получает OAuth-токен Яндекс Лавки.

    Returns:
        {
            "success": bool,
            "access_token": str,
            "expires_in": int,
            "uid": str,
            "error": str | None,
        }
    """
    phone = _normalize_phone(phone)
    session = requests.Session()

    if cookies:
        for name, value in cookies.items():
            session.cookies.set(name, value, domain=".yandex.ru")

    base_headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://passport.yandex.ru",
        "Referer": f"https://passport.yandex.ru/pwl-yandex/auth/code?cause=auth&process_uuid={process_uuid}",
    }
    if csrf_token:
        base_headers["X-CSRF-Token"] = csrf_token

    x_token = None
    uid = ""
    last_error = "Не удалось подтвердить код"

    # Пробуем разные endpoints подтверждения OTP
    confirm_endpoints = [
        # Новый pwl-yandex flow
        (f"{_PWL_HOST}/api/passport/commit_otp",
         {"track_id": track_id, "code": sms_code}),
        (f"{_PWL_HOST}/api/passport/otp/confirm",
         {"track_id": track_id, "code": sms_code}),
        (f"{_PWL_HOST}/api/passport/auth/confirm",
         {"track_id": track_id, "code": sms_code, "phone": phone}),
        # Старый multi_step flow (fallback)
        (f"{_PASSPORT_HOST}/registration-validations/auth/multi_step/commit_password",
         None),  # None = form-encoded
    ]

    for endpoint, json_body in confirm_endpoints:
        try:
            if json_body is None:
                # Старый form-encoded формат
                payload = {"track_id": track_id, "code": sms_code}
                if csrf_token:
                    payload["sk"] = csrf_token
                if process_uuid:
                    payload["process_uuid"] = process_uuid
                resp = session.post(
                    endpoint,
                    data=payload,
                    headers={
                        **base_headers,
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    verify=False,
                    timeout=15,
                )
            else:
                resp = session.post(
                    endpoint,
                    json=json_body,
                    headers=base_headers,
                    verify=False,
                    timeout=15,
                )

            data = {}
            try:
                data = resp.json()
            except Exception:
                pass

            print(f"[lavka_auth] confirm {endpoint[-50:]}: {resp.status_code} {str(data)[:200]}")

            # Проверяем успех
            if resp.status_code == 200:
                # Новый flow — x_token или access_token
                x_token = (
                    data.get("x_token")
                    or data.get("access_token")
                    or (data.get("data") or {}).get("x_token")
                    or (data.get("data") or {}).get("access_token")
                )
                uid = str(data.get("uid", ""))

                # Старый flow — status: ok
                if data.get("status") == "ok":
                    x_token = data.get("x_token") or data.get("access_token") or x_token
                    uid = str(data.get("uid", uid))

                if x_token:
                    break

            last_error = (
                data.get("message")
                or data.get("error")
                or (
                    data.get("errors", [{}])[0].get("message", "")
                    if isinstance(data.get("errors"), list) else ""
                )
                or f"HTTP {resp.status_code}"
            )

        except Exception as e:
            last_error = str(e)
            continue

    if not x_token:
        return {
            "success": False,
            "access_token": "",
            "expires_in": 0,
            "uid": "",
            "error": f"Неверный код: {last_error}",
        }

    # Обмен x_token → OAuth access_token для Лавки
    device_id = uuid.uuid4().hex
    try:
        resp = session.post(
            f"{_OAUTH_HOST}/token",
            data={
                "grant_type": "x-token",
                "access_token": x_token,
                "client_id": _LAVKA_CLIENT_ID,
                "client_secret": _LAVKA_CLIENT_SECRET,
                "device_id": device_id,
                "device_name": "Android Pixel 7",
            },
            headers={
                "User-Agent": "ru.yandex.lavka/3.100.0 (Android 13; Pixel 7)",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            verify=False,
            timeout=15,
        )
        data = {}
        try:
            data = resp.json()
        except Exception:
            pass

        print(f"[lavka_auth] OAuth: {resp.status_code} {str(data)[:200]}")

        if "access_token" in data:
            access_token = data["access_token"]
            expires_in = data.get("expires_in", 31536000)
            token_uid = str(data.get("uid", uid))

            token_data = {
                "access_token": access_token,
                "expires_in": expires_in,
                "expires_at": time.time() + expires_in,
                "uid": token_uid,
                "phone": phone,
            }
            save_token(token_data)
            return {
                "success": True,
                "access_token": access_token,
                "expires_in": expires_in,
                "uid": token_uid,
                "error": None,
            }
        else:
            # OAuth не сработал — сохраняем x_token напрямую
            token_data = {
                "access_token": x_token,
                "expires_in": 86400,
                "expires_at": time.time() + 86400,
                "uid": uid,
                "phone": phone,
            }
            save_token(token_data)
            return {
                "success": True,
                "access_token": x_token,
                "expires_in": 86400,
                "uid": uid,
                "error": None,
            }

    except Exception as e:
        return {
            "success": False,
            "access_token": "",
            "expires_in": 0,
            "uid": "",
            "error": f"Ошибка OAuth: {str(e)}",
        }


if __name__ == "__main__":
    import sys

    print("=== Авторизация в Яндекс Лавке ===")
    phone = input("Введите номер телефона (+7XXXXXXXXXX): ").strip()

    print(f"Запрашиваем SMS на {phone}...")
    result = request_sms(phone)

    if not result["success"]:
        print(f"❌ Ошибка: {result['error']}")
        print(f"   track_id: {result.get('track_id', '')}")
        sys.exit(1)

    print(f"✅ SMS/push отправлен! track_id: {result['track_id']}")
    code = input("Введите код из SMS/push: ").strip()

    print("Подтверждаем код...")
    auth_result = confirm_sms(
        track_id=result["track_id"],
        sms_code=code,
        phone=phone,
        csrf_token=result.get("csrf_token", ""),
        cookies=result.get("cookies"),
        process_uuid=result.get("process_uuid", ""),
    )

    if auth_result["success"]:
        print(f"✅ Авторизация успешна!")
        print(f"   UID: {auth_result['uid']}")
        print(f"   Token: {auth_result['access_token'][:20]}...")
    else:
        print(f"❌ Ошибка авторизации: {auth_result['error']}")
