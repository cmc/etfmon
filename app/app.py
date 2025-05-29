import requests
import yfinance as yf
import schedule
import time
import yaml
import hashlib
import json
import os
import sys
import datetime
from alert_emailer import send_email_alert

# --- CONFIGURATIONS ---

FAST_DEBUG = True  # <<<< Set this False for production (hourly scans)

# Load configuration
with open('config.yaml', 'r') as file:
    config = yaml.safe_load(file)

TICKERS = config['tickers']
POLYGON_API_KEY = config['polygon_api_key']
EMAIL_SETTINGS = config['email_settings']
RISK_THRESHOLDS = config['risk_thresholds']
PRINCIPAL_THRESHOLDS = config['principal_loss_thresholds']
AUM_THRESHOLDS = config['aum_thresholds']
HEARTBEAT_URL = config.get('heartbeat_url', None)
WEEKLY_REPORT_DAY = config.get('weekly_report_day', "Monday")
CAPITAL_GAINS_TAX = config.get('capital_gains_tax_rate', 0.50)
TRIM_COOLDOWN_DAYS = config.get('trim_cooldown_days', 30)

# Tracker files
ALERT_HISTORY_FILE = 'alert_history.json'
NAV_TRACKER_FILE = 'nav_tracker.json'
PORTFOLIO_FILE = 'portfolio.json'
AUM_TRACKER_FILE = 'aum_tracker.json'
MARKET_TRACKER_FILE = 'market_price_tracker.json'
TRIM_TRACKER_FILE = 'trim_tracker.json'

# Constants
TIMEOUT = 5
LOG_FILE = "output.log"

# --- Logging Utility ---
def log(msg):
    timestamp = datetime.datetime.utcnow().strftime("[%Y-%m-%d %H:%M:%S]")
    full_msg = f"{timestamp} {msg}" if msg.strip() != "" else ""
    print(full_msg)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(full_msg + "\n" if full_msg else "\n")
    except Exception as e:
        print(f"[ERROR] Failed to write to log file: {e}", file=sys.stderr)

# --- Utility Functions ---

def load_json(filename):
    if os.path.exists(filename):
        with open(filename, 'r') as f:
            return json.load(f)
    return {}

def save_json(filename, data):
    with open(filename, 'w') as f:
        json.dump(data, f, indent=2)

def hash_alert(text):
    return hashlib.sha256(text.encode()).hexdigest()

def should_send_alert(alert_hash):
    if FAST_DEBUG:
        return True  # In debug mode, always send alerts immediately

    history = load_json(ALERT_HISTORY_FILE)
    now = datetime.datetime.utcnow()
    if alert_hash not in history:
        history[alert_hash] = now.isoformat()
        save_json(ALERT_HISTORY_FILE, history)
        return True
    last_sent = datetime.datetime.fromisoformat(history[alert_hash])
    if (now - last_sent).total_seconds() > 86400:
        history[alert_hash] = now.isoformat()
        save_json(ALERT_HISTORY_FILE, history)
        return True
    return False

def send_heartbeat():
    if HEARTBEAT_URL:
        try:
            requests.get(HEARTBEAT_URL, timeout=5)
            log("💓 Heartbeat sent successfully")
        except Exception as e:
            log(f"❌ Heartbeat send failed: {e}")

# --- Smart Trim Logic ---

def find_discounted_etfs(exclude_ticker=None):
    discounts = []
    for ticker in TICKERS:
        if ticker == exclude_ticker:
            continue
        try:
            etf = yf.Ticker(ticker)
            nav = etf.info.get('navPrice', None)
            hist = etf.history(period="1d")
            if nav and not hist.empty:
                current_price = hist['Close'].iloc[-1]
                discount_pct = (nav - current_price) / nav * 100
                if discount_pct > 0:
                    discounts.append((ticker, discount_pct))
        except:
            continue
    discounts.sort(key=lambda x: x[1], reverse=True)
    return discounts[:3]

def generate_trim_email(ticker, buy_nav, current_price, gain_pct, shares, shares_to_trim, trim_value, gain_dollars, after_tax_gain, suggestions, next_alert_dt):
    suggestion_text = ""
    if suggestions:
        split_value = trim_value / len(suggestions)
        for item in suggestions:
            shares_to_buy = split_value / item['price']
            stability_note = (
                "🟢 Stable" if item['nav_stability'] > 0 else
                "🟡 Mild Drop" if item['nav_stability'] > -5 else
                "🔴 Risk"
            )
            suggestion_text += (
                f"- {item['ticker']}: Buy ~{int(shares_to_buy)} shares at ${item['price']:.2f}\n"
                f"  🔹 Discount: {item['discount_pct']:.2f}%\n"
                f"  🔹 Yield: {item['yield_pct']:.2f}%\n"
                f"  🔹 Stability: {item['nav_stability']:+.2f}% → {stability_note}\n"
            )

    return f"""
🧠 Smart Portfolio Trim Alert

Your portfolio monitor has detected a substantial gain in one of your ETF holdings.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 ETF: {ticker}
💸 Buy NAV: ${buy_nav:.2f}
📈 Current Price: ${current_price:.2f}
📊 Gain Since Purchase: +{gain_pct*100:.1f}%
📦 Total Shares Held: {shares}
✂️ Shares to Trim: {shares_to_trim}
💰 Total Cash Proceeds: ${trim_value:,.2f}
📈 Capital Gain on Sale: ${gain_dollars:,.2f}
💸 Estimated After-Tax Gain (50% CA): ${after_tax_gain:,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ Suggested Action:

Sell {shares_to_trim} shares (~{shares_to_trim/shares*100:.1f}% of position)

📥 Suggested Reinvestment Plan:
{suggestion_text or "None found"}

🕒 Cooldown active until: {next_alert_dt.strftime('%b %d, %Y')}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ Strategy: Harvest gains, rotate into discounted, high-yield, stable funds.
"""

