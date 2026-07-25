from bot.rates import CURRENCIES, DemoRateProvider, ExchangeRateProvider, get_provider


async def test_demo_provider_returns_all_currencies():
    rates = await DemoRateProvider().get_rates()
    assert set(rates) == set(CURRENCIES)
    assert all(isinstance(v, float) and v > 0 for v in rates.values())


def test_exchangerate_parse_inverts_rates():
    data = {"rates": {"USD": 0.0108, "EUR": 0.0100, "CNY": 0.0787, "GBP": 0.0085}}
    rates = ExchangeRateProvider.parse(data)
    assert set(rates) == set(CURRENCIES)
    assert rates["USD"] == round(1 / 0.0108, 2)
    assert rates["EUR"] == 100.0


def test_get_provider_selects_by_name():
    assert isinstance(get_provider("demo"), DemoRateProvider)
    assert isinstance(get_provider("exchangerate"), ExchangeRateProvider)
    assert isinstance(get_provider("unknown"), DemoRateProvider)
