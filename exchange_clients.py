"""Exchange client adapters for the strategy web console."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, ROUND_DOWN
from typing import Any, Protocol

from xau_martingale_bot import BinanceFuturesClient


OKX_BASE_URL = "https://www.okx.com"


def https_context() -> ssl.SSLContext | None:
    cafile = os.getenv("SSL_CERT_FILE")
    if cafile and os.path.exists(cafile):
        return ssl.create_default_context(cafile=cafile)
    if os.path.exists("/etc/ssl/cert.pem"):
        return ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return None


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
        "status": "live",
        "needsPassphrase": True,
        "defaultSymbol": "XAU-USDT-SWAP",
        "note": "已接入 OKX v5 SWAP/现货行情、杠杆和市价单；实盘前请先小金额测试。",
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


def decimal_round_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


class OkxClient:
    def __init__(
        self,
        live: bool,
        testnet: bool,
        api_key: str | None = None,
        api_secret: str | None = None,
        passphrase: str | None = None,
    ) -> None:
        self.live = live
        self.testnet = testnet
        self.base_url = (os.getenv("OKX_BASE_URL") or OKX_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("OKX_API_KEY", "")
        self.api_secret = (
            api_secret if api_secret is not None else os.getenv("OKX_API_SECRET", "")
        )
        self.passphrase = (
            passphrase if passphrase is not None else os.getenv("OKX_PASSPHRASE", "")
        )
        self.margin_mode = os.getenv("OKX_MARGIN_MODE", "cross")
        self.pos_side = os.getenv("OKX_POS_SIDE", "")
        self._instrument_cache: dict[str, dict[str, Any]] = {}

    def timestamp(self) -> str:
        return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def sign(self, timestamp: str, method: str, request_path: str, body: str) -> str:
        message = f"{timestamp}{method.upper()}{request_path}{body}"
        digest = hmac.new(
            self.api_secret.encode(),
            message.encode(),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode()

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        method = method.upper()
        params = params or {}
        query = urllib.parse.urlencode(params) if method == "GET" and params else ""
        request_path = f"{path}?{query}" if query else path
        body = ""
        data = None
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 quant-trading-console/1.0",
        }
        if method != "GET" and params:
            body = json.dumps(params, separators=(",", ":"))
            data = body.encode()
        if signed:
            if not self.api_key or not self.api_secret or not self.passphrase:
                raise RuntimeError("Missing OKX_API_KEY, OKX_API_SECRET, or OKX_PASSPHRASE")
            ts = self.timestamp()
            headers.update(
                {
                    "OK-ACCESS-KEY": self.api_key,
                    "OK-ACCESS-SIGN": self.sign(ts, method, request_path, body),
                    "OK-ACCESS-TIMESTAMP": ts,
                    "OK-ACCESS-PASSPHRASE": self.passphrase,
                }
            )
            if self.testnet:
                headers["x-simulated-trading"] = "1"
        req = urllib.request.Request(
            f"{self.base_url}{request_path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=15, context=https_context()) as resp:
                payload = resp.read().decode()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"OKX API error {exc.code}: {detail}") from exc
        result = json.loads(payload)
        if str(result.get("code", "0")) != "0":
            raise RuntimeError(f"OKX API error {result.get('code')}: {result.get('msg')}")
        return result

    def inst_type(self, symbol: str) -> str:
        if symbol.endswith("-SWAP"):
            return "SWAP"
        parts = symbol.split("-")
        if len(parts) >= 3:
            return "FUTURES"
        return "SPOT"

    def instrument(self, symbol: str) -> dict[str, Any]:
        cached = self._instrument_cache.get(symbol)
        if cached:
            return cached
        data = self.request(
            "GET",
            "/api/v5/public/instruments",
            {"instType": self.inst_type(symbol), "instId": symbol},
        )
        rows = data.get("data") or []
        if not rows:
            raise RuntimeError(f"OKX instrument not found: {symbol}")
        self._instrument_cache[symbol] = rows[0]
        return rows[0]

    def order_size(self, symbol: str, base_qty: Decimal) -> str:
        inst = self.instrument(symbol)
        inst_type = inst.get("instType", self.inst_type(symbol))
        lot_size = Decimal(str(inst.get("lotSz") or "0.00000001"))
        min_size = Decimal(str(inst.get("minSz") or "0"))
        if inst_type in {"SWAP", "FUTURES", "OPTION"}:
            contract_value = Decimal(str(inst.get("ctVal") or "1"))
            size = decimal_round_step(base_qty / contract_value, lot_size)
        else:
            size = decimal_round_step(base_qty, lot_size)
        if size <= 0 or (min_size > 0 and size < min_size):
            raise RuntimeError(f"OKX order size too small: {size}, min={min_size}")
        return format(size.normalize(), "f")

    def price(self, symbol: str) -> Decimal:
        data = self.request("GET", "/api/v5/market/ticker", {"instId": symbol})
        rows = data.get("data") or []
        if not rows:
            raise RuntimeError(f"OKX ticker not found: {symbol}")
        return Decimal(str(rows[0]["last"]))

    def set_leverage(self, symbol: str, leverage: int) -> None:
        if not self.live:
            print(f"[DRY:okx] set leverage {symbol} -> {leverage}x")
            return
        if self.inst_type(symbol) == "SPOT":
            return
        body: dict[str, Any] = {
            "instId": symbol,
            "lever": str(leverage),
            "mgnMode": self.margin_mode,
        }
        if self.pos_side:
            body["posSide"] = self.pos_side
        self.request("POST", "/api/v5/account/set-leverage", body, signed=True)

    def market_order(
        self,
        symbol: str,
        side: str,
        qty: Decimal,
        reduce_only: bool = False,
    ) -> None:
        okx_side = "buy" if side.upper() == "BUY" else "sell"
        size = self.order_size(symbol, qty)
        if not self.live:
            flag = " reduceOnly" if reduce_only else ""
            print(f"[DRY:okx] MARKET {okx_side} {symbol} sz={size}{flag}")
            return
        body: dict[str, Any] = {
            "instId": symbol,
            "tdMode": "cash" if self.inst_type(symbol) == "SPOT" else self.margin_mode,
            "side": okx_side,
            "ordType": "market",
            "sz": size,
        }
        if self.pos_side:
            body["posSide"] = self.pos_side
        if reduce_only:
            body["reduceOnly"] = "true"
        self.request("POST", "/api/v5/trade/order", body, signed=True)


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
    if exchange == "okx":
        return OkxClient(
            live=live,
            testnet=testnet,
            api_key=credential.api_key if credential else None,
            api_secret=credential.api_secret if credential else None,
            passphrase=credential.passphrase if credential else None,
        )
    return UnsupportedExchangeClient(exchange=exchange, live=live)


def exchange_catalog() -> list[dict[str, Any]]:
    return [
        {"id": exchange_id, **metadata}
        for exchange_id, metadata in SUPPORTED_EXCHANGES.items()
    ]