def monitor_smart_trims():
    log("\n✂️ Checking Smart Trim Conditions...")
    portfolio = load_json(PORTFOLIO_FILE)
    trim_tracker = load_json(TRIM_TRACKER_FILE)
    now = datetime.datetime.utcnow()

    for ticker, data in portfolio.items():
        shares = data['shares']
        buy_nav = data['buy_nav']

        hist = yf.Ticker(ticker).history(period="1d")
        if hist.empty:
            continue

        current_price = hist['Close'].iloc[-1]
        gain_pct = (current_price - buy_nav) / buy_nav

        last_trim = trim_tracker.get(ticker)
        allow_trim = True

        if last_trim:
            last_trim_dt = datetime.datetime.fromisoformat(last_trim)
            if (now - last_trim_dt).days < TRIM_COOLDOWN_DAYS:
                if not FAST_DEBUG:
                    next_ok = last_trim_dt + datetime.timedelta(days=TRIM_COOLDOWN_DAYS)
                    log(f"🔕 Trim alert skipped for {ticker} — in cooldown until {next_ok.strftime('%Y-%m-%d')}")
                    allow_trim = False
                else:
                    log(f"⚠️ Trim alert cooldown bypassed for {ticker} due to FAST_DEBUG=True")

        if gain_pct >= 0.15 and allow_trim:
            trim_pct = 0.10
            if gain_pct >= 0.18:
                trim_pct = 0.15
            if gain_pct >= 0.25:
                trim_pct = 0.20

            shares_to_trim = int(shares * trim_pct)
            trim_value = shares_to_trim * current_price
            gain_dollars = (current_price - buy_nav) * shares_to_trim
            after_tax_gain = gain_dollars * (1 - CAPITAL_GAINS_TAX)
            suggestions = find_best_rotation_targets(exclude_ticker=ticker)
            next_alert_dt = now + datetime.timedelta(days=TRIM_COOLDOWN_DAYS)

            # --- Build email body ---
            email_body = generate_trim_email(
                ticker, buy_nav, current_price, gain_pct, shares,
                shares_to_trim, trim_value, gain_dollars, after_tax_gain, suggestions, next_alert_dt)

            subject_line = f"[📈 Trim Alert] {ticker} is up +{gain_pct*100:.1f}% — consider taking profits"
            if FAST_DEBUG:
                subject_line += " (FAST_DEBUG=True)"

            alert_body_hash = hash_alert(email_body)

            if FAST_DEBUG or should_send_alert(alert_body_hash):
                send_email_alert(
                    subject=subject_line,
                    body=email_body
                )
                if not FAST_DEBUG:
                    trim_tracker[ticker] = now.isoformat()
            else:
                log(f"🔕 Trim email suppressed for {ticker} (duplicate)")
    
    save_json(TRIM_TRACKER_FILE, trim_tracker)

def find_best_rotation_targets(exclude_ticker=None):
    candidates = []
    now = datetime.datetime.utcnow()

    for ticker in TICKERS:
        if ticker == exclude_ticker:
            continue

        try:
            etf = yf.Ticker(ticker)
            info = etf.info
            nav = info.get('navPrice')
            div_yield = info.get('yield') or info.get('dividendYield') or 0

            if not nav or div_yield is None or div_yield <= 0:
                continue

            hist = etf.history(period="60d")
            if hist.empty or 'Close' not in hist:
                continue

            current_price = hist['Close'].iloc[-1]
            discount_pct = (nav - current_price) / nav * 100

            nav_60d_start = hist['Close'].iloc[0]
            nav_change_pct = ((current_price - nav_60d_start) / nav_60d_start) * 100

            # Filters:
            if nav < 50000000:  # Filter out very small funds
                continue
            if nav_change_pct < -10:  # Avoid decaying NAVs
                continue
            if div_yield < 0.05:  # Filter out weak payers
                continue

            # Simple reinvestment score (balance yield and discount)
            reinvest_score = (div_yield * 100) + discount_pct - abs(nav_change_pct / 2)

            candidates.append({
                "ticker": ticker,
                "discount_pct": discount_pct,
                "yield_pct": div_yield * 100,
                "nav_stability": nav_change_pct,
                "score": reinvest_score,
                "price": current_price
            })

        except Exception as e:
            log(f"⚠️ Skipping {ticker} for reinvestment scan: {e}")
            continue

    candidates.sort(key=lambda x: x['score'], reverse=True)
    return candidates[:3]


# --- Main Execution ---

if __name__ == "__main__":
    from app_monitoring import monitor_etfs, monitor_aum
    log("✅ ETF Risk Monitor started. Running first scan now...")
    monitor_etfs()
    monitor_smart_trims()
    if FAST_DEBUG:
        schedule.every(5).minutes.do(monitor_etfs)
        schedule.every(5).minutes.do(monitor_smart_trims)
    else:
        schedule.every(1).hours.do(monitor_etfs)
        schedule.every(1).hours.do(monitor_smart_trims)

    while True:
        schedule.run_pending()
        time.sleep(60)

