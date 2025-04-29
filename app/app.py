import requests
import yfinance as yf
import schedule
import time
import yaml
import hashlib
import json
import os
import datetime
from alert_emailer import send_email_alert

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
    if (now - last_sent).total_seconds() > 86400:  # 24 hours
        history[alert_hash] = now.isoformat()
        save_json(ALERT_HISTORY_FILE, history)
        return True
    return False

def send_heartbeat():
    if HEARTBEAT_URL:
        try:
            requests.get(HEARTBEAT_URL, timeout=5)
            print(f"💓 Heartbeat sent successfully")
        except Exception as e:
            print(f"❌ Heartbeat send failed: {e}")

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
        print(f"❌ Error fetching Polygon for {ticker}: {e}")
        return None, None

def fetch_yahoo_nav(ticker):
    try:
        etf = yf.Ticker(ticker)
        nav = etf.info.get('navPrice', None)
        return nav
    except:
        return None

def monitor_etfs():
    print(f"\n🛰️ Starting monitoring cycle at {datetime.datetime.utcnow()}")

    nav_tracker = load_json(NAV_TRACKER_FILE)
    portfolio = load_json(PORTFOLIO_FILE)
    aum_tracker = load_json(AUM_TRACKER_FILE)
    market_tracker = load_json(MARKET_TRACKER_FILE)
    alerts_triggered = []

    for ticker in TICKERS:
        print(f"\n📈 Checking {ticker}...")
        price, volume = fetch_polygon_price_volume(ticker)
        if not price or not volume:
            print(f"⚠️ Skipping {ticker} due to fetch error.")
            continue

        nav = fetch_yahoo_nav(ticker)
        if not nav:
            print(f"⚠️ No NAV available for {ticker}, skipping NAV-based checks.")
            continue

        # Save NAV and Market for trends
        nav_tracker.setdefault(ticker, []).append({"date": datetime.datetime.utcnow().isoformat(), "nav": nav})
        market_tracker.setdefault(ticker, []).append({"date": datetime.datetime.utcnow().isoformat(), "price": price})

        save_json(NAV_TRACKER_FILE, nav_tracker)
        save_json(MARKET_TRACKER_FILE, market_tracker)

        # --- NAV Premium/Discount ---
        premium_discount = (price - nav) / nav
        premium_discount_pct = premium_discount * 100
        if abs(premium_discount) >= RISK_THRESHOLDS['premium_discount_pct']:
            if premium_discount > 0:
                msg = f"⚡ PREMIUM detected: {ticker} trading {premium_discount_pct:.2f}% above NAV.\n"
                msg += "🛑 Recommendation: Investigate premium sustainability. Consider trimming exposure."
            else:
                msg = f"⚡ DISCOUNT detected: {ticker} trading {abs(premium_discount_pct):.2f}% below NAV.\n"
                msg += "🛑 Recommendation: Discount could signal distress. Confirm fundamentals before buying."
            alerts_triggered.append(msg)

        # --- Volume Collapse ---
        hist = yf.Ticker(ticker).history(period="30d")
        avg_vol = hist['Volume'].mean()
        vol_drop = (avg_vol - volume) / avg_vol
        if vol_drop > RISK_THRESHOLDS['volume_drop_pct']:
            alerts_triggered.append(f"⚡ {ticker}: Volume dropped {vol_drop*100:.2f}% below 30d average. Watch liquidity.")

        # --- 5-day NAV Erosion ---
        recent_navs = [entry['nav'] for entry in nav_tracker[ticker][-5:]]
        if len(recent_navs) == 5 and all(recent_navs[i] > recent_navs[i+1] for i in range(4)):
            alerts_triggered.append(f"⚡ {ticker}: 5-Day NAV erosion detected. Fund losing underlying value.")

        # --- NAV vs Market Inversion ---
        if nav > price:
            alerts_triggered.append(f"⚡ {ticker}: Market price ${price:.2f} is below NAV ${nav:.2f}. Potential fund weakness.")

        # --- Principal Loss Monitoring ---
        if ticker in portfolio:
            shares = portfolio[ticker]['shares']
            buy_nav = portfolio[ticker]['buy_nav']
            original_value = shares * buy_nav
            current_value = shares * price
            loss_pct = (original_value - current_value) / original_value

            if loss_pct > PRINCIPAL_THRESHOLDS['critical']:
                alerts_triggered.append(f"🛑 CRITICAL Principal Loss: {ticker} down {loss_pct*100:.2f}% from original investment.")
            elif loss_pct > PRINCIPAL_THRESHOLDS['danger']:
                alerts_triggered.append(f"⚠️ Danger Principal Loss: {ticker} down {loss_pct*100:.2f}% from original investment.")
            elif loss_pct > PRINCIPAL_THRESHOLDS['warning']:
                alerts_triggered.append(f"⚠️ Warning Principal Loss: {ticker} down {loss_pct*100:.2f}% from original investment.")

    # --- AUM Monitoring ---
    monitor_aum()

    # --- Send Alerts ---
    if alerts_triggered:
        full_message = "\n\n".join(alerts_triggered)
        alert_hash = hash_alert(full_message)
        if should_send_alert(alert_hash):
            send_email_alert(subject="⚠️ ETF Risk Alert", body=full_message)
        else:
            print("🔕 Duplicate alert detected. Suppressed.")
    else:
        print("✅ No alerts this cycle.")

    send_heartbeat()

def monitor_aum():
    print("\n📊 Checking AUM values...")
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
            print(f"❌ Error checking AUM for {ticker}: {e}")

    save_json(AUM_TRACKER_FILE, current)

    if alerts:
        body = "\n\n".join(alerts)
        alert_hash = hash_alert(body)
        if should_send_alert(alert_hash):
            send_email_alert(subject="⚠️ ETF AUM Risk Alert", body=body)
        else:
            print("🔕 Duplicate AUM alert detected. Suppressed.")

# --- MAIN LOOP ---

print("✅ ETF Risk Monitor started. Running first scan now...")
monitor_etfs()
schedule.every(1).hours.do(monitor_etfs)

while True:
    schedule.run_pending()
    time.sleep(60)

