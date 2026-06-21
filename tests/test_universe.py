from crypto_trader.config import UniverseConfig
from crypto_trader.domain import Market
from crypto_trader.universe import is_eligible


CONFIG = UniverseConfig(30, 10_000_000, 0.003, frozenset({"USDC"}))


def market(**changes) -> Market:
    values = dict(
        symbol="ALTUSDT",
        base_asset="ALT",
        quote_asset="USDT",
        listing_days=60,
        quote_volume_24h=20_000_000,
        bid=100,
        ask=100.1,
    )
    values.update(changes)
    return Market(**values)


def test_eligible_market() -> None:
    assert is_eligible(market(), CONFIG)


def test_rejects_new_or_illiquid_market() -> None:
    assert not is_eligible(market(listing_days=29), CONFIG)
    assert not is_eligible(market(quote_volume_24h=9_999_999), CONFIG)


def test_rejects_wide_spread_and_stablecoin() -> None:
    assert not is_eligible(market(bid=100, ask=101), CONFIG)
    assert not is_eligible(market(base_asset="USDC"), CONFIG)


def test_intraday_range_ratio() -> None:
    value = market(high_24h=110, low_24h=90).intraday_range_ratio
    assert value == 0.2
