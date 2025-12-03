import asyncio
import json
import os
import time
from collections import deque, defaultdict

import requests
import websockets
from tele import TelegramBot
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ---------------- CONFIG LOADING ---------------- #

def load_config():
    """Load configuration from config.json file"""
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        return config
    except FileNotFoundError:
        print("Error: config.json file not found")
        return None
    except json.JSONDecodeError as e:
        print(f"Error parsing config.json: {e}")
        return None

def load_env_config():
    """Load environment variables from .env file"""
    telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
    telegram_channel_id = os.getenv('TELEGRAM_CHANNEL_ID')

    if not telegram_token:
        print("Warning: TELEGRAM_BOT_TOKEN not found in environment variables")
    if not telegram_channel_id:
        print("Warning: TELEGRAM_CHANNEL_ID not found in environment variables")

    return telegram_token, telegram_channel_id

# Load configurations
config = load_config()
telegram_token, telegram_channel_id = load_env_config()

if not config:
    exit(1)

# Extract configuration values
BINANCE_FUTURES_REST = config["binance"]["futures_rest"]
BINANCE_FUTURES_EXCHANGE_INFO = config["binance"]["futures_exchange_info"]
BINANCE_FUTURES_WS_URL = config["binance"]["futures_ws_url"]

# Convert string keys to integers for time windows
PUMP_WINDOWS = {int(k): v for k, v in config["detection"]["pump_windows"].items()}
DUMP_WINDOWS = {int(k): v for k, v in config["detection"]["dump_windows"].items()}
ALERT_COOLDOWN = config["detection"]["alert_cooldown"]

# Volume filters
VOLUME_WINDOW_MIN = config["filters"]["volume"]["window_min"]
VOLUME_LOOKBACK_MIN = config["filters"]["volume"]["lookback_min"]
MIN_5M_VOLUME_USDT = config["filters"]["volume"]["min_5m_volume_usdt"]
MIN_VOLUME_SPIKE_RATIO = config["filters"]["volume"]["min_spike_ratio"]

# Spread filters
MAX_SPREAD_PCT = config["filters"]["spread"]["max_spread_pct"]
DEPTH_LIMIT = config["filters"]["spread"]["depth_limit"]
MIN_BID_NOTIONAL = config["filters"]["spread"]["min_bid_notional"]

# Open interest stats
OI_WINDOW_SEC = config["filters"]["open_interest"]["window_sec"]


# ---------------- SYMBOL DISCOVERY ---------------- #

