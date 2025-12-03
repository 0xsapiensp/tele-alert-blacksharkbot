A real-time cryptocurrency futures alert bot that monitors Binance Futures for significant price pumps and dumps. The bot analyzes mark price data, applies volume and spread filters, and sends alerts via Telegram when unusual price movements are detected. Big thanks to https://x.com/blackshark0x

## Features

- 🚀 **Pump Detection**: Monitors for rapid price increases across multiple time windows
- 📉 **Dump Detection**: Alerts on significant price drops
- 📊 **Volume Analysis**: Filters alerts based on trading volume spikes
- 💹 **Spread Filtering**: Ensures sufficient liquidity before alerting
- 📈 **Open Interest Tracking**: Monitors OI changes as additional context
- 🔔 **Telegram Integration**: Real-time alerts sent to your Telegram channel
- ⚙️ **Configurable Parameters**: Easy-to-adjust detection thresholds and filters

## Requirements

- Python 3.7+
- Telegram Bot Token
- Telegram Channel ID
- Binance Futures account (for API access)

## Installation

### 1. Clone the Repository
```bash
git clone <repository-url>
cd blackshark-alert
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configuration Setup

#### Step 3.1: Configure Telegram Settings
Create a `.env` file in the project root:
```env
# Telegram Configuration
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHANNEL_ID=your_telegram_channel_id_here
```

**How to get Telegram Bot Token:**
1. Start a chat with [@BotFather](https://t.me/botfather) on Telegram
2. Send `/newbot` and follow the instructions
3. Copy the bot token provided

**How to get Channel ID:**
1. Add your bot to the channel as an administrator
2. Send a message to the channel
3. Visit `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
4. Find the `chat.id` value (will be negative for channels)

#### Step 3.2: Configure Detection Parameters

Edit `config.json` to customize detection settings:

```json
{
  "binance": {
    "futures_rest": "https://fapi.binance.com",
    "futures_exchange_info": "https://fapi.binance.com/fapi/v1/exchangeInfo",
    "futures_ws_url": "wss://fstream.binance.com/stream?streams=!markPrice@arr"
  },
  "detection": {
    "pump_windows": {
      "300": 0.3,
      "900": 2.0,
      "1800": 3.0
    },
    "dump_windows": {
      "180": -0.3,
      "300": -0.5,
      "900": -0.7
    },
    "alert_cooldown": 1800
  },
  "filters": {
    "volume": {
      "window_min": 5,
      "lookback_min": 60,
      "min_5m_volume_usdt": 20000,
      "min_spike_ratio": 2.0
    },
    "spread": {
      "max_spread_pct": 0.05,
      "depth_limit": 5,
      "min_bid_notional": 2000
    },
    "open_interest": {
      "window_sec": 900
    }
  }
}
```

## Configuration Guide

### Detection Windows (`pump_windows` & `dump_windows`)

Format: `"seconds": decimal_return`

- **pump_windows**: Time windows and minimum returns for pump detection
- **dump_windows**: Time windows and maximum returns for dump detection (negative values)

**Examples:**
```json
{
  "pump_windows": {
    "300": 0.3,    // +30% in 5 minutes
    "900": 2.0,    // +200% in 15 minutes
    "1800": 3.0    // +300% in 30 minutes
  },
  "dump_windows": {
    "180": -0.3,   // -30% in 3 minutes
    "300": -0.5,   // -50% in 5 minutes
    "900": -0.7    // -70% in 15 minutes
  }
}
```

### Volume Filters (`volume`)

- `window_min`: Minutes to look back for current volume (default: 5)
- `lookback_min`: Minutes of historical data to compare against (default: 60)
- `min_5m_volume_usdt`: Minimum absolute volume in current window (default: 20,000)
- `min_spike_ratio`: Minimum volume spike ratio vs historical average (default: 2.0x)

### Spread Filters (`spread`)

- `max_spread_pct`: Maximum allowed bid-ask spread as percentage (default: 0.05 = 5%)
- `depth_limit`: Number of orderbook levels to check for liquidity (default: 5)
- `min_bid_notional`: Minimum total bid volume in top levels (default: 2,000 USDT)

### Open Interest (`open_interest`)

- `window_sec`: Time window to measure OI changes (default: 900 = 15 minutes)

### Other Settings

- `alert_cooldown`: Seconds between alerts for same symbol+window (default: 1800 = 30 minutes)

## Running the Bot

### Option 1: Direct Execution
```bash
python main.py
```

### Option 2: Running in Background

**Linux/macOS:**
```bash
nohup python main.py > bot.log 2>&1 &
```

**Windows:**
```bash
start /b python main.py
```

### Option 3: Using Screen/Tmux
```bash
screen -S blackshark-bot
python main.py
# Press Ctrl+A, then D to detach
```

## Monitoring the Bot

The bot will output:
- Connection status messages
- Alert details in console
- Warning messages for failed API calls
- Telegram notifications for detected pumps/dumps

Sample console output:
```
================================================================================
[ALERT] DOGEUSDT pumped 45.2% over last 5m | 0.082345 -> 0.119567
  Volume last 5m: 850000 USDT | spike x4.2 (avg 202381 USDT)
  Spread: 0.03% | bid 0.119555 ask 0.119578 | top-5 bid notional ≈ 15000 USDT
  OI change (15m): 12.5% | past 12500000.0, now 14062500.0 (contracts)
================================================================================
```

## Troubleshooting

### Common Issues

1. **"config.json file not found"**
   - Ensure config.json exists in the project root
   - Check file permissions

2. **"TELEGRAM_BOT_TOKEN not found"**
   - Verify .env file exists and contains the token
   - Check for typos in variable names

3. **WebSocket connection errors**
   - Check internet connection
   - Verify Binance API is accessible from your network

4. **No alerts generated**
   - Try lowering detection thresholds in config.json
   - Check if volume filters are too restrictive
   - Verify market conditions meet criteria

### Debug Mode
Add debug prints by modifying the detection thresholds temporarily:
```json
{
  "detection": {
    "pump_windows": {
      "60": 0.05    // Lower threshold for testing
    }
  }
}
```

**Note**: This bot monitors market data and provides alerts. It does not execute trades or provide financial advice.
