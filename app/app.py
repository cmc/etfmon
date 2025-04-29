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

# Tracker files
ALERT_HISTORY_FILE = 'alert_history.json'
NAV_TRACKER_FILE = 'nav_tracker.json'
PORTFOLIO_FILE = 'portfolio.json'
AUM_TRACKER_FILE = 'aum_tracker.json'
MARKET_TRACKER_FILE = 'market_price_tracker.json'

# Constants
TIMEOUT = 5
LOG_FILE = "output.log"

# --- Logging Utility ---

def log(msg):
    timestamp = datetime.datetime.utcnow().strftime("[%Y-%m-%d %H:%M:%S]")
    full_msg = f"{timestamp} {msg}"
    print(full_msg)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(full_msg + "\n")
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

# --- Monitoring Functions ---

def fetch_polygon_price_volume(ticker):
    try:
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/prev?apiKey={POLYGON_API_KEY}"
        resp = requests.get(url, timeout=TIMEOUT)
        data = resp.json()
        if 'results' in data and len(data['results']) > 0:
            price = data['results'][0]['c']
            volume = data['results'][0]['v']
            return price, volume
        else:
            return None, None
    except Exception as e:
        log(f"❌ Error fetching Polygon for {ticker}: {e}")
        return None, None

def fetch_yahoo_nav(ticker):
    try:
        etf = yf.Ticker(ticker)
        nav = etf.info.get('navPrice', None)
        return nav
    except:
        return None

def monitor_etfs():
    log(f"🛰️ Starting monitoring cycle at {datetime.datetime.utcnow()}")

    nav_tracker = load_json(NAV_TRACKER_FILE)
    portfolio = load_json(PORTFOLIO_FILE)
    aum_tracker = load_json(AUM_TRACKER_FILE)
    market_tracker = load_json(MARKET_TRACKER_FILE)
    alerts_triggered = []

    for ticker in TICKERS:
        log(f"📈 Checking {ticker}...")
        price, volume = fetch_polygon_price_volume(ticker)
        if not price or not volume:
            log(f"⚠️ Skipping {ticker} due to fetch error.")
            continue

        nav = fetch_yahoo_nav(ticker)
        if not nav:
            log(f"⚠️ No NAV available for {ticker}, skipping NAV-based checks.")
            continue

        nav_tracker.setdefault(ticker, []).append({"date": datetime.datetime.utcnow().isoformat(), "nav": nav})
        market_tracker.setdefault(ticker, []).append({"date": datetime.datetime.utcnow().isoformat(), "price": price})

        save_json(NAV_TRACKER_FILE, nav_tracker)
        save_json(MARKET_TRACKER_FILE, market_tracker)

        # --- Log Market, NAV, Premium/Discount Info ---
        diff_pct = (price - nav) / nav * 100
        premium_discount_label = "Premium" if diff_pct > 0 else "Discount"
        log(f"🔎 {ticker}: Market=${price:.2f}, NAV=${nav:.2f}, {premium_discount_label}={abs(diff_pct):.2f}%")

        # --- Log Volume Info ---
        hist = yf.Ticker(ticker).history(period="30d")
        avg_vol = hist['Volume'].mean()
        log(f"📊 {ticker}: Today Volume={volume:,}, 30d Avg Volume={avg_vol:,.0f}")

        # --- Premium/Discount threshold alert ---
        if abs(diff_pct/100) >= RISK_THRESHOLDS['premium_discount_pct']:
            if diff_pct > 0:
                msg = f"⚡ PREMIUM detected: {ticker} trading {diff_pct:.2f}% above NAV.\n🛑 Risk: Overheating or bubble behavior possible. Review for sell opportunity."
            else:
                msg = f"⚡ DISCOUNT detected: {ticker} trading {abs(diff_pct):.2f}% below NAV.\n🛑 Risk: Potential distress or undervaluation. Review fundamentals carefully."
            alerts_triggered.append(msg)

        # --- Volume Drop Alert ---
        vol_drop = (avg_vol - volume) / avg_vol
        if vol_drop > RISK_THRESHOLDS['volume_drop_pct']:
            alerts_triggered.append(f"⚡ {ticker}: Volume dropped {vol_drop*100:.2f}% below 30d avg. Liquidity risk increasing.")

        # --- 5-Day NAV Erosion Alert ---
        recent_navs = [entry['nav'] for entry in nav_tracker[ticker][-5:]]
        if len(recent_navs) == 5 and all(recent_navs[i] > recent_navs[i+1] for i in range(4)):
            alerts_triggered.append(f"⚡ {ticker}: 5-Day consecutive NAV erosion detected. Review fund stability.")

        # --- NAV vs Market Inversion Alert ---
        if nav > price:
            alerts_triggered.append(f"⚡ {ticker}: Market price ${price:.2f} is below NAV ${nav:.2f}. Investigate for possible fund distress.")

        # --- Principal Loss Monitoring ---
        if ticker in portfolio:
            shares = portfolio[ticker]['shares']
            buy_nav = portfolio[ticker]['buy_nav']
            original_value = shares * buy_nav
            current_value = shares * price
            loss_pct = (original_value - current_value) / original_value

            if loss_pct > PRINCIPAL_THRESHOLDS['critical']:
                alerts_triggered.append(f"🛑 CRITICAL Principal Loss: {ticker} down {loss_pct*100:.2f}% from buy NAV.")
            elif loss_pct > PRINCIPAL_THRESHOLDS['danger']:
                alerts_triggered.append(f"⚠️ Danger Principal Loss: {ticker} down {loss_pct*100:.2f}% from buy NAV.")
            elif loss_pct > PRINCIPAL_THRESHOLDS['warning']:
                alerts_triggered.append(f"⚠️ Warning Principal Loss: {ticker} down {loss_pct*100:.2f}% from buy NAV.")

    monitor_aum()

    # --- Send Alerts ---
    if alerts_triggered:
        full_message = "\n\n".join(alerts_triggered)
        alert_hash = hash_alert(full_message)
        if should_send_alert(alert_hash):
            send_email_alert(subject="⚠️ ETF Risk Alert", body=full_message)
        else:
            log("🔕 Duplicate alert detected. Suppressed.")
    else:
        log("✅ No alerts this cycle.")

    send_heartbeat()

