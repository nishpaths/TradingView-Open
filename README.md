# US Market Dashboard

A financial charting web application optimised for the US market. Built with a Python/Flask backend and a vanilla HTML/CSS/JS frontend using TradingView Lightweight Charts.

## Features

- **Live multi-pane grid** — 1, 2, 4, 6, or 8 independent chart panes in a responsive CSS Grid layout; your choice persists across reloads via `localStorage`.
- **US stock history** — candlestick charts for any US equity (AAPL, NVDA, TSLA, MSFT …) via `yfinance`; supports 1 m, 5 m, 15 m, 30 m, 1 h, 4 h, 1 d, 1 w intervals.
- **Real-time crypto ticks** — live BTC-USD and ETH-USD prices streamed from the Coinbase Advanced Trade public WebSocket (`wss://advanced-trade-ws.coinbase.com`), broadcast to the browser via Socket.IO.
- **Tick flash animations** — each pane's ticker bar flashes green on an upward tick and red on a downward tick.
- **Pluggable data layer** — adding a new broker (Alpaca, Polygon, etc.) requires implementing one class in `single_data_source.py`.

## Project Structure

```
TradingView/
├── single_data_source.py   # Pluggable data layer (DataProvider ABC + implementations)
├── app.py                  # Flask app, REST /api/history, Socket.IO server
├── requirements.txt
├── .env.example            # Template for environment variables
├── templates/
│   └── index.html          # Main page (served by Flask)
└── static/
    ├── style.css
    └── script.js
```

## Quick Start (Local)

```powershell
# 1. Enter the project directory
cd TradingView

# 2. Activate the virtual environment (already created)
.\venv\Scripts\Activate.ps1

# 3. (Optional) copy and edit environment config
Copy-Item .env.example .env

# 4. Start the server
python app.py
```

Then open **http://localhost:5000** in your browser.

## Environment Variables

Copy `.env.example` to `.env` and adjust as needed. On cloud platforms (Render, Railway) set these as dashboard environment variables.

| Variable         | Default         | Description                                     |
|------------------|-----------------|-------------------------------------------------|
| `HOST`           | `0.0.0.0`       | Server bind address                             |
| `PORT`           | `5000`          | Server port                                     |
| `FLASK_DEBUG`    | `false`         | Enable Flask debug mode (`true` / `false`)      |
| `CRYPTO_SYMBOLS` | `BTC-USD,ETH-USD` | Coinbase product IDs to stream (comma-separated) |
| `DATA_PROVIDER`  | `yfinance`      | Active data provider (see Extending below)      |
| `SECRET_KEY`     | `changeme-…`    | Flask session secret — change in production     |

## API Reference

### `GET /api/history`

Returns OHLCV candle data as a JSON array.

| Parameter  | Required | Example    | Description                        |
|------------|----------|------------|------------------------------------|
| `symbol`   | Yes      | `AAPL`     | US equity ticker or crypto pair    |
| `interval` | No       | `1d`       | `1m 5m 15m 30m 1h 4h 1d 1w 1M`    |
| `period`   | No       | `1y`       | yfinance look-back period (auto if omitted) |

Response shape:
```json
[{"time": 1700000000, "open": 189.3, "high": 191.1, "low": 188.5, "close": 190.7}, ...]
```

### `GET /api/symbols`

Returns the list of crypto product IDs currently being streamed.

```json
{"crypto": ["BTC-USD", "ETH-USD"]}
```

### Socket.IO — `crypto_tick` event

Emitted to all connected clients on every Coinbase ticker update.

```json
{"symbol": "BTC-USD", "price": 68432.10, "timestamp": "2024-01-01T12:00:00Z"}
```

## Extending with a New Data Provider

1. Open `single_data_source.py`.
2. Create a class that inherits `DataProvider` and implements `get_ohlcv()`.
3. Register it in `PROVIDER_REGISTRY` at the bottom of the file.
4. Set `DATA_PROVIDER=your_provider_name` in `.env`.

```python
class AlpacaProvider(DataProvider):
    def get_ohlcv(self, symbol, interval="1d", period=None):
        # ... fetch from Alpaca API ...
        return [{"time": ..., "open": ..., "high": ..., "low": ..., "close": ...}]

PROVIDER_REGISTRY["alpaca"] = AlpacaProvider
```

## Deploying to Render / Railway

1. Push the repository to GitHub.
2. Create a new Web Service and point it to your repo.
3. Set the **Start Command** to:
   ```
   python app.py
   ```
4. Add the environment variables from the table above (at minimum set `SECRET_KEY` to a random string and `FLASK_DEBUG=false`).
5. The server binds to `0.0.0.0` and reads `PORT` automatically — both Render and Railway inject `PORT` at runtime.

## Dependencies

| Package           | Purpose                                         |
|-------------------|-------------------------------------------------|
| `flask`           | Web framework                                   |
| `flask-socketio`  | WebSocket / Socket.IO server                    |
| `flask-cors`      | CORS headers for the REST API                   |
| `yfinance`        | US stock & crypto historical data               |
| `websocket-client`| Blocking WebSocket client for Coinbase stream   |
| `python-dotenv`   | Load `.env` files into `os.environ`             |
