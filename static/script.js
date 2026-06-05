/**
 * script.js — US Market Dashboard frontend logic
 *
 * Two primary classes:
 *   ChartPane   — owns one chart pane (DOM + LightweightCharts instance)
 *   PaneManager — singleton that manages the grid layout and all panes
 *
 * Real-time crypto ticks arrive via Socket.IO and are routed to whichever
 * pane(s) currently display that crypto symbol.
 */

'use strict';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const LIGHTWEIGHT_CHARTS_NS = window.LightweightCharts;

/** Default symbol assigned to each slot (indices 0-7). */
const DEFAULT_SYMBOLS = ['AAPL', 'NVDA', 'TSLA', 'BTC-USD', 'ETH-USD', 'MSFT', 'AMZN', 'GOOGL'];

/** Interval → approximate look-back period for the REST call. */
const PERIOD_FOR_INTERVAL = {
  '1m':  '7d',
  '5m':  '60d',
  '15m': '60d',
  '30m': '60d',
  '1h':  '730d',
  '4h':  '730d',
  '1d':  '5y',
  '1w':  '10y',
};

/** Dark theme colours passed to LightweightCharts.createChart(). */
const CHART_THEME = {
  layout: {
    background:  { type: 'solid', color: '#161b22' },
    textColor:   '#8b949e',
  },
  grid: {
    vertLines:   { color: '#21262d' },
    horzLines:   { color: '#21262d' },
  },
  crosshair: {
    vertLine:    { color: '#388bfd55', labelBackgroundColor: '#388bfd' },
    horzLine:    { color: '#388bfd55', labelBackgroundColor: '#388bfd' },
  },
  timeScale: {
    borderColor: '#30363d',
    timeVisible: true,
    secondsVisible: false,
  },
  rightPriceScale: {
    borderColor: '#30363d',
  },
};