def get_usdt_perpetual_symbols():
    """
    Query Binance Futures exchangeInfo and return a set of symbols that are:
    - USDT-margined
    - PERPETUAL contracts
    """
    resp = requests.get(BINANCE_FUTURES_EXCHANGE_INFO, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    symbols = set()
    for s in data.get("symbols", []):
        if (
            s.get("contractType") == "PERPETUAL"
            and s.get("quoteAsset") == "USTS" or s.get("quoteAsset") == "USDT"
            and s.get("status") == "TRADING"
        ):
            # NOTE: Some docs mention USDS or USTS variants; we filter pure USDT
            if s["quoteAsset"] == "USDT":
                symbols.add(s["symbol"])
    return symbols


# ---------------- STATE ---------------- #

class SymbolState:
    def __init__(self, max_window_seconds: int):
        # store (timestamp_sec, price)
        self.prices = deque()
        self.max_window = max_window_seconds

    def add_price(self, ts: float, price: float):
        """
        Add a new price point and evict old ones.
        ts: time.time() in seconds
        """
        self.prices.append((ts, price))
        cutoff = ts - self.max_window

        # Evict old data from the left (oldest first)
        while self.prices and self.prices[0][0] < cutoff:
            self.prices.popleft()

    def get_return_over_window(self, window_seconds: int):
        """
        Compute return over the given window (in seconds).
        Return: (r, old_price, new_price) or (None, None, None) if insufficient data.
        """
        if not self.prices:
            return None, None, None

        now_ts = self.prices[-1][0]
        cutoff = now_ts - window_seconds

        # Find earliest price >= cutoff
        old_price = None
        for ts, price in self.prices:
            if ts >= cutoff:
                old_price = price
                break

        if old_price is None:
            # No data in that window
            return None, None, None

        new_price = self.prices[-1][1]
        if old_price <= 0:
            return None, None, None

        r = (new_price / old_price) - 1.0
        return r, old_price, new_price


# ---------------- ALERT BOT ---------------- #

class PumpAlertBot:
    def __init__(self, usdt_perp_symbols, pump_windows, dump_windows, cooldown_seconds, telegram_bot=None):
        self.usdt_perp_symbols = usdt_perp_symbols
        self.pump_windows = pump_windows
        self.dump_windows = dump_windows
        self.max_window = max(list(pump_windows.keys()) + list(dump_windows.keys()))
        self.cooldown_seconds = cooldown_seconds
        self.telegram_bot = telegram_bot

        # symbol -> SymbolState
        self.state = {
            symbol: SymbolState(self.max_window)
            for symbol in usdt_perp_symbols
        }

        # (symbol, window_seconds, alert_type) -> last_alert_time
        self.last_alert = defaultdict(lambda: 0.0)

        # symbol -> deque[(ts, oi)]
        self.oi_history = defaultdict(lambda: deque())

    # ---------- core price handler ---------- #

    def handle_price_update(self, symbol: str, price: float, ts: float = None):
        """
        Called whenever we get a new price for a symbol.
        """
        if symbol not in self.state:
            # Not a USDT perpetual, ignore
            return

        if ts is None:
            ts = time.time()

        self.state[symbol].add_price(ts, price)
        self.check_pumps(symbol)
        self.check_dumps(symbol)

    # ---------- detection engine ---------- #

    def check_pumps(self, symbol: str):
        """
        Check if symbol has pumped enough in any of our configured windows.
        If yes, run additional filters (volume, spread) and then print alert.
        """
        sym_state = self.state[symbol]
        now = time.time()

        for window_sec, threshold_return in self.pump_windows.items():
            r, old_price, new_price = sym_state.get_return_over_window(window_sec)
            if r is None:
                continue

            if r >= threshold_return:
                key = (symbol, window_sec, 'pump')
                last_ts = self.last_alert[key]

                # Cooldown check
                if now - last_ts < self.cooldown_seconds:
                    continue

                # --- extra filters --- #
                vol_ok, vol_info = self.check_volume_filter(symbol)
                if not vol_ok:
                    # Volume not healthy / no spike, skip
                    continue

                spread_ok, spread_info = self.check_spread_filter(symbol)
                if not spread_ok:
                    # Spread too wide or book too thin, skip
                    continue

                # OI is just a stat (not a hard filter)
                oi_change_ratio, oi_now, oi_past = self.update_and_get_oi_change(symbol)

                self.last_alert[key] = now
                pct = r * 100

                # Build console message
                console_msg = "=" * 80 + "\n"
                console_msg += (
                    f"[ALERT] {symbol} pumped {pct:.1f}% "
                    f"over last {window_sec//60}m | {old_price:.6g} -> {new_price:.6g}\n"
                )

                # Volume details
                if vol_info:
                    console_msg += (
                        f"  Volume last {VOLUME_WINDOW_MIN}m: "
                        f"{vol_info['last_5m_usdt']:.0f} USDT | "
                        f"spike x{vol_info['spike_ratio']:.1f} "
                        f"(avg {vol_info['avg_5m_usdt']:.0f} USDT)\n"
                    )

                # Spread / depth details
                if spread_info:
                    console_msg += (
                        f"  Spread: {spread_info['spread_pct']*100:.2f}% | "
                        f"bid {spread_info['bid_price']:.6g} "
                        f"ask {spread_info['ask_price']:.6g} | "
                        f"top-{DEPTH_LIMIT} bid notional ≈ {spread_info['bid_notional']:.0f} USDT\n"
                    )

                # Open interest stats
                if oi_change_ratio is not None:
                    console_msg += (
                        f"  OI change ({OI_WINDOW_SEC//60}m): "
                        f"{oi_change_ratio*100:.1f}% | "
                        f"past {oi_past:.4f}, now {oi_now:.4f} (contracts)\n"
                    )
                else:
                    console_msg += "  OI change: insufficient history yet.\n"

                console_msg += "=" * 80
                print(console_msg)

                # Send Telegram alert
                if self.telegram_bot:
                    telegram_msg = f"🚀 <b>PUMP ALERT</b>\n\n"
                    telegram_msg += f"<b>{symbol}</b> pumped <b>{pct:.1f}%</b> "
                    telegram_msg += f"over last {window_sec//60}m\n"
                    telegram_msg += f"Price: {old_price:.6g} → {new_price:.6g}\n\n"
                    
                    if vol_info:
                        telegram_msg += f"📊 Volume ({VOLUME_WINDOW_MIN}m): "
                        telegram_msg += f"{vol_info['last_5m_usdt']:.0f} USDT "
                        telegram_msg += f"(spike x{vol_info['spike_ratio']:.1f})\n"
                    
                    if spread_info:
                        telegram_msg += f"💹 Spread: {spread_info['spread_pct']*100:.2f}%\n"
                    
                    if oi_change_ratio is not None:
                        telegram_msg += f"📈 OI change: {oi_change_ratio*100:.1f}%\n"
                    
                    self.telegram_bot.send_message_html(telegram_msg)

    def check_dumps(self, symbol: str):
        """
        Check if symbol has dumped enough in any of our configured windows.
        If yes, run additional filters (volume, spread) and then print alert.
        """
        sym_state = self.state[symbol]
        now = time.time()

        for window_sec, threshold_return in self.dump_windows.items():
            r, old_price, new_price = sym_state.get_return_over_window(window_sec)
            if r is None:
                continue

            # For dumps, threshold_return is negative (e.g., -0.5 for -50%)
            # We check if r <= threshold_return (i.e., dropped by at least that much)
            if r <= threshold_return:
                key = (symbol, window_sec, 'dump')
                last_ts = self.last_alert[key]

                # Cooldown check
                if now - last_ts < self.cooldown_seconds:
                    continue

                # --- extra filters --- #
                vol_ok, vol_info = self.check_volume_filter(symbol)
                if not vol_ok:
                    # Volume not healthy / no spike, skip
                    continue

                spread_ok, spread_info = self.check_spread_filter(symbol)
                if not spread_ok:
                    # Spread too wide or book too thin, skip
                    continue

                # OI is just a stat (not a hard filter)
                oi_change_ratio, oi_now, oi_past = self.update_and_get_oi_change(symbol)

                self.last_alert[key] = now
                pct = r * 100

                # Build console message
                console_msg = "=" * 80 + "\n"
                console_msg += (
                    f"[ALERT - DUMP] {symbol} dumped {abs(pct):.1f}% "
                    f"over last {window_sec//60}m | {old_price:.6g} -> {new_price:.6g}\n"
                )

                # Volume details
                if vol_info:
                    console_msg += (
                        f"  Volume last {VOLUME_WINDOW_MIN}m: "
                        f"{vol_info['last_5m_usdt']:.0f} USDT | "
                        f"spike x{vol_info['spike_ratio']:.1f} "
                        f"(avg {vol_info['avg_5m_usdt']:.0f} USDT)\n"
                    )

                # Spread / depth details
                if spread_info:
                    console_msg += (
                        f"  Spread: {spread_info['spread_pct']*100:.2f}% | "
                        f"bid {spread_info['bid_price']:.6g} "
                        f"ask {spread_info['ask_price']:.6g} | "
                        f"top-{DEPTH_LIMIT} bid notional ≈ {spread_info['bid_notional']:.0f} USDT\n"
                    )

                # Open interest stats
                if oi_change_ratio is not None:
                    console_msg += (
                        f"  OI change ({OI_WINDOW_SEC//60}m): "
                        f"{oi_change_ratio*100:.1f}% | "
                        f"past {oi_past:.4f}, now {oi_now:.4f} (contracts)\n"
                    )
                else:
                    console_msg += "  OI change: insufficient history yet.\n"

                console_msg += "=" * 80
                print(console_msg)

                # Send Telegram alert
                if self.telegram_bot:
                    telegram_msg = f"📉 <b>DUMP ALERT</b>\n\n"
                    telegram_msg += f"<b>{symbol}</b> dumped <b>{abs(pct):.1f}%</b> "
                    telegram_msg += f"over last {window_sec//60}m\n"
                    telegram_msg += f"Price: {old_price:.6g} → {new_price:.6g}\n\n"
                    
                    if vol_info:
                        telegram_msg += f"📊 Volume ({VOLUME_WINDOW_MIN}m): "
                        telegram_msg += f"{vol_info['last_5m_usdt']:.0f} USDT "
                        telegram_msg += f"(spike x{vol_info['spike_ratio']:.1f})\n"
                    
                    if spread_info:
                        telegram_msg += f"💹 Spread: {spread_info['spread_pct']*100:.2f}%\n"
                    
                    if oi_change_ratio is not None:
                        telegram_msg += f"📈 OI change: {oi_change_ratio*100:.1f}%\n"
                    
                    self.telegram_bot.send_message_html(telegram_msg)

    # ---------- volume filter ---------- #

    def check_volume_filter(self, symbol: str):
        """
        Use 1m klines via REST to check a 5m volume spike vs 60m history.
        - last 5m quoteVolume must be >= MIN_5M_VOLUME_USDT
        - last 5m must be >= MIN_VOLUME_SPIKE_RATIO * historical average 5m
        """
        try:
            params = {
                "symbol": symbol,
                "interval": "1m",
                "limit": VOLUME_LOOKBACK_MIN,
            }
            resp = requests.get(
                f"{BINANCE_FUTURES_REST}/fapi/v1/klines",
                params=params,
                timeout=5,
            )
            resp.raise_for_status()
            klines = resp.json()
        except Exception as e:
            print(f"[WARN] Volume check failed for {symbol}: {e}")
            return False, None

        if len(klines) < VOLUME_WINDOW_MIN + 5:
            # not enough data
            return False, None

        # kline format: [openTime, open, high, low, close, volume, closeTime, quoteVolume, ...]
        quote_volumes = [float(k[7]) for k in klines]  # quoteVolume is index 7

        last_5 = quote_volumes[-VOLUME_WINDOW_MIN:]
        prev = quote_volumes[:-VOLUME_WINDOW_MIN]

        last_5m_usdt = sum(last_5)
        if not prev:
            return False, None

        avg_per_min_prev = sum(prev) / len(prev)
        avg_5m_usdt = avg_per_min_prev * VOLUME_WINDOW_MIN
        spike_ratio = last_5m_usdt / (avg_5m_usdt + 1e-9)

        if last_5m_usdt < MIN_5M_VOLUME_USDT:
            return False, {
                "last_5m_usdt": last_5m_usdt,
                "avg_5m_usdt": avg_5m_usdt,
                "spike_ratio": spike_ratio,
            }

        if spike_ratio < MIN_VOLUME_SPIKE_RATIO:
            return False, {
                "last_5m_usdt": last_5m_usdt,
                "avg_5m_usdt": avg_5m_usdt,
                "spike_ratio": spike_ratio,
            }

        return True, {
            "last_5m_usdt": last_5m_usdt,
            "avg_5m_usdt": avg_5m_usdt,
            "spike_ratio": spike_ratio,
        }

    # ---------- spread / orderbook filter ---------- #

    def check_spread_filter(self, symbol: str):
        """
        Check that spread is within MAX_SPREAD_PCT and bid side has at least MIN_BID_NOTIONAL
        in the top DEPTH_LIMIT levels.
        """
        try:
            # Best bid/ask (cheap) – for spread
            ticker_resp = requests.get(
                f"{BINANCE_FUTURES_REST}/fapi/v1/ticker/bookTicker",
                params={"symbol": symbol},
                timeout=5,
            )
            ticker_resp.raise_for_status()
            t = ticker_resp.json()
            bid_price = float(t["bidPrice"])
            ask_price = float(t["askPrice"])

            if bid_price <= 0 or ask_price <= 0:
                return False, None

            mid = 0.5 * (bid_price + ask_price)
            spread_pct = (ask_price - bid_price) / mid

            if spread_pct > MAX_SPREAD_PCT:
                return False, {
                    "bid_price": bid_price,
                    "ask_price": ask_price,
                    "spread_pct": spread_pct,
                    "bid_notional": 0.0,
                }

            # Depth (more expensive) – for liquidity
            depth_resp = requests.get(
                f"{BINANCE_FUTURES_REST}/fapi/v1/depth",
                params={"symbol": symbol, "limit": DEPTH_LIMIT},
                timeout=5,
            )
            depth_resp.raise_for_status()
            depth = depth_resp.json()

            bids = depth.get("bids", [])
            bid_notional = 0.0
            for price, qty in bids:
                price_f = float(price)
                qty_f = float(qty)
                bid_notional += price_f * qty_f

            if bid_notional < MIN_BID_NOTIONAL:
                return False, {
                    "bid_price": bid_price,
                    "ask_price": ask_price,
                    "spread_pct": spread_pct,
                    "bid_notional": bid_notional,
                }

            return True, {
                "bid_price": bid_price,
                "ask_price": ask_price,
                "spread_pct": spread_pct,
                "bid_notional": bid_notional,
            }

        except Exception as e:
            print(f"[WARN] Spread check failed for {symbol}: {e}")
            return False, None

    # ---------- Open interest stats ---------- #

    def update_and_get_oi_change(self, symbol: str):
        """
        Poll REST openInterest for this symbol, store in a deque,
        and compute OI change over OI_WINDOW_SEC.
        This is a STAT ONLY (does not filter alerts).
        """
        now = time.time()

        try:
            resp = requests.get(
                f"{BINANCE_FUTURES_REST}/fapi/v1/openInterest",
                params={"symbol": symbol},
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            oi_now = float(data["openInterest"])
        except Exception as e:
            print(f"[WARN] OI fetch failed for {symbol}: {e}")
            return None, None, None

        dq = self.oi_history[symbol]
        dq.append((now, oi_now))

        # Evict old entries
        cutoff = now - OI_WINDOW_SEC
        while dq and dq[0][0] < cutoff:
            dq.popleft()

        # Find earliest entry within window
        oi_past = None
        for ts, oi in dq:
            if ts >= cutoff:
                oi_past = oi
                break

        if oi_past is None or oi_past <= 0:
            return None, oi_now, None

        oi_change_ratio = (oi_now / oi_past) - 1.0
        return oi_change_ratio, oi_now, oi_past


# ---------------- WEBSOCKET HANDLER ---------------- #

async def futures_mark_price_stream(bot: PumpAlertBot):
    """
    Connect to Binance Futures !markPrice@arr stream and feed prices into the bot.

    Docs: https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams
    Stream used here: !markPrice@arr (all symbols)
    """
    while True:
        try:
            async with websockets.connect(BINANCE_FUTURES_WS_URL, ping_interval=20) as ws:
                print("Connected to Binance Futures mark price stream.")
                async for msg in ws:
                    data = json.loads(msg)

                    # data format: {"stream":"!markPrice@arr","data":[{...}, {...}, ...]}
                    arr = data.get("data", [])
                    now = time.time()

                    for entry in arr:
                        # Example entry:
                        # {
                        #   "e": "markPriceUpdate",
                        #   "E": 1562305380000,
                        #   "s": "BTCUSDT",
                        #   "p": "11794.15000000",
                        #   ...
                        # }
                        symbol = entry.get("s")
                        if symbol not in bot.usdt_perp_symbols:
                            continue

                        try:
                            price = float(entry["p"])
                        except (KeyError, ValueError, TypeError):
                            continue

                        ts = now
                        bot.handle_price_update(symbol, price, ts=ts)

        except (websockets.exceptions.ConnectionClosedError,
                websockets.exceptions.ConnectionClosedOK) as e:
            print(f"WebSocket connection closed: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"Error in WebSocket loop: {e}. Reconnecting in 10s...")
            await asyncio.sleep(10)


# ---------------- MAIN ---------------- #

async def main():
    print("Fetching USDT perpetual symbols from Binance Futures...")
    usdt_perp_symbols = get_usdt_perpetual_symbols()
    print(f"Got {len(usdt_perp_symbols)} USDT perpetual symbols.")

    # Initialize Telegram bot
    if not telegram_token:
        print("Error: Telegram bot token not configured. Please set TELEGRAM_BOT_TOKEN in .env file")
        return

    if not telegram_channel_id:
        print("Error: Telegram channel ID not configured. Please set TELEGRAM_CHANNEL_ID in .env file")
        return

    telegram_bot = TelegramBot(
        token=telegram_token,
        channel_id=telegram_channel_id
    )
    print("Telegram bot initialized.")

    bot = PumpAlertBot(
        usdt_perp_symbols=usdt_perp_symbols,
        pump_windows=PUMP_WINDOWS,
        dump_windows=DUMP_WINDOWS,
        cooldown_seconds=ALERT_COOLDOWN,
        telegram_bot=telegram_bot,
    )

    await futures_mark_price_stream(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutting down...")
