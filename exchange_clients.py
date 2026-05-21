"""Exchange client adapters for the strategy web console."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from xau_martingale_bot import BinanceFuturesClient


SUPPORTED_EXCHANGES = {
    "binance": {
        "name": "Binance",
        "status": "live",
        "needsPassphrase": False,
        "defaultSymbol": "XAUUSDT",
        "note": "已接入 USD-M Futures 实盘/测试网。",
    },
    "okx": {
        "name": "OKX",
        "status": "adapter-required",
        "needsPassphrase": True,
        "defaultSymbol": "XAU-USDT-SWAP",
        "note": "可绑定密钥；真实行情和下单需要补 OKX 适配器。",
    },
    "bybit": {
        "name": "Bybit",
        "status": "adapter-required",
        "needsPassphrase": False,
        "defaultSymbol": "XAUUSDT",
        "note": "可绑定密钥；真实行情和下单需要补 Bybit 适配器。",
    },
    "bitget": {
        "name": "Bitget",
        "status": "adapter-required",
        "needsPassphrase": True,
        "defaultSymbol": "XAUUSDT",
        "note": "可绑定密钥；真实行情和下单需要补 Bitget 适配器。",
    },
    "gate": {
        "name": "Gate.io",
        "status": "adapter-required",
        "needsPassphrase": False,
        "defaultSymbol": "XAU_USDT",
        "note": "可绑定密钥；真实行情和下单需要补 Gate 适配器。",
    },
    "custom": {
        "name": "自定义交易所",
        "status": "adapter-required",
        "needsPassphrase": True,
        "defaultSymbol": "XAUUSDT",
        "note": "用于客户登记 API；需要按交易所 API 文档补适配器。",
    },
}


@dataclass
class ExchangeCredential:
    exchange: str
    api_key: str
    api_secret: str
    passphrase: str = ""


class ExchangeClient(Protocol):
    live: bool

    def price(self, symbol: str) -> Decimal:
        ...

    def set_leverage(self, symbol: str, leverage: int) -> None:
        ...

    def market_order(
        self, symbol: str, side: str, qty: Decimal, reduce_only: bool = False
    ) -> None:
        ...


class UnsupportedExchangeClient:
    def __init__(self, exchange: str, live: bool) -> None:
        self.exchange = exchange
        self.live = live

    def _raise(self) -> None:
        name = SUPPORTED_EXCHANGES.get(self.exchange, {}).get("name", self.exchange)
        raise RuntimeError(f"{name} 已支持绑定，但还没有接入真实行情和下单适配器。")

    def price(self, symbol: str) -> Decimal:
        self._raise()

    def set_leverage(self, symbol: str, leverage: int) -> None:
        if self.live:
            self._raise()
        print(f"[DRY:{self.exchange}] set leverage {symbol} -> {leverage}x")

    def market_order(
        self, symbol: str, side: str, qty: Decimal, reduce_only: bool = False
    ) -> None:
        if self.live:
            self._raise()
        flag = " reduceOnly" if reduce_only else ""
        print(f"[DRY:{self.exchange}] MARKET {side} {symbol} qty={qty}{flag}")


def create_exchange_client(
    credential: ExchangeCredential | None,
    live: bool,
    testnet: bool,
) -> ExchangeClient:
    exchange = credential.exchange if credential else "binance"
    if exchange == "binance":
        return BinanceFuturesClient(
            live=live,
            testnet=testnet,
            api_key=credential.api_key if credential else None,
            api_secret=credential.api_secret if credential else None,
        )
    return UnsupportedExchangeClient(exchange=exchange, live=live)


def exchange_catalog() -> list[dict[str, Any]]:
    return [
        {"id": exchange_id, **metadata}
        for exchange_id, metadata in SUPPORTED_EXCHANGES.items()
    ]