def monitor_aum():
    log("📊 Checking AUM values...")
    previous = load_json(AUM_TRACKER_FILE)
    current = {}
    alerts = []

    for ticker in TICKERS:
        try:
            etf = yf.Ticker(ticker)
            aum = etf.info.get('totalAssets', None)
            if aum:
                current[ticker] = aum
                thresholds = AUM_THRESHOLDS.get(ticker, {})
                min_aum = thresholds.get('min_aum')
                max_aum = thresholds.get('max_aum')

                if min_aum and aum < min_aum:
                    alerts.append(f"🛑 {ticker}: AUM below configured floor (${min_aum/1_000_000:.1f}M): Current ${aum/1_000_000:.1f}M.")

                if max_aum and previous.get(ticker, 0) < max_aum and aum > max_aum:
                    alerts.append(f"📈 {ticker}: AUM milestone exceeded ${max_aum/1_000_000:.1f}M! Current ${aum/1_000_000:.1f}M.")

        except Exception as e:
            log(f"❌ Error checking AUM for {ticker}: {e}")

    save_json(AUM_TRACKER_FILE, current)

    if alerts:
        body = "\n\n".join(alerts)
        alert_hash = hash_alert(body)
        if should_send_alert(alert_hash):
            send_email_alert(subject="⚠️ ETF AUM Risk Alert", body=body)
        else:
            log("🔕 Duplicate AUM alert detected. Suppressed.")

# --- MAIN LOOP ---

log("✅ ETF Risk Monitor started. Running first scan now...")
monitor_etfs()
if FAST_DEBUG:
    schedule.every(1).minutes.do(monitor_etfs)
else:
    schedule.every(1).hours.do(monitor_etfs)

while True:
    schedule.run_pending()
    time.sleep(60)

