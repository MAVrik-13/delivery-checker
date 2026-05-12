"""
Парсер условий доставки Пятёрочки через Playwright.
Получает актуальные данные о доставке с сайта 5ka.ru.
"""

import asyncio
import re
from playwright.async_api import async_playwright
from typing import Optional


async def parse_pyaterochka_playwright(lat: float, lon: float) -> dict:
    """
    Парсит условия доставки Пятёрочки через Playwright.
    Получает актуальные данные с сайта 5ka.ru.
    """
    result = {
        "service": "Пятёрочка",
        "logo": "🔴",
        "available": False,
        "delivery_price": None,
        "min_order": None,
        "free_from": None,
        "delivery_time": None,
        "packaging_price": None,
        "assembly_price": None,
        "note": "",
        "error": None,
        "data_source": "playwright",
    }

    try:
        async with async_playwright() as p:
            # Запускаем браузер
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            # Переходим на страницу каталога с координатами
            url = f"https://5ka.ru/catalog/?lat={lat}&lon={lon}"
            await page.goto(url, timeout=30000)
            
            # Ждём загрузки страницы
            await page.wait_for_load_state("networkidle")
            
            # Получаем HTML страницы
            html = await page.content()
            
            # Ищем информацию о доставке в HTML
            # Паттерны для поиска данных о доставке
            patterns = [
                # Минимальная корзина
                (r'минимальная\s+заказ\s*:?\s*(\d+)\s*₽', 'min_order'),
                (r'мин\.?\s*заказ\s*:?\s*(\d+)\s*₽', 'min_order'),
                (r'от\s*(\d+)\s*₽\s*минимальный', 'min_order'),
                
                # Стоимость доставки
                (r'доставка\s*:?\s*(\d+)\s*₽', 'delivery_price'),
                (r'стоимость\s+доставки\s*:?\s*(\d+)\s*₽', 'delivery_price'),
                
                # Бесплатная доставка
                (r'бесплатная\s+доставка\s+от\s*:?\s*(\d+)\s*₽', 'free_from'),
                (r'бесплатно\s+от\s*:?\s*(\d+)\s*₽', 'free_from'),
                
                # Время доставки
                (r'доставка\s+за\s*:?\s*(\d+)\s*-\s*(\d+)\s*мин', 'delivery_time'),
                (r'время\s+доставки\s*:?\s*(\d+)\s*-\s*(\d+)\s*мин', 'delivery_time'),
            ]
            
            for pattern, field in patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    if field == 'delivery_time':
                        # Время доставки в формате "30-60 мин"
                        result[field] = f"{match.group(1)}-{match.group(2)} мин"
                    else:
                        # Числовые значения
                        result[field] = int(match.group(1))
            
            # Проверяем, доступна ли доставка
            if result.get('min_order') or result.get('delivery_price') is not None:
                result['available'] = True
            
            # Если не нашли данные, добавляем примечание
            if not result.get('min_order') and not result.get('delivery_price'):
                result['note'] = 'Не удалось получить актуальные условия доставки. Используются фиксированные значения.'
            else:
                result['note'] = 'Актуальные условия доставки получены с сайта 5ka.ru'
            
            # Закрываем браузер
            await browser.close()
            
    except Exception as e:
        result['error'] = str(e)
        result['note'] = f'Ошибка при парсинге: {str(e)}'
    
    return result


# Тестовая функция
async def test():
    """Тестовая функция для проверки парсера."""
    # Координаты для СПб Новгородская 17
    lat = 59.9390662
    lon = 30.3888356
    
    result = await parse_pyaterochka_playwright(lat, lon)
    print("=== Результат парсинга Пятёрочки ===")
    print(f"Доступность: {result['available']}")
    print(f"Минимальная корзина: {result['min_order']} ₽")
    print(f"Доставка: {result['delivery_price']} ₽")
    print(f"Бесплатная доставка от: {result['free_from']} ₽")
    print(f"Время доставки: {result['delivery_time']}")
    print(f"Примечание: {result['note']}")
    if result.get('error'):
        print(f"Ошибка: {result['error']}")


if __name__ == "__main__":
    asyncio.run(test())
