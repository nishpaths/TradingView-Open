"""
app.py — Flask + Flask-SocketIO server for the Financial Charting App.

Environment variables (copy .env.example → .env to configure):
  HOST            Server bind address (default: 0.0.0.0)
  PORT            Server port         (default: 5000)
  FLASK_DEBUG     Enable debug mode   (default: false)
  CRYPTO_SYMBOLS  Comma-separated Coinbase product IDs (default: BTC-USD,ETH-USD)
  DATA_PROVIDER   Data provider name  (default: yfinance)
"""

import logging
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from flask_socketio import SocketIO

from single_data_source import CoinbaseCryptoStream, get_provider

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "changeme-in-production")

CORS(app, resources={r"/api/*": {"origins": "*"}})

# threading async_mode: works without eventlet/gevent; compatible with
# the blocking websocket-client library used by CoinbaseCryptoStream.
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

# Shared data provider (pluggable via DATA_PROVIDER env var)
data_provider = get_provider()

# ---------------------------------------------------------------------------
# Coinbase real-time stream
# ---------------------------------------------------------------------------

_crypto_symbols: list[str] = [
    s.strip()
    for s in os.getenv("CRYPTO_SYMBOLS", "BTC-USD,ETH-USD").split(",")
    if s.strip()
]


def _on_crypto_tick(product_id: str, price: float, timestamp: str) -> None:
    """Callback invoked by CoinbaseCryptoStream on every price update."""
    payload = {
        "symbol": product_id,
        "price": price,
        "timestamp": timestamp,
    }
    # Broadcast to every connected Socket.IO client
    socketio.emit("crypto_tick", payload)
    logger.debug("Tick broadcast: %s @ %.4f", product_id, price)


crypto_stream = CoinbaseCryptoStream(
    product_ids=_crypto_symbols,
    on_tick=_on_crypto_tick,
)

# ---------------------------------------------------------------------------
# REST API routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/history")
def api_history():
    """
    GET /api/history?symbol=AAPL&interval=1d&period=1y

    Query parameters:
      symbol    – ticker symbol, e.g. AAPL, BTC-USD   (required)
      interval  – candle interval: 1m 5m 15m 30m 1h 4h 1d 1w 1M  (default: 1d)
      period    – look-back period accepted by yfinance (optional, auto-chosen)

    Returns JSON array of OHLCV candles:
      [{"time": <unix_seconds>, "open": ..., "high": ..., "low": ..., "close": ...}, ...]
    """
    symbol = request.args.get("symbol", "").strip().upper()
    if not symbol:
        return jsonify({"error": "symbol parameter is required"}), 400

    interval = request.args.get("interval", "1d").strip()
    period = request.args.get("period", None)

    try:
        candles = data_provider.get_ohlcv(symbol, interval=interval, period=period)
    except Exception as exc:
        logger.error("get_ohlcv failed for %s: %s", symbol, exc)
        return jsonify({"error": str(exc)}), 500

    return jsonify(candles)


@app.route("/api/symbols")
def api_symbols():
    """
    GET /api/symbols

    Returns the list of crypto symbols being streamed in real time.
    The frontend uses this to know which panes should subscribe to Socket.IO
    ticks rather than polling.
    """
    return jsonify({"crypto": _crypto_symbols})


# ---------------------------------------------------------------------------
# Socket.IO lifecycle events
# ---------------------------------------------------------------------------


@socketio.on("connect")
def on_connect():
    logger.info("Client connected: %s", request.sid)


@socketio.on("disconnect")
def on_disconnect():
    logger.info("Client disconnected: %s", request.sid)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")

    # Start the Coinbase WebSocket stream in its background thread before
    # the Flask dev server takes over the main thread.
    crypto_stream.start()
    logger.info(
        "Starting server on http://%s:%d (debug=%s)", host, port, debug
    )

    socketio.run(
    app,
    host=host,
    port=port,
    debug=debug,
    use_reloader=False,
    allow_unsafe_werkzeug=True,
)