const CANDLE_COLORS = {
  upColor:          '#26a69a',
  downColor:        '#ef5350',
  borderUpColor:    '#26a69a',
  borderDownColor:  '#ef5350',
  wickUpColor:      '#26a69a',
  wickDownColor:    '#ef5350',
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Return true if a symbol looks like a crypto pair (contains a hyphen). */
function isCrypto(symbol) {
  return symbol.includes('-');
}

/** Format a price number to a reasonable number of decimal places. */
function formatPrice(price) {
  if (price >= 1000)   return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (price >= 1)      return price.toFixed(4);
  return price.toFixed(6);
}

// ---------------------------------------------------------------------------
// ChartPane — one chart pane in the grid
// ---------------------------------------------------------------------------

class ChartPane {
  /**
   * @param {HTMLElement} container  — The .chart-pane element (from template clone)
   * @param {string}      symbol     — Initial symbol, e.g. "AAPL"
   * @param {string}      interval   — Initial interval, e.g. "1d"
   * @param {number}      index      — Slot index (used for default symbol fallback)
   */
  constructor(container, symbol, interval, index) {
    this.container  = container;
    this.symbol     = symbol.toUpperCase();
    this.interval   = interval;
    this.index      = index;
    this._lastPrice = null;
    this._chart     = null;
    this._series    = null;
    this._resizeObs = null;

    this._bindDom();
    this._initChart();
    this.loadHistory();
  }

  // ---- DOM wiring ----------------------------------------------------------

  _bindDom() {
    this._tickerBar    = this.container.querySelector('.ticker-bar');
    this._tickerSym    = this.container.querySelector('.ticker-symbol');
    this._tickerPrice  = this.container.querySelector('.ticker-price');
    this._tickerChange = this.container.querySelector('.ticker-change');
    this._chartArea    = this.container.querySelector('.chart-area');
    this._loadingEl    = this.container.querySelector('.pane-loading');
    this._errorEl      = this.container.querySelector('.pane-error');
    this._symInput     = this.container.querySelector('.ctrl-symbol');
    this._ivSelect     = this.container.querySelector('.ctrl-interval');
    this._loadBtn      = this.container.querySelector('.ctrl-load');

    // Populate controls with current values
    this._symInput.value = this.symbol;
    this._ivSelect.value = this.interval;

    // Load on button click
    this._loadBtn.addEventListener('click', () => this._applyControls());

    // Load on Enter in symbol input
    this._symInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') this._applyControls();
    });

    // Load on interval change
    this._ivSelect.addEventListener('change', () => this._applyControls());

    // Enforce uppercase in symbol input
    this._symInput.addEventListener('input', () => {
      const pos = this._symInput.selectionStart;
      this._symInput.value = this._symInput.value.toUpperCase();
      this._symInput.setSelectionRange(pos, pos);
    });
  }

  _applyControls() {
    const newSym = this._symInput.value.trim().toUpperCase();
    const newIv  = this._ivSelect.value;
    if (!newSym) return;
    this.symbol   = newSym;
    this.interval = newIv;
    this.loadHistory();
  }

  // ---- Chart initialisation -----------------------------------------------

  _initChart() {
    this._chart = LIGHTWEIGHT_CHARTS_NS.createChart(this._chartArea, {
      ...CHART_THEME,
      width:  this._chartArea.clientWidth  || 300,
      height: this._chartArea.clientHeight || 200,
    });

    this._series = this._chart.addCandlestickSeries(CANDLE_COLORS);

    // Keep the chart sized to its container via ResizeObserver
    this._resizeObs = new ResizeObserver(() => {
      if (this._chart && this._chartArea) {
        this._chart.resize(
          this._chartArea.clientWidth,
          this._chartArea.clientHeight
        );
      }
    });
    this._resizeObs.observe(this._chartArea);
  }

  // ---- Data loading --------------------------------------------------------

  async loadHistory() {
    this._showLoading(true);
    this._hideError();
    this._updateTickerSymbol(this.symbol);

    const period = PERIOD_FOR_INTERVAL[this.interval] || '1y';
    const url = `/api/history?symbol=${encodeURIComponent(this.symbol)}&interval=${this.interval}&period=${period}`;

    try {
      const resp = await fetch(url);
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.error || `HTTP ${resp.status}`);
      }
      const candles = await resp.json();
      if (!Array.isArray(candles) || candles.length === 0) {
        throw new Error('No data returned for this symbol / interval.');
      }
      this._series.setData(candles);
      this._chart.timeScale().fitContent();

      // Seed the ticker bar with the last close price
      const last = candles[candles.length - 1];
      this._updateTickerPrice(last.close, null);
    } catch (err) {
      this._showError(err.message);
    } finally {
      this._showLoading(false);
    }
  }

  // ---- Real-time tick handler ---------------------------------------------

  /**
   * Called by PaneManager when a live crypto tick arrives for this pane's symbol.
   * @param {number} price
   * @param {string} timestamp  ISO-8601 string from Coinbase
   */
  onTick(price, timestamp) {
    const prev = this._lastPrice;
    this._updateTickerPrice(price, prev);

    // Flash the ticker bar
    const bar = this._tickerBar;
    bar.classList.remove('flash-up', 'flash-down');

    // Force a reflow so the animation restarts if called rapidly
    void bar.offsetWidth;

    if (prev !== null && price !== prev) {
      bar.classList.add(price > prev ? 'flash-up' : 'flash-down');
    }

    // Update the last candle on the chart with the live price
    if (this._series) {
      // Convert ISO timestamp → unix seconds
      const tsMs = timestamp ? Date.parse(timestamp) : Date.now();
      const tsSec = Math.floor(tsMs / 1000);
      try {
        this._series.update({
          time:  tsSec,
          open:  price,
          high:  price,
          low:   price,
          close: price,
        });
      } catch (_) {
        // Silently ignore if the tick timestamp is behind existing data
      }
    }
  }

  // ---- UI helpers ---------------------------------------------------------

  _updateTickerSymbol(sym) {
    this._tickerSym.textContent = sym;
  }

  _updateTickerPrice(price, prevPrice) {
    this._lastPrice = price;
    this._tickerPrice.textContent = '$' + formatPrice(price);

    if (prevPrice !== null && prevPrice !== undefined) {
      const diff = price - prevPrice;
      const pct  = prevPrice !== 0 ? (diff / prevPrice) * 100 : 0;
      const sign = diff >= 0 ? '+' : '';
      this._tickerChange.textContent = `${sign}${pct.toFixed(2)}%`;
      this._tickerChange.style.color = diff >= 0 ? 'var(--green)' : 'var(--red)';
    }
  }

  _showLoading(visible) {
    this._loadingEl.hidden = !visible;
  }

  _showError(msg) {
    this._errorEl.textContent = `⚠ ${msg}`;
    this._errorEl.hidden = false;
  }

  _hideError() {
    this._errorEl.hidden = true;
    this._errorEl.textContent = '';
  }

  // ---- Cleanup -------------------------------------------------------------

  destroy() {
    if (this._resizeObs) {
      this._resizeObs.disconnect();
      this._resizeObs = null;
    }
    if (this._chart) {
      this._chart.remove();
      this._chart  = null;
      this._series = null;
    }
    if (this.container.parentNode) {
      this.container.parentNode.removeChild(this.container);
    }
  }
}

