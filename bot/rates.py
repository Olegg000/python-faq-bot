"""Курсы валют: единый интерфейс + две реализации (демо и реальный API)."""

from typing import Protocol

CURRENCIES = ("USD", "EUR", "CNY")


class RateProvider(Protocol):
    async def get_rates(self) -> dict[str, float]:
        """Возвращает курсы валют к рублю, например {"USD": 92.5}."""
        ...


class DemoRateProvider:
    """Фиксированные курсы — для демонстрации и тестов, без сети."""

    async def get_rates(self) -> dict[str, float]:
        return {"USD": 92.50, "EUR": 100.20, "CNY": 12.70}


class ExchangeRateProvider:
    """Реальные курсы через open.er-api.com (бесплатный, без ключа)."""

    URL = "https://open.er-api.com/v6/latest/RUB"

    async def get_rates(self) -> dict[str, float]:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(self.URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                data = await resp.json()
        return self.parse(data)

    @staticmethod
    def parse(data: dict) -> dict[str, float]:
        # API отдаёт курсы "рубль -> валюта", нам нужен обратный курс
        rates = data["rates"]
        return {cur: round(1 / rates[cur], 2) for cur in CURRENCIES}


def get_provider(name: str) -> RateProvider:
    if name == "exchangerate":
        return ExchangeRateProvider()
    return DemoRateProvider()
