import requests
import yfinance as yf
import schedule
import time
import yaml
import os
import json
import datetime
from alert_emailer import send_email_alert

# Load configuration
with open('config.yaml', 'r') as file:
    config = yaml.safe_load(file)

TICKERS = config['tickers']
POLYGON_API_KEY = config['polygon_api_key']
EMAIL_SETTINGS = config['email_settings']
RISK_THRESHOLDS = config['risk_thresholds']

# Settings
TIMEOUT = 5  # seconds for API requests
AUM_MINIMUM = 50_000_000  # $50M alert threshold
AUM_DROP_PERCENT = 0.20   # 20% drop alert
AUM_TRACK_FILE = 'aum_tracker.json'
LAST_AUM_CHECK_FILE = 'last_aum_check.txt'

# Utility Functions for AUM Tracking
def load_previous_aum():
    if os.path.exists(AUM_TRACK_FILE):
        with open(AUM_TRACK_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_current_aum(current_aum):
    with open(AUM_TRACK_FILE, 'w') as f:
        json.dump(current_aum, f)

def load_last_aum_check_time():
    if os.path.exists(LAST_AUM_CHECK_FILE):
        with open(LAST_AUM_CHECK_FILE, 'r') as f:
            return datetime.datetime.fromisoformat(f.read().strip())
    return None

def save_last_aum_check_time():
    with open(LAST_AUM_CHECK_FILE, 'w') as f:
        f.write(datetime.datetime.utcnow().isoformat())

# Fetch Functions
def fetch_polygon_price_volume(ticker):
    try:
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/prev?apiKey={POLYGON_API_KEY}"
        resp = requests.get(url, timeout=TIMEOUT)
        print(f"🔵 Polygon Aggregates Response ({ticker}): {resp.status_code} {resp.json()}")
        data = resp.json()

        if 'results' in data and len(data['results']) > 0:
            price = data['results'][0]['c']  # Close price
            volume = data['results'][0]['v']  # Volume
            return price, volume, "Polygon (Delayed Aggregate)"
        else:
            print(f"⚠️ Polygon aggregates missing data for {ticker}, falling back to Yahoo Finance...")
            return None, None, "Yahoo"
    except Exception as e:
        print(f"❌ Error fetching Polygon aggregates for {ticker}: {e}")
        return None, None, "Yahoo"

def fetch_yahoo_price_volume(ticker):
    try:
        etf = yf.Ticker(ticker)
        hist = etf.history(period="1d")
        price = hist['Close'].iloc[-1]
        volume = hist['Volume'].iloc[-1]
        print(f"🟡 Yahoo Fallback ({ticker}): Price={price}, Volume={volume}")
        return price, volume
    except Exception as e:
        print(f"❌ Error fetching from Yahoo for {ticker}: {e}")
        return None, None

def fetch_yahoo_nav(ticker):
    try:
        etf = yf.Ticker(ticker)
        nav = etf.info.get('navPrice', None)
        if nav:
            print(f"🟡 Yahoo NAV for {ticker}: {nav}")
        else:
            print(f"⚠️ No NAV available for {ticker} on Yahoo")
        return nav
    except Exception as e:
        print(f"❌ Error fetching NAV from Yahoo for {ticker}: {e}")
        return None

# Core Monitoring Functions
def monitor_etfs():
    for ticker in TICKERS:
        print(f"\n📈 Checking {ticker}...")

        price, volume, source = fetch_polygon_price_volume(ticker)
        if price is None or volume is None:
            price, volume = fetch_yahoo_price_volume(ticker)
            source = "Yahoo"

        if price is None or volume is None:
            print(f"⚠️ Skipping {ticker} due to data fetch failure.")
            continue

        nav = fetch_yahoo_nav(ticker)

        print(f"💲 Final Price: {price}, 🔄 Final Volume: {volume}, 🧮 NAV: {nav if nav else 'Unavailable'}, 📡 Data Source Used: {source}")

        message = ""

        # Premium/Discount Check
        if nav:
            premium_discount = abs(price - nav) / nav
            print(f"🔍 Premium/Discount: {premium_discount*100:.2f}%")
            if premium_discount > RISK_THRESHOLDS['premium_discount_pct']:
                message += f"{ticker}: Premium/Discount exceeds {RISK_THRESHOLDS['premium_discount_pct']*100:.2f}%.\n"

        # NAV Decay Check
        if nav:
            hist = yf.Ticker(ticker).history(period="30d")
            nav_30d_avg = hist['Close'].mean()
            nav_drop = (nav_30d_avg - nav) / nav_30d_avg
            print(f"🔍 30d NAV drop: {nav_drop*100:.2f}%")
            if nav_drop > RISK_THRESHOLDS['nav_decay_pct']:
                message += f"{ticker}: NAV has dropped {nav_drop*100:.2f}% over 30 days.\n"

        # Volume Drop Check
        hist = yf.Ticker(ticker).history(period="30d")
        avg_vol = hist['Volume'].mean()
        vol_drop = (avg_vol - volume) / avg_vol
        print(f"🔍 Volume drop vs 30d avg: {vol_drop*100:.2f}%")
        if vol_drop > RISK_THRESHOLDS['volume_drop_pct']:
            message += f"{ticker}: Volume dropped {vol_drop*100:.2f}% compared to 30d avg.\n"

        if message:
            message += "\n🛑 Recommendation: Review NAV or market conditions immediately. Confirm ETF solvency and assess reducing exposure."
            print(f"🚨 Sending ALERT for {ticker}!\n{message}")
            send_email_alert(subject=f"⚠️ ETF Risk Alert for {ticker}", body=message)
        else:
            print(f"✅ {ticker} passed all checks. No alert needed.")

    # Now check AUM once a day
    try:
        last_check = load_last_aum_check_time()
        now = datetime.datetime.utcnow()
        if not last_check or (now - last_check).total_seconds() > 86400:
            monitor_aum()
        else:
            print(f"✅ AUM check not needed yet. Last checked at {last_check}.")
    except Exception as e:
        print(f"❌ Error during AUM timing check: {e}")

def monitor_aum():
    print("\n🔎 Starting AUM Monitor Check...")
    previous_aum = load_previous_aum()
    current_aum = {}
    alert_message = ""

    for ticker in TICKERS:
        try:
            etf = yf.Ticker(ticker)
            aum = etf.info.get('totalAssets', None)
            print(f"📊 {ticker} AUM: {aum}")

            if aum is None:
                continue

            current_aum[ticker] = aum

            prev = previous_aum.get(ticker)

            if aum < AUM_MINIMUM:
                alert_message += f"{ticker}: AUM below $50M! Current AUM: ${aum:,}\n"

            if prev:
                drop_pct = (prev - aum) / prev
                if drop_pct > AUM_DROP_PERCENT:
                    alert_message += f"{ticker}: AUM dropped {drop_pct*100:.2f}% since last check! Previous: ${prev:,}, Current: ${aum:,}\n"

        except Exception as e:
            print(f"❌ Error fetching AUM for {ticker}: {e}")

    if alert_message:
        alert_message += "\n🛑 Recommendation: Review fund stability. Declining AUM may indicate liquidation risk. Consider adjusting exposure."
        send_email_alert(subject="⚠️ ETF AUM Risk Alert", body=alert_message)
    else:
        print("✅ No AUM issues detected.")

    save_current_aum(current_aum)
    save_last_aum_check_time()

# --- MAIN ---

print("✅ ETF Risk Monitor started... Running first full check immediately.")
monitor_etfs()
schedule.every(1).hours.do(monitor_etfs)

while True:
    schedule.run_pending()
    time.sleep(60)

