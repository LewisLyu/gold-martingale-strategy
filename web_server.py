#!/usr/bin/env python3
"""Local web console for the Golden Conservative Martingale strategy."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import urllib.parse
from dataclasses import asdict
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from exchange_clients import (
    ExchangeCredential,
    create_exchange_client,
    exchange_catalog,
)
from xau_martingale_bot import (
    Config,
    CycleState,
    DecimalJSONEncoder,
    add_breakeven_price,
    build_config,
    core_take_profit_price,
    d,
    floating_pnl,
    handle_tick,
    invested_cost,
    load_state,
    position_value,
    qfmt,
    save_state,
    stop_price,
)


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "web"
STATE_FILE = ROOT / "xau_bot_state.json"
STATE_DIR = ROOT / "runtime_state"
SESSION_COOKIE = "gold_strategy_session"


class StrategyRunner:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.client: Any = None
        self.cfg = Config()
        self.exchange = "binance"
        self.step_size = Decimal("0.001")
        self.interval = 15
        self.running = False
        self.live = False
        self.testnet = False
        self.last_price: Decimal | None = None
        self.last_error = ""
        self.last_actions: list[str] = []
        self.api_key = ""
        self.api_secret = ""
        self.passphrase = ""

    @property
    def state_file(self) -> Path:
        STATE_DIR.mkdir(exist_ok=True)
        return STATE_DIR / f"{self.session_id}.json"

    def configure(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.exchange = str(payload.get("exchange", self.exchange or "binance"))
            self.cfg = Config(
                symbol=str(payload.get("symbol", "XAUUSDT")).upper(),
                equity=d(payload.get("equity", "500")),
                initial_notional=d(payload.get("initialNotional", "45")),
                leverage=int(payload.get("leverage", 35)),
                grid_drop=d(payload.get("gridDrop", "0.01")),
                core_take_profit=d(payload.get("coreTakeProfit", "0.01")),
                max_adds=int(payload.get("maxAdds", 8)),
                fee_rate=d(payload.get("feeRate", "0.0005")),
                slippage_rate=d(payload.get("slippageRate", "0.0002")),
                max_orders_per_tick=int(payload.get("maxOrdersPerTick", 1)),
                auto_reopen=bool(payload.get("autoReopen", False)),
            )
            self.step_size = d(payload.get("stepSize", "0.001"))
            self.interval = int(payload.get("interval", 15))

    def credential(self) -> ExchangeCredential | None:
        if not self.api_key or not self.api_secret:
            return None
        return ExchangeCredential(
            exchange=self.exchange,
            api_key=self.api_key,
            api_secret=self.api_secret,
            passphrase=self.passphrase,
        )

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.running:
                return {"ok": True, "message": "already running"}
            confirm = str(payload.get("confirmText", ""))
            self.live = bool(payload.get("live", False))
            self.testnet = bool(payload.get("testnet", False))
            if self.live and confirm != "黄金保守马丁格尔策略":
                raise ValueError("实盘启动前必须输入策略名称确认。")
            api_key = str(payload.get("apiKey", "")).strip()
            api_secret = str(payload.get("apiSecret", "")).strip()
            if api_key and api_secret:
                self.api_key = api_key
                self.api_secret = api_secret
                self.passphrase = str(payload.get("passphrase", "")).strip()
            api_key = self.api_key
            api_secret = self.api_secret
            if self.live and (not api_key or not api_secret):
                raise ValueError("实盘模式需要先绑定 API key 和 secret。")
            self.configure(payload)
            self.client = create_exchange_client(self.credential(), self.live, self.testnet)
            self.stop_event.clear()
            self.running = True
            self.last_error = ""
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
            return {"ok": True, "message": "started"}

    def bind(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_key = str(payload.get("apiKey", "")).strip()
        api_secret = str(payload.get("apiSecret", "")).strip()
        if not api_key or not api_secret:
            raise ValueError("请填写 API Key 和 Secret Key。")
        with self.lock:
            self.exchange = str(payload.get("exchange", "binance"))
            self.api_key = api_key
            self.api_secret = api_secret
            self.passphrase = str(payload.get("passphrase", "")).strip()
        return {"ok": True, "message": "bound", "exchange": self.exchange}

    def clear_auth(self) -> dict[str, Any]:
        with self.lock:
            if self.running:
                raise ValueError("策略运行中不能更换授权，请先停止。")
            self.api_key = ""
            self.api_secret = ""
            self.passphrase = ""
        return {"ok": True, "message": "auth cleared"}

    def stop(self) -> dict[str, Any]:
        self.stop_event.set()
        with self.lock:
            self.running = False
        return {"ok": True, "message": "stopping"}

    def reset_state(self) -> dict[str, Any]:
        with self.lock:
            if self.running:
                raise ValueError("先停止策略，再重置状态。")
            if self.state_file.exists():
                self.state_file.unlink()
            self.last_actions = []
            self.last_error = ""
            self.last_price = None
            return {"ok": True, "message": "state reset"}

    def tick_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self.configure(payload)
            price = d(payload["price"])
            self.client = create_exchange_client(self.credential(), live=False, testnet=False)
            state = load_state(self.state_file)
            actions = handle_tick(self.client, state, self.cfg, price, self.step_size)
            save_state(self.state_file, state)
            self.last_price = price
            self.last_actions = actions
            return self.status()

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                with self.lock:
                    client = self.client
                    cfg = self.cfg
                    step = self.step_size
                if client is None:
                    raise RuntimeError("client not configured")
                state = load_state(self.state_file)
                price = client.price(cfg.symbol)
                actions = handle_tick(client, state, cfg, price, step)
                save_state(self.state_file, state)
                with self.lock:
                    self.last_price = price
                    self.last_actions = actions
                    self.last_error = ""
            except Exception as exc:
                with self.lock:
                    self.last_error = str(exc)
            time.sleep(self.interval)
        with self.lock:
            self.running = False

    def ladder(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        r = Decimal("1") - self.cfg.grid_drop
        cumulative = Decimal("0")
        qty_mult_total = Decimal("0")
        for level in range(self.cfg.max_adds + 1):
            mult = Decimal("1") if level == 0 else Decimal(2) ** Decimal(level - 1)
            price_ratio = r ** Decimal(level)
            buy = self.cfg.initial_notional * mult * price_ratio
            cumulative += buy
            qty_mult_total += mult
            current_value = self.cfg.initial_notional * qty_mult_total * price_ratio
            loss = cumulative - current_value
            rows.append(
                {
                    "level": "首仓" if level == 0 else f"加仓{level}",
                    "drawdown": f"{level}%",
                    "buy": qfmt(buy),
                    "cumulative": qfmt(cumulative),
                    "value": qfmt(current_value),
                    "loss": qfmt(loss),
                }
            )
        return rows

    def status(self) -> dict[str, Any]:
        with self.lock:
            state = load_state(self.state_file)
            price = self.last_price or Decimal("0")
            invested = invested_cost(state)
            value = position_value(state, price) if price else Decimal("0")
            pnl = floating_pnl(state, price) if price else Decimal("0")
            margin = invested / Decimal(self.cfg.leverage) if invested else Decimal("0")
            pressure = margin - pnl
            be = add_breakeven_price(state, self.cfg)
            tp = core_take_profit_price(state, self.cfg)
            sp = stop_price(state, self.cfg)
            return {
                "strategyName": "黄金保守马丁格尔策略",
                "exchange": self.exchange,
                "exchanges": exchange_catalog(),
                "running": self.running,
                "live": self.live,
                "testnet": self.testnet,
                "lastError": self.last_error,
                "lastActions": self.last_actions,
                "authBound": bool(self.api_key and self.api_secret),
                "config": asdict(self.cfg),
                "state": asdict(state),
                "lastPrice": str(price) if price else "",
                "invested": qfmt(invested),
                "value": qfmt(value),
                "floatingPnl": qfmt(pnl),
                "margin": qfmt(margin),
                "pressure": qfmt(pressure),
                "addBreakeven": qfmt(be, "0.0001") if be else "",
                "coreTakeProfit": qfmt(tp, "0.0001") if tp else "",
                "stopPrice": qfmt(sp, "0.0001") if sp else "",
                "ladder": self.ladder(),
            }

RUNNERS: dict[str, StrategyRunner] = {}


def get_or_create_runner(session_id: str) -> StrategyRunner:
    runner = RUNNERS.get(session_id)
    if runner is None:
        runner = StrategyRunner(session_id)
        RUNNERS[session_id] = runner
    return runner


class Handler(BaseHTTPRequestHandler):
    runner: StrategyRunner
    pending_cookie: str | None

    def prepare_session(self) -> None:
        self.pending_cookie = None
        session_id = self.session_id()
        self.runner = get_or_create_runner(session_id)

    def session_id(self) -> str:
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE and value:
                return value
        session_id = secrets.token_urlsafe(24)
        self.pending_cookie = session_id
        return session_id

    def do_GET(self) -> None:
        self.prepare_session()
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/status":
            self.json(self.runner.status())
            return
        if parsed.path == "/api/exchanges":
            self.json({"exchanges": exchange_catalog()})
            return
        path = "index.html" if parsed.path in {"/", ""} else parsed.path.lstrip("/")
        file_path = (STATIC / path).resolve()
        if not str(file_path).startswith(str(STATIC.resolve())) or not file_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ctype = "text/html"
        if file_path.suffix == ".css":
            ctype = "text/css"
        elif file_path.suffix == ".js":
            ctype = "application/javascript"
        body = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.security_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self) -> None:
        self.prepare_session()
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {"/", ""}:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.security_headers()
            self.end_headers()
            return
        if parsed.path == "/api/status":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.security_headers()
            self.end_headers()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        self.prepare_session()
        try:
            payload = self.read_json()
            if self.path == "/api/start":
                self.json(self.runner.start(payload))
            elif self.path == "/api/bind":
                self.json(self.runner.bind(payload))
            elif self.path == "/api/clear-auth":
                self.json(self.runner.clear_auth())
            elif self.path == "/api/stop":
                self.json(self.runner.stop())
            elif self.path == "/api/reset":
                self.json(self.runner.reset_state())
            elif self.path == "/api/tick":
                self.json(self.runner.tick_once(payload))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode())

    def json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data, cls=DecimalJSONEncoder).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.security_headers()
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self) -> None:
        if getattr(self, "pending_cookie", None):
            self.send_header(
                "Set-Cookie",
                f"{SESSION_COOKIE}={self.pending_cookie}; HttpOnly; SameSite=Lax; Path=/",
            )
        super().end_headers()

    def security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> None:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8765"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"黄金保守马丁格尔策略 web console: http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