// ---------------------------------------------------------------------------
// PaneManager — singleton grid manager + Socket.IO coordinator
// ---------------------------------------------------------------------------

class PaneManager {
  constructor() {
    this._panes       = [];   // Array<ChartPane>
    this._socketReady = false;
    this._socket      = null;
    this._grid        = document.getElementById('grid-container');
    this._layoutSel   = document.getElementById('layout-select');
    this._badge       = document.getElementById('connection-badge');
    this._badgeText   = this._badge.querySelector('.badge-text');
    this._template    = document.getElementById('pane-template');

    this._initSocketIO();
    this._initLayoutSelector();

    // Restore layout from localStorage (or default to 4)
    const saved = parseInt(localStorage.getItem('chartCount'), 10) || 4;
    this._layoutSel.value = String(saved);
    this.setLayout(saved);
  }

  // ---- Layout management --------------------------------------------------

  setLayout(count) {
    const n = parseInt(count, 10);
    localStorage.setItem('chartCount', String(n));

    // Update the grid CSS class
    this._grid.className = `grid-${n}`;

    const current = this._panes.length;

    if (n > current) {
      // Add panes
      for (let i = current; i < n; i++) {
        this._addPane(i);
      }
    } else if (n < current) {
      // Remove surplus panes
      for (let i = current - 1; i >= n; i--) {
        this._panes[i].destroy();
        this._panes.splice(i, 1);
      }
    }
  }

  _addPane(index) {
    const fragment = this._template.content.cloneNode(true);
    const paneEl   = fragment.querySelector('.chart-pane');

    this._grid.appendChild(paneEl);

    const sym  = DEFAULT_SYMBOLS[index] || 'AAPL';
    const pane = new ChartPane(paneEl, sym, '1d', index);
    this._panes.push(pane);
  }

  _initLayoutSelector() {
    this._layoutSel.addEventListener('change', () => {
      this.setLayout(parseInt(this._layoutSel.value, 10));
    });
  }

  // ---- Socket.IO ----------------------------------------------------------

  _initSocketIO() {
    // Connect to the same host that served the page
    this._socket = io({ transports: ['websocket', 'polling'] });

    this._socket.on('connect', () => {
      this._socketReady = true;
      this._setBadge('live', 'Live');
    });

    this._socket.on('disconnect', () => {
      this._socketReady = false;
      this._setBadge('connecting', 'Reconnecting…');
    });

    this._socket.on('connect_error', () => {
      this._setBadge('error', 'Connection error');
    });

    this._socket.on('crypto_tick', (data) => {
      const symbol = (data.symbol || '').toUpperCase();
      const price  = parseFloat(data.price);
      const ts     = data.timestamp || '';

      if (!symbol || isNaN(price)) return;

      // Route to every pane that currently shows this symbol
      for (const pane of this._panes) {
        if (pane.symbol === symbol) {
          pane.onTick(price, ts);
        }
      }
    });
  }

  _setBadge(type, text) {
    this._badge.className = `badge badge-${type}`;
    this._badgeText.textContent = text;
  }
}

// ---------------------------------------------------------------------------
// Bootstrap — run after DOM is ready
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
  if (!LIGHTWEIGHT_CHARTS_NS) {
    console.error('LightweightCharts library not loaded. Check the CDN script tag.');
    return;
  }
  window._paneManager = new PaneManager();
});
