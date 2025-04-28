# ETF Risk Monitoring System

## Overview

This project is a **professional-grade ETF Risk Monitoring system** built to continuously monitor a portfolio of ETFs for:

- Price deviations from NAV
- 30-day NAV decay
- Trading volume collapses
- AUM (Assets Under Management) decline

If a risk threshold is breached, **email and SMS alerts** are automatically sent — including **recommended actions** for portfolio protection.

✅ Fully Dockerized  
✅ Automatic daily AUM checks  
✅ Hourly price/volume/NAV checks  
✅ Log rotation  
✅ Designed for 24/7 unattended operation

---

## Why This Exists

- High-yield ETFs (e.g., YieldMax series) are newer and carry closure/liquidity risks.
- AUM or trading volume declines are early warning signs of trouble.
- Manually monitoring dozens of ETFs is inefficient and unreliable.
- Professional monitoring services like Bloomberg Terminal cost $24,000+/year.

This system replicates professional-grade monitoring for **almost zero cost**.

---

## Project Structure

| File | Purpose |
|------|---------|
| `app.py` | Core monitor: checks price, volume, NAV, and AUM. Runs hourly. |
| `alert_emailer.py` | Sends email and SMS alerts via Gmail SMTP |
| `build.sh` | Builds and restarts Docker container cleanly |
| `fire_monitor_once.py` | Runs a **one-time forced monitoring cycle** manually for testing |
| `test_monitor.py` | Fires a **manual test email/SMS** to verify alert system |
| `config.yaml` | Defines tickers, thresholds, email settings, and API keys |
| `aum_tracker.json` | Stores previous AUM values for comparison |
| `last_aum_check.txt` | Stores timestamp of last AUM check to avoid overchecking |
| `Dockerfile` | Defines lightweight Python environment for Docker build |
| `requirements.txt` | Python dependencies |

---

## How It Works

Every hour:

- Pull latest prices/volumes from **Polygon.io** (15-min delayed Starter plan)
- Pull latest NAVs from **Yahoo Finance**
- Detect:
  - Price/NAV deviation >2%
  - NAV decay >5% over 30 days
  - Volume drop >30% below average

Every 24 hours:

- Pull AUM values from Yahoo
- Detect:
  - AUM below $50M
  - AUM drop >20% compared to last check

✅ If any issues are detected, an **instant alert** is emailed and SMS'ed to you.

✅ Alerts include **"Recommended Actions"** depending on the issue type.

---

## Configuration

Edit `config.yaml`:

```yaml
tickers:
  - MRNY
  - TSLY
  - YETH
  - QQQY
  - FIAT
  - YMAG
  - YMAX
  - LFGY
  - GPTY
  - WDTE
  - JEPI
  - JEPQ

polygon_api_key: "your_polygon_api_key_here"

email_settings:
  smtp_server: "smtp.gmail.com"
  smtp_port: 587
  sender_email: "yourgmail@gmail.com"
  sender_password: "your_gmail_app_password"  # (From Google App Passwords, NOT your main password)
  receivers:
    - "youremail@example.com"
    - "yournumber@txt.att.net"  # SMS Gateway email address

```

## Console Logs

📈 Checking MRNY...
🔵 Polygon Aggregates Response (MRNY): 200 {'results': [...]}
💲 Final Price: 2.52, 🔄 Volume: 1,650,400, 🧮 NAV: 2.55, 📡 Data Source: Polygon (Delayed Aggregate)
🔍 Premium/Discount: 1.27%
🔍 30d NAV drop: 3.99%
🔍 Volume drop vs 30d avg: -24.11%
✅ MRNY passed all checks.

📊 FIAT AUM: 48,900,000
🚨 FIAT: AUM below $50M!
✅ Alert email sent: ⚠️ ETF AUM Risk Alert


## Example email

⚠️ ETF Risk Alert for FIAT

FIAT: NAV has dropped 12.31% over 30 days.

🛑 Recommendation:
Review fund health. Significant NAV decline could signal asset weakness.
Consider reducing exposure if trend persists.

