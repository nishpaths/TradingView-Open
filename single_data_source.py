"""
single_data_source.py — Pluggable data layer for the Financial Charting App.

To add a new broker/provider (e.g. Alpaca, Polygon):
  1. Create a class that inherits DataProvider and implements get_ohlcv().
  2. Register it in PROVIDER_REGISTRY at the bottom of this file.
  3. Set DATA_PROVIDER=your_provider_name in the .env file.

The CoinbaseCryptoStream runs as a background thread and pushes real-time
crypto ticks to any registered callback via on_tick(product_id, price, side).
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Callable

import websocket
import yfinance as yf

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Interval normalisation helpers
# ---------------------------------------------------------------------------

# Maps frontend interval strings → yfinance download interval strings
_INTERVAL_MAP: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1w": "1wk",
    "1M": "1mo",
}

# Default look-back periods per interval so yfinance returns enough candles
_DEFAULT_PERIOD: dict[str, str] = {
    "1m": "7d",
    "5m": "60d",
    "15m": "60d",
    "30m": "60d",
    "1h": "730d",
    "4h": "730d",
    "1d": "5y",
    "1w": "10y",
    "1M": "max",
}


def _normalise_interval(interval: str) -> str:
    return _INTERVAL_MAP.get(interval, "1d")


def _default_period(interval: str) -> str:
    return _DEFAULT_PERIOD.get(interval, "1y")


# ---------------------------------------------------------------------------
# Abstract interface — implement this to plug in a new provider
# ---------------------------------------------------------------------------


class DataProvider(ABC):
    """
    Unified interface for historical OHLCV data.

    Implementors must return a list of dicts with the keys:
        time  – Unix timestamp (integer seconds, UTC)
        open  – float
        high  – float
        low   – float
        close – float
    The list must be sorted in ascending time order (oldest first).
    """

    @abstractmethod
    def get_ohlcv(
        self,
        symbol: str,
        interval: str = "1d",
        period: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return OHLCV candles for *symbol* at the given *interval*."""
        ...


# ---------------------------------------------------------------------------
# YFinance provider (default)
# ---------------------------------------------------------------------------


class YFinanceProvider(DataProvider):
    """
    Fetches historical OHLCV data using the yfinance library.

    Works natively with US equity tickers (AAPL, NVDA, TSLA …) and
    crypto pairs written in yfinance notation (BTC-USD, ETH-USD …).
    No API key is required.
    """

    def get_ohlcv(
        self,
        symbol: str,
        interval: str = "1d",
        period: str | None = None,
    ) -> list[dict[str, Any]]:
        yf_interval = _normalise_interval(interval)
        yf_period = period or _default_period(interval)

        logger.debug(
            "YFinanceProvider.get_ohlcv: symbol=%s interval=%s period=%s",
            symbol,
            yf_interval,
            yf_period,
        )

        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=yf_period, interval=yf_interval)
        except Exception as exc:
            logger.error("yfinance download failed for %s: %s", symbol, exc)
            return []

        if df is None or df.empty:
            logger.warning("yfinance returned empty data for %s", symbol)
            return []

        candles: list[dict[str, Any]] = []
        for ts, row in df.iterrows():
            # ts is a pandas Timestamp; convert to UTC unix seconds
            try:
                unix_ts = int(ts.timestamp())
            except Exception:
                continue

            o = float(row["Open"])
            h = float(row["High"])
            lo = float(row["Low"])
            c = float(row["Close"])

            # Skip candles where any OHLC value is NaN (common after splits/dividends)
            if any(math.isnan(v) for v in (o, h, lo, c)):
                continue

            candles.append(
                {
                    "time": unix_ts,
                    "open": round(o, 6),
                    "high": round(h, 6),
                    "low": round(lo, 6),
                    "close": round(c, 6),
                }
            )

        # Ensure ascending order and deduplicate timestamps
        seen: set[int] = set()
        unique: list[dict[str, Any]] = []
        for c in sorted(candles, key=lambda x: x["time"]):
            if c["time"] not in seen:
                seen.add(c["time"])
                unique.append(c)

        return unique


# ---------------------------------------------------------------------------
# Coinbase Advanced Trade WebSocket stream
# ---------------------------------------------------------------------------


