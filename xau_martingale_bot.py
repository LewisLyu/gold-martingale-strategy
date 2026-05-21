#!/usr/bin/env python3
"""
Defensive XAUUSDT martingale bot.

Default mode is dry-run. Live trading requires explicit --live plus API keys in
environment variables. The code never prints secrets.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path
from typing import Any


LIVE_BASE_URL = "https://fapi.binance.com"
TESTNET_BASE_URL = "https://testnet.binancefuture.com"


def https_context() -> ssl.SSLContext | None:
    cafile = os.getenv("SSL_CERT_FILE")
    if cafile and os.path.exists(cafile):
        return ssl.create_default_context(cafile=cafile)
    if os.path.exists("/etc/ssl/cert.pem"):
        return ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    return None


@dataclass
class Config:
    symbol: str = "XAUUSDT"
    equity: Decimal = Decimal("500")
    initial_notional: Decimal = Decimal("45")
    leverage: int = 35
    grid_drop: Decimal = Decimal("0.01")
    core_take_profit: Decimal = Decimal("0.01")
    max_adds: int = 8
    fee_rate: Decimal = Decimal("0.0005")
    slippage_rate: Decimal = Decimal("0.0002")
    max_orders_per_tick: int = 1
    auto_reopen: bool = False
    execution_mode: str = "market"
    preload_adds: int = 2


@dataclass
class Lot:
    kind: str
    level: int
    qty: Decimal
    entry_price: Decimal

    @property
    def cost(self) -> Decimal:
        return self.qty * self.entry_price


@dataclass
class PendingOrder:
    kind: str
    level: int
    order_id: str
    qty: Decimal
    price: Decimal


@dataclass
class CycleState:
    status: str = "idle"
    cycle_id: int = 0
    core_entry_price: Decimal | None = None
    core_qty: Decimal | None = None
    add_count: int = 0
    lots: list[Lot] = field(default_factory=list)
    pending_orders: list[PendingOrder] = field(default_factory=list)
    last_action: str = ""
    realized_pnl: Decimal = Decimal("0")


def d(value: Any) -> Decimal:
    return Decimal(str(value))


class DecimalJSONEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, Lot | PendingOrder):
            return asdict(obj)
        return super().default(obj)


def lot_from_dict(data: dict[str, Any]) -> Lot:
    return Lot(
        kind=data["kind"],
        level=int(data["level"]),
        qty=d(data["qty"]),
        entry_price=d(data["entry_price"]),
    )


def pending_order_from_dict(data: dict[str, Any]) -> PendingOrder:
    return PendingOrder(
        kind=data["kind"],
        level=int(data["level"]),
        order_id=str(data["order_id"]),
        qty=d(data["qty"]),
        price=d(data["price"]),
    )


def load_state(path: Path) -> CycleState:
    if not path.exists():
        return CycleState()
    data = json.loads(path.read_text())
    return CycleState(
        status=data.get("status", "idle"),
        cycle_id=int(data.get("cycle_id", 0)),
        core_entry_price=d(data["core_entry_price"])
        if data.get("core_entry_price") is not None
        else None,
        core_qty=d(data["core_qty"]) if data.get("core_qty") is not None else None,
        add_count=int(data.get("add_count", 0)),
        lots=[lot_from_dict(item) for item in data.get("lots", [])],
        pending_orders=[
            pending_order_from_dict(item) for item in data.get("pending_orders", [])
        ],
        last_action=data.get("last_action", ""),
        realized_pnl=d(data.get("realized_pnl", "0")),
    )


def save_state(path: Path, state: CycleState) -> None:
    path.write_text(json.dumps(asdict(state), cls=DecimalJSONEncoder, indent=2) + "\n")


def qfmt(value: Decimal, places: str = "0.01") -> str:
    return str(value.quantize(Decimal(places), rounding=ROUND_HALF_UP))


class BinanceFuturesClient:
    def __init__(
        self,
        live: bool,
        testnet: bool,
        api_key: str | None = None,
        api_secret: str | None = None,
    ) -> None:
        self.live = live
        self.base_url = TESTNET_BASE_URL if testnet else LIVE_BASE_URL
        self.api_key = api_key if api_key is not None else os.getenv("BINANCE_API_KEY", "")
        self.api_secret = (
            api_secret if api_secret is not None else os.getenv("BINANCE_API_SECRET", "")
        )

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        params = params or {}
        headers = {}
        if signed:
            if not self.api_key or not self.api_secret:
                raise RuntimeError("Missing BINANCE_API_KEY or BINANCE_API_SECRET")
            params["timestamp"] = int(time.time() * 1000)
            params.setdefault("recvWindow", 5000)
            query = urllib.parse.urlencode(params)
            signature = hmac.new(
                self.api_secret.encode(), query.encode(), hashlib.sha256
            ).hexdigest()
            query = f"{query}&signature={signature}"
            headers["X-MBX-APIKEY"] = self.api_key
        else:
            query = urllib.parse.urlencode(params)
        url = f"{self.base_url}{path}"
        body = None
        if method in {"GET", "DELETE"} and query:
            url = f"{url}?{query}"
        elif query:
            body = query.encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15, context=https_context()) as resp:
                payload = resp.read().decode()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"Binance API error {exc.code}: {detail}") from exc
        return json.loads(payload)

    def price(self, symbol: str) -> Decimal:
        data = self.request("GET", "/fapi/v1/ticker/price", {"symbol": symbol})
        return d(data["price"])

    def exchange_filters(self, symbol: str) -> dict[str, Decimal]:
        data = self.request("GET", "/fapi/v1/exchangeInfo", {"symbol": symbol})
        symbols = data.get("symbols", [])
        if not symbols:
            raise RuntimeError(f"Symbol not found: {symbol}")
        filters = symbols[0]["filters"]
        out = {
            "step_size": Decimal("0.001"),
            "min_qty": Decimal("0"),
            "tick_size": Decimal("0.01"),
        }
        for item in filters:
            if item["filterType"] == "LOT_SIZE":
                out["step_size"] = d(item["stepSize"])
                out["min_qty"] = d(item["minQty"])
            if item["filterType"] == "PRICE_FILTER":
                out["tick_size"] = d(item["tickSize"])
        return out

    def set_leverage(self, symbol: str, leverage: int) -> None:
        if not self.live:
            print(f"[DRY] set leverage {symbol} -> {leverage}x")
            return
        self.request(
            "POST",
            "/fapi/v1/leverage",
            {"symbol": symbol, "leverage": leverage},
            signed=True,
        )

    def market_order(
        self,
        symbol: str,
        side: str,
        qty: Decimal,
        reduce_only: bool = False,
    ) -> None:
        if not self.live:
            flag = " reduceOnly" if reduce_only else ""
            print(f"[DRY] MARKET {side} {symbol} qty={qty}{flag}")
            return
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": str(qty),
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        self.request("POST", "/fapi/v1/order", params, signed=True)


def round_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def add_qty_multiplier(add_level: int) -> Decimal:
    if add_level <= 1:
        return Decimal("1")
    return Decimal(2) ** Decimal(add_level - 1)


def price_to_order_text(price: Decimal) -> str:
    return format(price.normalize(), "f")


def add_breakeven_price(state: CycleState, cfg: Config) -> Decimal | None:
    add_lots = [lot for lot in state.lots if lot.kind == "add"]
    if not add_lots:
        return None
    qty = sum((lot.qty for lot in add_lots), Decimal("0"))
    cost = sum((lot.cost for lot in add_lots), Decimal("0"))
    friction = cfg.fee_rate + cfg.slippage_rate
    return (cost / qty) * (Decimal("1") + friction * Decimal("2"))


def core_take_profit_price(state: CycleState, cfg: Config) -> Decimal | None:
    if state.core_entry_price is None:
        return None
    friction = cfg.fee_rate + cfg.slippage_rate
    return state.core_entry_price * (
        Decimal("1") + cfg.core_take_profit + friction * Decimal("2")
    )


def stop_price(state: CycleState, cfg: Config) -> Decimal | None:
    if state.core_entry_price is None:
        return None
    return state.core_entry_price * (
        Decimal("1") - cfg.grid_drop
    ) ** Decimal(cfg.max_adds + 1)


def next_add_price(state: CycleState, cfg: Config) -> Decimal | None:
    if state.core_entry_price is None or state.add_count >= cfg.max_adds:
        return None
    return state.core_entry_price * (
        Decimal("1") - cfg.grid_drop
    ) ** Decimal(state.add_count + 1)


def position_value(state: CycleState, price: Decimal) -> Decimal:
    return sum((lot.qty for lot in state.lots), Decimal("0")) * price


def invested_cost(state: CycleState) -> Decimal:
    return sum((lot.cost for lot in state.lots), Decimal("0"))


def floating_pnl(state: CycleState, price: Decimal) -> Decimal:
    return position_value(state, price) - invested_cost(state)


def open_core(
    client: BinanceFuturesClient,
    state: CycleState,
    cfg: Config,
    price: Decimal,
    step_size: Decimal,
) -> str:
    qty = round_step(cfg.initial_notional / price, step_size)
    if qty <= 0:
        raise RuntimeError("Initial quantity rounded to zero")
    client.set_leverage(cfg.symbol, cfg.leverage)
    client.market_order(cfg.symbol, "BUY", qty)
    state.status = "active"
    state.cycle_id += 1
    state.core_entry_price = price
    state.core_qty = qty
    state.add_count = 0
    state.lots = [Lot("core", 0, qty, price)]
    state.last_action = f"opened core qty={qty} price={price}"
    return state.last_action


def add_position(
    client: BinanceFuturesClient,
    state: CycleState,
    cfg: Config,
    price: Decimal,
    step_size: Decimal,
) -> str:
    if state.core_qty is None:
        raise RuntimeError("Cannot add before core is opened")
    level = state.add_count + 1
    qty = round_step(state.core_qty * add_qty_multiplier(level), step_size)
    if qty <= 0:
        raise RuntimeError("Add quantity rounded to zero")
    client.market_order(cfg.symbol, "BUY", qty)
    state.add_count = level
    state.lots.append(Lot("add", level, qty, price))
    state.last_action = f"add {level} qty={qty} price={price}"
    return state.last_action


def close_adds(
    client: BinanceFuturesClient,
    state: CycleState,
    cfg: Config,
    price: Decimal,
    step_size: Decimal,
) -> str:
    add_lots = [lot for lot in state.lots if lot.kind == "add"]
    qty = round_step(sum((lot.qty for lot in add_lots), Decimal("0")), step_size)
    if qty <= 0:
        return "no add lots to close"
    cost = sum((lot.cost for lot in add_lots), Decimal("0"))
    client.market_order(cfg.symbol, "SELL", qty, reduce_only=True)
    pnl = qty * price - cost
    state.realized_pnl += pnl
    state.lots = [lot for lot in state.lots if lot.kind != "add"]
    state.add_count = 0
    state.last_action = f"trimmed adds qty={qty} price={price} pnl={qfmt(pnl)}"
    return state.last_action


def close_all(
    client: BinanceFuturesClient,
    state: CycleState,
    cfg: Config,
    price: Decimal,
    step_size: Decimal,
    reason: str,
) -> str:
    qty = round_step(sum((lot.qty for lot in state.lots), Decimal("0")), step_size)
    if qty > 0:
        client.market_order(cfg.symbol, "SELL", qty, reduce_only=True)
    pnl = qty * price - invested_cost(state)
    state.realized_pnl += pnl
    state.status = "idle"
    state.core_entry_price = None
    state.core_qty = None
    state.add_count = 0
    state.lots = []
    state.last_action = f"closed all reason={reason} qty={qty} price={price} pnl={qfmt(pnl)}"
    return state.last_action


def place_limit_order(
    client: Any,
    state: CycleState,
    cfg: Config,
    kind: str,
    level: int,
    side: str,
    qty: Decimal,
    price: Decimal,
    reduce_only: bool = False,
) -> str:
    limit_order = getattr(client, "limit_order", None)
    if callable(limit_order):
        order_id = limit_order(cfg.symbol, side, qty, price, reduce_only=reduce_only)
    elif getattr(client, "live", False):
        raise RuntimeError("当前交易所适配器还不支持限价托管单。")
    else:
        flag = " reduceOnly" if reduce_only else ""
        order_id = f"dry-{kind}-{state.cycle_id}-{level}-{time.time_ns()}"
        print(
            f"[DRY] LIMIT {side} {cfg.symbol} qty={qty} price={price_to_order_text(price)}{flag}"
        )
    state.pending_orders.append(PendingOrder(kind, level, order_id, qty, price))
    return order_id


def cancel_pending_orders(
    client: Any,
    state: CycleState,
    cfg: Config,
    kinds: set[str] | None = None,
) -> list[str]:
    remaining: list[PendingOrder] = []
    actions: list[str] = []
    cancel_order = getattr(client, "cancel_order", None)
    for order in state.pending_orders:
        if kinds is not None and order.kind not in kinds:
            remaining.append(order)
            continue
        if callable(cancel_order):
            cancel_order(cfg.symbol, order.order_id)
        elif getattr(client, "live", False):
            raise RuntimeError("当前交易所适配器还不支持撤销托管单。")
        actions.append(f"cancel {order.kind} order level={order.level}")
    state.pending_orders = remaining
    return actions


def pending_filled(client: Any, cfg: Config, order: PendingOrder, price: Decimal) -> bool:
    order_status = getattr(client, "order_status", None)
    if callable(order_status) and getattr(client, "live", False):
        status = order_status(cfg.symbol, order.order_id)
        return str(status.get("state", "")).lower() == "filled"
    if order.kind == "add":
        return price <= order.price
    return price >= order.price


def ensure_add_orders(
    client: Any,
    state: CycleState,
    cfg: Config,
    step_size: Decimal,
) -> list[str]:
    if state.core_entry_price is None or state.core_qty is None:
        return []
    actions: list[str] = []
    wanted_levels: list[int] = []
    for level in range(state.add_count + 1, cfg.max_adds + 1):
        if len(wanted_levels) >= max(1, cfg.preload_adds):
            break
        wanted_levels.append(level)
    live_levels = {order.level for order in state.pending_orders if order.kind == "add"}
    for level in wanted_levels:
        if level in live_levels:
            continue
        qty = round_step(state.core_qty * add_qty_multiplier(level), step_size)
        if qty <= 0:
            raise RuntimeError("Add quantity rounded to zero")
        price = state.core_entry_price * (Decimal("1") - cfg.grid_drop) ** Decimal(level)
        order_id = place_limit_order(client, state, cfg, "add", level, "BUY", qty, price)
        actions.append(f"placed add limit level={level} qty={qty} price={qfmt(price, '0.0001')} id={order_id}")
    return actions


def ensure_trim_order(
    client: Any,
    state: CycleState,
    cfg: Config,
    step_size: Decimal,
) -> list[str]:
    add_lots = [lot for lot in state.lots if lot.kind == "add"]
    if not add_lots:
        return []
    if any(order.kind == "trim" for order in state.pending_orders):
        return []
    be = add_breakeven_price(state, cfg)
    if be is None:
        return []
    qty = round_step(sum((lot.qty for lot in add_lots), Decimal("0")), step_size)
    if qty <= 0:
        return []
    order_id = place_limit_order(client, state, cfg, "trim", state.add_count, "SELL", qty, be, reduce_only=True)
    return [f"placed trim reduce-only qty={qty} price={qfmt(be, '0.0001')} id={order_id}"]


def ensure_core_tp_order(
    client: Any,
    state: CycleState,
    cfg: Config,
    step_size: Decimal,
) -> list[str]:
    if any(lot.kind == "add" for lot in state.lots):
        return []
    if any(order.kind == "core_tp" for order in state.pending_orders):
        return []
    tp = core_take_profit_price(state, cfg)
    if tp is None or state.core_qty is None:
        return []
    qty = round_step(state.core_qty, step_size)
    if qty <= 0:
        return []
    order_id = place_limit_order(client, state, cfg, "core_tp", 0, "SELL", qty, tp, reduce_only=True)
    return [f"placed core tp reduce-only qty={qty} price={qfmt(tp, '0.0001')} id={order_id}"]


def apply_filled_order(
    client: Any,
    state: CycleState,
    cfg: Config,
    order: PendingOrder,
    step_size: Decimal,
) -> list[str]:
    actions: list[str] = []
    if order.kind == "add":
        state.add_count = max(state.add_count, order.level)
        state.lots.append(Lot("add", order.level, order.qty, order.price))
        actions.append(f"filled add level={order.level} qty={order.qty} price={qfmt(order.price, '0.0001')}")
        actions.extend(cancel_pending_orders(client, state, cfg, {"trim", "core_tp"}))
        actions.extend(ensure_trim_order(client, state, cfg, step_size))
        actions.extend(ensure_add_orders(client, state, cfg, step_size))
        return actions
    if order.kind == "trim":
        add_lots = [lot for lot in state.lots if lot.kind == "add"]
        cost = sum((lot.cost for lot in add_lots), Decimal("0"))
        qty = sum((lot.qty for lot in add_lots), Decimal("0"))
        pnl = qty * order.price - cost
        state.realized_pnl += pnl
        state.lots = [lot for lot in state.lots if lot.kind != "add"]
        state.add_count = 0
        actions.append(f"filled trim qty={qfmt(qty, '0.000001')} price={qfmt(order.price, '0.0001')} pnl={qfmt(pnl)}")
        actions.extend(cancel_pending_orders(client, state, cfg, {"add"}))
        actions.extend(ensure_core_tp_order(client, state, cfg, step_size))
        return actions
    if order.kind == "core_tp":
        pnl = order.qty * order.price - invested_cost(state)
        state.realized_pnl += pnl
        state.status = "idle"
        state.core_entry_price = None
        state.core_qty = None
        state.add_count = 0
        state.lots = []
        actions.append(f"filled core tp qty={order.qty} price={qfmt(order.price, '0.0001')} pnl={qfmt(pnl)}")
        actions.extend(cancel_pending_orders(client, state, cfg))
        return actions
    return actions


def handle_limit_tick(
    client: Any,
    state: CycleState,
    cfg: Config,
    price: Decimal,
    step_size: Decimal,
) -> list[str]:
    actions: list[str] = []
    if state.status == "idle":
        actions.append(open_core(client, state, cfg, price, step_size))
        actions.extend(ensure_add_orders(client, state, cfg, step_size))
        actions.extend(ensure_core_tp_order(client, state, cfg, step_size))
        return actions

    for order in list(state.pending_orders):
        if not pending_filled(client, cfg, order, price):
            continue
        state.pending_orders = [
            item for item in state.pending_orders if item.order_id != order.order_id
        ]
        actions.extend(apply_filled_order(client, state, cfg, order, step_size))

    if state.status == "idle":
        if cfg.auto_reopen:
            actions.append(open_core(client, state, cfg, price, step_size))
            actions.extend(ensure_add_orders(client, state, cfg, step_size))
            actions.extend(ensure_core_tp_order(client, state, cfg, step_size))
        return actions

    sp = stop_price(state, cfg)
    if state.add_count >= cfg.max_adds and sp is not None and price <= sp:
        actions.extend(cancel_pending_orders(client, state, cfg))
        actions.append(close_all(client, state, cfg, price, step_size, "max-add-stop"))
        if cfg.auto_reopen:
            actions.append(open_core(client, state, cfg, price, step_size))
            actions.extend(ensure_add_orders(client, state, cfg, step_size))
            actions.extend(ensure_core_tp_order(client, state, cfg, step_size))
        return actions

    actions.extend(ensure_trim_order(client, state, cfg, step_size))
    if any(lot.kind == "add" for lot in state.lots):
        actions.extend(ensure_add_orders(client, state, cfg, step_size))
    else:
        actions.extend(ensure_core_tp_order(client, state, cfg, step_size))
        actions.extend(ensure_add_orders(client, state, cfg, step_size))
    if not actions:
        state.last_action = "hold"
        actions.append("hold")
    else:
        state.last_action = " / ".join(actions[-3:])
    return actions


def handle_market_tick(
    client: BinanceFuturesClient,
    state: CycleState,
    cfg: Config,
    price: Decimal,
    step_size: Decimal,
) -> list[str]:
    actions: list[str] = []
    if state.status == "idle":
        actions.append(open_core(client, state, cfg, price, step_size))
        return actions

    sp = stop_price(state, cfg)
    if state.add_count >= cfg.max_adds and sp is not None and price <= sp:
        actions.append(close_all(client, state, cfg, price, step_size, "max-add-stop"))
        if cfg.auto_reopen:
            actions.append(open_core(client, state, cfg, price, step_size))
        return actions

    be = add_breakeven_price(state, cfg)
    if be is not None and price >= be:
        actions.append(close_adds(client, state, cfg, price, step_size))
        return actions

    if not any(lot.kind == "add" for lot in state.lots):
        tp = core_take_profit_price(state, cfg)
        if tp is not None and price >= tp:
            actions.append(close_all(client, state, cfg, price, step_size, "core-take-profit"))
            if cfg.auto_reopen:
                actions.append(open_core(client, state, cfg, price, step_size))
            return actions

    orders = 0
    while orders < cfg.max_orders_per_tick:
        trigger = next_add_price(state, cfg)
        if trigger is None or price > trigger:
            break
        actions.append(add_position(client, state, cfg, price, step_size))
        orders += 1
    if not actions:
        state.last_action = "hold"
        actions.append("hold")
    return actions


def handle_tick(
    client: BinanceFuturesClient,
    state: CycleState,
    cfg: Config,
    price: Decimal,
    step_size: Decimal,
) -> list[str]:
    if cfg.execution_mode == "limit":
        return handle_limit_tick(client, state, cfg, price, step_size)
    return handle_market_tick(client, state, cfg, price, step_size)


def print_status(state: CycleState, cfg: Config, price: Decimal) -> None:
    invested = invested_cost(state)
    value = position_value(state, price)
    pnl = floating_pnl(state, price)
    margin = invested / Decimal(cfg.leverage) if invested else Decimal("0")
    print(f"symbol={cfg.symbol} price={price}")
    print(f"status={state.status} cycle={state.cycle_id} add_count={state.add_count}")
    print(f"invested={qfmt(invested)} value={qfmt(value)} floating_pnl={qfmt(pnl)}")
    print(f"margin@{cfg.leverage}x={qfmt(margin)} pressure={qfmt(margin - pnl)}")
    be = add_breakeven_price(state, cfg)
    tp = core_take_profit_price(state, cfg)
    sp = stop_price(state, cfg)
    na = next_add_price(state, cfg)
    if na:
        print(f"next_add_price={qfmt(na, '0.0001')}")
    if be:
        print(f"add_breakeven_trim_price={qfmt(be, '0.0001')}")
    if tp and not any(lot.kind == "add" for lot in state.lots):
        print(f"core_take_profit_price={qfmt(tp, '0.0001')}")
    if sp:
        print(f"max_add_stop_price={qfmt(sp, '0.0001')}")
    print(f"realized_pnl={qfmt(state.realized_pnl)} last_action={state.last_action}")


def print_ladder(cfg: Config) -> None:
    r = Decimal("1") - cfg.grid_drop
    cumulative = Decimal("0")
    qty_mult_total = Decimal("0")
    print("level,drawdown,buy_notional,cumulative_notional,current_value,floating_loss")
    for level in range(cfg.max_adds + 1):
        mult = Decimal("1") if level == 0 else add_qty_multiplier(level)
        price_ratio = r ** Decimal(level)
        buy = cfg.initial_notional * mult * price_ratio
        cumulative += buy
        qty_mult_total += mult
        current_value = cfg.initial_notional * qty_mult_total * price_ratio
        loss = cumulative - current_value
        name = "core" if level == 0 else f"add{level}"
        print(
            f"{name},{level}%,{qfmt(buy)},{qfmt(cumulative)},"
            f"{qfmt(current_value)},{qfmt(loss)}"
        )
    stop_ratio = r ** Decimal(cfg.max_adds + 1)
    stop_value = cfg.initial_notional * qty_mult_total * stop_ratio
    stop_loss = cumulative - stop_value
    print(f"stop_after_next_1pct_loss={qfmt(stop_loss)}")
    print(f"margin@{cfg.leverage}x={qfmt(cumulative / Decimal(cfg.leverage))}")


def build_config(args: argparse.Namespace) -> Config:
    initial = (
        d(args.initial_notional)
        if args.initial_notional is not None
        else Decimal("45") * d(args.equity) / Decimal("500")
    )
    return Config(
        symbol=args.symbol,
        equity=d(args.equity),
        initial_notional=initial,
        leverage=args.leverage,
        grid_drop=d(args.grid_drop),
        core_take_profit=d(args.core_take_profit),
        max_adds=args.max_adds,
        fee_rate=d(args.fee_rate),
        slippage_rate=d(args.slippage_rate),
        max_orders_per_tick=args.max_orders_per_tick,
        auto_reopen=args.auto_reopen,
        execution_mode=args.execution_mode,
        preload_adds=args.preload_adds,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="XAUUSDT defensive martingale bot")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--symbol", default=os.getenv("SYMBOL", "XAUUSDT"))
        p.add_argument("--equity", default=os.getenv("EQUITY", "500"))
        p.add_argument("--initial-notional", default=os.getenv("INITIAL_NOTIONAL"))
        p.add_argument("--leverage", type=int, default=int(os.getenv("LEVERAGE", "35")))
        p.add_argument("--grid-drop", default=os.getenv("GRID_DROP", "0.01"))
        p.add_argument("--core-take-profit", default=os.getenv("CORE_TAKE_PROFIT", "0.01"))
        p.add_argument("--max-adds", type=int, default=int(os.getenv("MAX_ADDS", "8")))
        p.add_argument("--fee-rate", default=os.getenv("FEE_RATE", "0.0005"))
        p.add_argument("--slippage-rate", default=os.getenv("SLIPPAGE_RATE", "0.0002"))
        p.add_argument(
            "--max-orders-per-tick",
            type=int,
            default=int(os.getenv("MAX_ORDERS_PER_TICK", "1")),
        )
        p.add_argument("--auto-reopen", action="store_true")
        p.add_argument(
            "--execution-mode",
            choices=["market", "limit"],
            default=os.getenv("EXECUTION_MODE", "market"),
        )
        p.add_argument(
            "--preload-adds",
            type=int,
            default=int(os.getenv("PRELOAD_ADDS", "2")),
        )

    table = sub.add_parser("table", help="print the USDT ladder")
    common(table)

    tick = sub.add_parser("tick", help="process one price tick")
    common(tick)
    tick.add_argument("--price", help="manual price; if omitted, fetch public price")
    tick.add_argument("--state", default=os.getenv("STATE_FILE", "xau_bot_state.json"))
    tick.add_argument("--live", action="store_true", help="place real orders")
    tick.add_argument("--testnet", action="store_true", help="use futures testnet URL")
    tick.add_argument("--step-size", default=os.getenv("STEP_SIZE"))

    loop = sub.add_parser("loop", help="poll price and process ticks")
    common(loop)
    loop.add_argument("--state", default=os.getenv("STATE_FILE", "xau_bot_state.json"))
    loop.add_argument("--live", action="store_true", help="place real orders")
    loop.add_argument("--testnet", action="store_true", help="use futures testnet URL")
    loop.add_argument("--interval", type=int, default=int(os.getenv("POLL_INTERVAL", "15")))
    loop.add_argument("--step-size", default=os.getenv("STEP_SIZE"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cfg = build_config(args)
    if args.command == "table":
        print_ladder(cfg)
        return 0

    client = BinanceFuturesClient(live=args.live, testnet=args.testnet)
    filters = {"step_size": d(args.step_size)} if args.step_size else client.exchange_filters(cfg.symbol)
    step_size = filters["step_size"]
    state_path = Path(args.state)

    while True:
        state = load_state(state_path)
        price = d(args.price) if args.command == "tick" and args.price else client.price(cfg.symbol)
        actions = handle_tick(client, state, cfg, price, step_size)
        save_state(state_path, state)
        print("\n".join(f"action={item}" for item in actions))
        print_status(state, cfg, price)
        if args.command == "tick":
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("stopped")
        raise SystemExit(130)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