class CoinbaseCryptoStream:
    """
    Maintains a persistent WebSocket connection to the Coinbase Advanced Trade
    market data endpoint and calls *on_tick* for every price update received.

    Runs in its own daemon thread. Reconnects automatically with exponential
    backoff on any connection error.

    Parameters
    ----------
    product_ids:
        List of Coinbase product IDs to subscribe to, e.g. ["BTC-USD", "ETH-USD"].
    on_tick:
        Callback invoked with (product_id: str, price: float, timestamp: str)
        for each incoming ticker update.
    """

    WS_URL = "wss://advanced-trade-ws.coinbase.com"
    MAX_RECONNECT_DELAY = 60  # seconds

    def __init__(
        self,
        product_ids: list[str],
        on_tick: Callable[[str, float, str], None],
    ) -> None:
        self.product_ids = product_ids
        self.on_tick = on_tick
        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._reconnect_delay = 2  # seconds, doubles on each failure

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background thread (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="coinbase-ws",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "CoinbaseCryptoStream started for products: %s", self.product_ids
        )

    def stop(self) -> None:
        """Signal the background thread to stop."""
        self._stop_event.set()
        if self._ws:
            self._ws.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Keep connecting until stop() is called."""
        while not self._stop_event.is_set():
            try:
                self._connect()
                # _connect() blocks until the connection closes.
                # If we reach here normally, reset the backoff.
                self._reconnect_delay = 2
            except Exception as exc:
                logger.error("CoinbaseCryptoStream error: %s", exc)

            if self._stop_event.is_set():
                break

            logger.info(
                "CoinbaseCryptoStream reconnecting in %ds …",
                self._reconnect_delay,
            )
            time.sleep(self._reconnect_delay)
            self._reconnect_delay = min(
                self._reconnect_delay * 2, self.MAX_RECONNECT_DELAY
            )

    def _connect(self) -> None:
        self._ws = websocket.WebSocketApp(
            self.WS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws.run_forever()

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        logger.info("Coinbase WS connected — subscribing …")
        # Subscribe to heartbeats first to keep the connection alive
        ws.send(
            json.dumps(
                {
                    "type": "subscribe",
                    "channel": "heartbeats",
                }
            )
        )
        # Then subscribe to ticker for the requested products
        ws.send(
            json.dumps(
                {
                    "type": "subscribe",
                    "product_ids": self.product_ids,
                    "channel": "ticker",
                }
            )
        )

    def _on_message(self, ws: websocket.WebSocketApp, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        channel = msg.get("channel", "")

        if channel == "heartbeats":
            return  # silently ignore heartbeat envelopes

        if channel != "ticker":
            return

        timestamp = msg.get("timestamp", "")
        for event in msg.get("events", []):
            for ticker in event.get("tickers", []):
                product_id = ticker.get("product_id", "")
                raw_price = ticker.get("price")
                if product_id and raw_price is not None:
                    try:
                        price = float(raw_price)
                    except (TypeError, ValueError):
                        continue
                    try:
                        self.on_tick(product_id, price, timestamp)
                    except Exception as exc:
                        logger.error("on_tick callback raised: %s", exc)

    def _on_error(self, ws: websocket.WebSocketApp, error: Any) -> None:
        logger.error("Coinbase WS error: %s", error)

    def _on_close(
        self,
        ws: websocket.WebSocketApp,
        close_status_code: Any,
        close_msg: Any,
    ) -> None:
        logger.info(
            "Coinbase WS closed (code=%s msg=%s)", close_status_code, close_msg
        )


# ---------------------------------------------------------------------------
# Provider registry & factory
# ---------------------------------------------------------------------------

PROVIDER_REGISTRY: dict[str, type[DataProvider]] = {
    "yfinance": YFinanceProvider,
    # "alpaca": AlpacaProvider,   # example: add future providers here
    # "polygon": PolygonProvider,
}


def get_provider(name: str | None = None) -> DataProvider:
    """
    Return an instantiated DataProvider by name.

    The name defaults to the DATA_PROVIDER environment variable, or "yfinance"
    if that variable is not set.
    """
    provider_name = name or os.getenv("DATA_PROVIDER", "yfinance")
    cls = PROVIDER_REGISTRY.get(provider_name)
    if cls is None:
        raise ValueError(
            f"Unknown data provider '{provider_name}'. "
            f"Available: {list(PROVIDER_REGISTRY)}"
        )
    return cls()
