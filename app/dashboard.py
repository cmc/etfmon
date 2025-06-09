import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import datetime
import pytz
import numpy as np
from app import load_json, TICKERS, RISK_THRESHOLDS
import feedparser
from textblob import TextBlob
import requests
from bs4 import BeautifulSoup
import time
import re
from calendar import monthrange
from datetime import timedelta
import threading
import json
import os
from pathlib import Path
import yfinance as yf
import hashlib

# --- Constants ---
NEWS_CACHE_FILE = "news_cache.json"
ARTICLE_CACHE_DIR = "article_cache"
DIVIDEND_HISTORY_DIR = "dividend_history"
NEWS_UPDATE_INTERVAL = 3600  # 1 hour in seconds
ARTICLE_CACHE_DURATION = 7 * 24 * 3600  # 7 days in seconds
TICKER_DELAY = 3  # seconds between ticker updates
DIVIDEND_UPDATE_INTERVAL = 7 * 24 * 3600  # 7 days in seconds
MAX_DIVIDEND_RETRIES = 3  # Maximum number of retries for dividend fetching
MAX_RETRIES = 3

# Color constants for position status
COLORS = {
    'healthy': {
        'bg': '#28a745',
        'text': '#ffffff',
        'shadow': '0 1px 3px rgba(0,0,0,0.3)'
    },
    'at_risk': {
        'bg': '#fd7e14',  # Bootstrap orange
        'text': '#ffffff',
        'shadow': '0 1px 3px rgba(0,0,0,0.3)',
        'border': '#dc6502'  # Darker orange border
    },
    'high_risk': {
        'bg': '#dc3545',
        'text': '#ffffff',
        'shadow': '0 1px 3px rgba(0,0,0,0.3)'
    }
}

# Ensure cache directories exist
os.makedirs(ARTICLE_CACHE_DIR, exist_ok=True)
os.makedirs(DIVIDEND_HISTORY_DIR, exist_ok=True)

# --- Page Config ---
st.set_page_config(
    page_title="YieldMax ETF Risk Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# --- Custom CSS ---
st.markdown("""
<style>
    /* Modern color palette */
    :root {
        --primary-color: #2E3440;
        --secondary-color: #4C566A;
        --accent-color: #88C0D0;
        --text-color: #D8DEE9;
        --background-color: #2E3440;
    }
    
    /* Main container */
    .stApp {
        background-color: var(--background-color);
    }
    
    /* Headers */
    h1, h2, h3, h4, h5, h6 {
        font-family: 'Inter', sans-serif;
        color: var(--text-color) !important;
        font-weight: 600 !important;
    }
    
    /* Text elements */
    p, span, div {
        font-family: 'Inter', sans-serif;
        color: var(--text-color);
    }
    
    /* DataFrames */
    .dataframe {
        font-family: 'Inter', sans-serif !important;
        border-radius: 10px !important;
        border: none !important;
    }
    
    /* Buttons */
    .stButton>button {
        border-radius: 8px !important;
        background-color: var(--accent-color) !important;
        color: var(--primary-color) !important;
        border: none !important;
        padding: 10px 20px !important;
        font-weight: 600 !important;
        transition: all 0.3s ease !important;
    }
    
    .stButton>button:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 4px 12px rgba(136, 192, 208, 0.2) !important;
    }
    
    /* Cards/Containers */
    div[data-testid="stMetric"] {
        background-color: var(--secondary-color);
        padding: 20px;
        border-radius: 10px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
    }
    
    /* Metrics */
    div[data-testid="stMetricValue"] {
        font-size: 24px !important;
        font-weight: 600 !important;
    }
    
    div[data-testid="stMetricDelta"] {
        font-size: 16px !important;
    }
    
    /* Tables */
    .dataframe th {
        background-color: var(--secondary-color) !important;
        color: var(--text-color) !important;
        font-weight: 600 !important;
        padding: 12px !important;
    }
    
    .dataframe td {
        padding: 10px !important;
    }
    
    /* Hover effects */
    .dataframe tr:hover {
        background-color: rgba(136, 192, 208, 0.1) !important;
    }
    
    /* Scrollbars */
    ::-webkit-scrollbar {
        width: 8px;
        height: 8px;
    }
    
    ::-webkit-scrollbar-track {
        background: var(--primary-color);
        border-radius: 4px;
    }
    
    ::-webkit-scrollbar-thumb {
        background: var(--accent-color);
        border-radius: 4px;
    }
    
    ::-webkit-scrollbar-thumb:hover {
        background: var(--text-color);
    }
</style>
""", unsafe_allow_html=True)

# --- Helper Functions ---
def calculate_nav_stability(nav_history, window=5):
    """Calculate NAV stability score based on recent trend"""
    if len(nav_history) < window:
        return 0
    
    recent_navs = [entry['nav'] for entry in nav_history[-window:]]
    consecutive_drops = sum(1 for i in range(len(recent_navs)-1) if recent_navs[i] > recent_navs[i+1])
    return consecutive_drops / (window - 1)  # Normalize to 0-1

def calculate_aum_risk(aum, threshold=50_000_000):
    """Calculate AUM risk score based on size"""
    if aum == 'N/A' or aum is None:
        return 1
    return max(0, min(1, 1 - (aum / threshold)))

def get_risk_color(value):
    """Return color based on risk value (0-1)"""
    if value >= 0.7:
        return "red"
    elif value >= 0.4:
        return "orange"
    return "green"

def get_sentiment_score(text):
    """Calculate sentiment score from text using TextBlob"""
    blob = TextBlob(text)
    return blob.sentiment.polarity



# --- Cache Management ---
def load_news_cache():
    """Load the news cache from file"""
    try:
        if os.path.exists(NEWS_CACHE_FILE):
            with open(NEWS_CACHE_FILE, 'r') as f:
                cache = json.load(f)
                # Convert timestamp strings to datetime objects
                for ticker in cache:
                    cache[ticker]['last_update'] = datetime.datetime.fromisoformat(cache[ticker]['last_update'])
                return cache
        return {}
    except Exception as e:
        st.warning(f"Error loading news cache: {str(e)}")
        return {}

def save_news_cache(cache):
    """Save the news cache to file"""
    try:
        # Convert datetime objects to ISO format strings
        cache_copy = {}
        for ticker in cache:
            cache_copy[ticker] = cache[ticker].copy()
            cache_copy[ticker]['last_update'] = cache[ticker]['last_update'].isoformat()
        
        with open(NEWS_CACHE_FILE, 'w') as f:
            json.dump(cache_copy, f)
    except Exception as e:
        st.warning(f"Error saving news cache: {str(e)}")

def get_cached_article_content(url):
    """Get article content from cache or fetch if needed"""
    try:
        # Create a unique filename from the URL
        url_hash = hashlib.md5(url.encode()).hexdigest()
        cache_file = os.path.join(ARTICLE_CACHE_DIR, f"{url_hash}.json")
        
        # Check if we have a cached version
        if os.path.exists(cache_file):
            file_age = time.time() - os.path.getmtime(cache_file)
            if file_age < ARTICLE_CACHE_DURATION:
                with open(cache_file, 'r') as f:
                    cached_data = json.load(f)
                return cached_data.get('content', ''), cached_data.get('sentiment', 0)
        
        # If not cached or too old, fetch new content
        content = fetch_article_content(url)
        if content:
            sentiment = analyze_article_sentiment(content)
            
            # Cache the result
            cache_data = {
                'url': url,
                'content': content,
                'sentiment': sentiment,
                'cached_at': datetime.datetime.now(pacific_tz).isoformat()
            }
            with open(cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
            
            return content, sentiment
        
        return '', 0
        
    except Exception as e:
        st.warning(f"Error getting article content: {str(e)}")
        return '', 0

def fetch_article_content(url):
    """Fetch and extract article content with rate limiting"""
    try:
        # Check rate limiting
        if hasattr(st.session_state, 'article_requests'):
            if st.session_state.article_requests.get('count', 0) >= 10:  # Max 10 requests per hour
                last_request = st.session_state.article_requests.get('last_time', 0)
                if time.time() - last_request < 3600:  # If less than an hour has passed
                    return ''
                st.session_state.article_requests['count'] = 0
        else:
            st.session_state.article_requests = {'count': 0, 'last_time': time.time()}

        # Increment request counter
        st.session_state.article_requests['count'] += 1
        st.session_state.article_requests['last_time'] = time.time()

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Remove script and style elements
        for script in soup(['script', 'style']):
            script.decompose()
        
        # Find the main article content
        article_content = ''
        
        # Try different common article content selectors
        content_selectors = [
            'article',
            '[class*="article"]',
            '[class*="content"]',
            'main',
            '.post-content',
            '#article-body'
        ]
        
        for selector in content_selectors:
            content = soup.select(selector)
            if content:
                article_content = ' '.join(p.get_text().strip() for p in content)
                if len(article_content) > 200:  # Minimum content length
                    break
        
        # If no content found, try paragraphs
        if not article_content:
            paragraphs = soup.find_all('p')
            article_content = ' '.join(p.get_text().strip() for p in paragraphs)
        
        # Clean up the content
        article_content = re.sub(r'\s+', ' ', article_content).strip()
        article_content = re.sub(r'[^\w\s.,!?-]', '', article_content)
        
        return article_content if len(article_content) > 200 else ''
        
    except Exception as e:
        st.warning(f"Error fetching article content: {str(e)}")
        return ''

def analyze_article_sentiment(content):
    """Analyze sentiment of article content"""
    try:
        if not content:
            return 0
            
        # Split content into chunks to handle long articles
        chunk_size = 1000
        chunks = [content[i:i + chunk_size] for i in range(0, len(content), chunk_size)]
        
        # Analyze sentiment for each chunk
        sentiments = []
        for chunk in chunks:
            blob = TextBlob(chunk)
            sentiments.append(blob.sentiment.polarity)
        
        # Return average sentiment
        return sum(sentiments) / len(sentiments) if sentiments else 0
        
    except Exception as e:
        st.warning(f"Error analyzing sentiment: {str(e)}")
        return 0

def update_news_for_ticker(ticker, cache):
    """Update news for a single ticker"""
    try:
        xml_url = f"https://seekingalpha.com/api/sa/combined/{ticker}.xml"
        feed = feedparser.parse(xml_url)
        
        if feed.entries:
            latest = feed.entries[0]
            
            # Clean up the title
            title = re.sub(r'<[^>]+>', '', latest.title)
            title = ' '.join(title.split())
            
            # Get the link and fetch article content
            link = latest.link
            content, sentiment_score = get_cached_article_content(link)
            
            # Get the publication date
            pub_date = datetime.datetime(*latest.published_parsed[:6])
            pacific_date = pub_date.astimezone(pacific_tz)
            
            cache[ticker] = {
                'title': title,
                'link': link,
                'date': pacific_date.strftime('%Y-%m-%d'),
                'sentiment': sentiment_score,
                'content_summary': content[:500] + '...' if len(content) > 500 else content,
                'last_update': datetime.datetime.now(pacific_tz)
            }
        else:
            cache[ticker] = {
                'title': f"No recent news available for {ticker}",
                'link': f"https://seekingalpha.com/symbol/{ticker}",
                'date': datetime.datetime.now(pacific_tz).strftime('%Y-%m-%d'),
                'sentiment': 0,
                'content_summary': '',
                'last_update': datetime.datetime.now(pacific_tz)
            }
        
        save_news_cache(cache)
        return True
    except Exception as e:
        st.warning(f"Error updating news for {ticker}: {str(e)}")
        return False

@st.cache_data(ttl=24*3600)  # Cache for 24 hours
def get_dividend_history(ticker):
    """Get dividend history from cache, only fetch from API if absolutely necessary"""
    try:
        file_path = os.path.join(DIVIDEND_HISTORY_DIR, f"{ticker}.json")
        
        # Check if file exists and is recent enough
        if os.path.exists(file_path):
            file_age = time.time() - os.path.getmtime(file_path)
            
            # Load from file if it's newer than DIVIDEND_UPDATE_INTERVAL
            if file_age < DIVIDEND_UPDATE_INTERVAL:
                with open(file_path, 'r') as f:
                    return json.load(f)
            
            # If file exists but is old, load it anyway but mark for update
            with open(file_path, 'r') as f:
                data = json.load(f)
                if data:  # If we have any data, use it
                    st.session_state.setdefault('dividend_update_queue', set()).add(ticker)
                    return data
        
        # If we get here, we need to fetch from API
        return fetch_dividend_data(ticker)
    
    except Exception as e:
        st.warning(f"Error loading dividend history for {ticker}: {str(e)}")
        return []

def fetch_dividend_data(ticker):
    """Fetch dividend data from YFinance with retries and rate limiting"""
    try:
        # Check if we've exceeded API calls
        if hasattr(st.session_state, 'yf_api_calls'):
            if st.session_state.yf_api_calls.get('count', 0) >= 5:  # Max 5 calls per session
                last_call = st.session_state.yf_api_calls.get('last_time', 0)
                if time.time() - last_call < 3600:  # If less than an hour has passed
                    return []
                st.session_state.yf_api_calls['count'] = 0
        else:
            st.session_state.yf_api_calls = {'count': 0, 'last_time': time.time()}

        # Increment API call counter
        st.session_state.yf_api_calls['count'] += 1
        st.session_state.yf_api_calls['last_time'] = time.time()

        file_path = os.path.join(DIVIDEND_HISTORY_DIR, f"{ticker}.json")
        
        # Load existing data if any
        existing_data = []
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                existing_data = json.load(f)
        
        # Get the last dividend date if we have data
        last_date = None
        if existing_data:
            last_date = datetime.datetime.strptime(
                max(d['date'] for d in existing_data),
                '%Y-%m-%d'
            ).date()
        
        # Only fetch new data if necessary
        stock = yf.Ticker(ticker)
        if last_date:
            # Only get dividends since last known date
            dividends = stock.dividends.loc[last_date:]
        else:
            # Get all dividends if we have no data
            dividends = stock.dividends
        
        if dividends is not None and not dividends.empty:
            # Convert dividends to list of dicts
            new_data = []
            for date, amount in dividends.items():
                div_date = date.date()
                if not last_date or div_date > last_date:
                    new_data.append({
                        'date': div_date.strftime('%Y-%m-%d'),
                        'amount': float(amount)
                    })
            
            # Combine and sort data
            all_data = existing_data + new_data
            all_data.sort(key=lambda x: x['date'])
            
            # Save to file
            with open(file_path, 'w') as f:
                json.dump(all_data, f, indent=2)
            
            return all_data
        
        return existing_data or []
    
    except Exception as e:
        st.warning(f"Error fetching dividend data for {ticker}: {str(e)}")
        return existing_data if existing_data else []

def process_dividend_update_queue():
    """Process queued dividend updates in background"""
    if hasattr(st.session_state, 'dividend_update_queue'):
        update_queue = st.session_state.dividend_update_queue
        if update_queue:
            ticker = update_queue.pop()
            fetch_dividend_data(ticker)

def background_updates():
    """Combined background thread for news and dividend updates"""
    while True:
        try:
            # Update news
            cache = load_news_cache()
            portfolio_data = load_json("portfolio.json")
            if portfolio_data:
                tickers = list(portfolio_data.keys())
                
                for ticker in tickers:
                    current_time = datetime.datetime.now(pacific_tz)
                    
                    # Check if ticker needs news update
                    if (ticker not in cache or 
                        (current_time - cache[ticker]['last_update']).total_seconds() >= NEWS_UPDATE_INTERVAL):
                        update_news_for_ticker(ticker, cache)
                        time.sleep(TICKER_DELAY)
            
            # Process one dividend update from queue if any
            process_dividend_update_queue()
            
            time.sleep(60)  # Check for updates every minute
            
        except Exception as e:
            st.warning(f"Error in background updates: {str(e)}")
            time.sleep(60)  # Wait before retrying

# Replace the old background thread initialization
if 'background_updater' not in st.session_state:
    updater = threading.Thread(target=background_updates, daemon=True)
    updater.start()
    st.session_state['background_updater'] = updater

# Replace the old fetch_seeking_alpha_news function
@st.cache_data(ttl=3600)  # Cache for 1 hour
def fetch_seeking_alpha_news(tickers):
    """Get news items from cache with Streamlit caching"""
    try:
        cache = load_news_cache()
        news_items = []
        current_time = datetime.datetime.now(pacific_tz)
        
        # Load portfolio data
        portfolio_data = load_json("portfolio.json")
        if not portfolio_data:
            return []
            
        for ticker in portfolio_data.keys():
            # If ticker not in cache or cache is old, update immediately
            if (ticker not in cache or 
                (current_time - cache[ticker]['last_update']).total_seconds() >= NEWS_UPDATE_INTERVAL):
                if update_news_for_ticker(ticker, cache):
                    time.sleep(TICKER_DELAY)
            
            # Add to news items
            if ticker in cache:
                item = cache[ticker].copy()
                item['ticker'] = ticker
                news_items.append(item)
        
        return news_items
    except Exception as e:
        st.warning(f"Error getting news items: {str(e)}")
        return []

def get_sentiment_color(score):
    """Return color based on sentiment score"""
    if score > 0.2:
        return "#28a745"  # Green
    elif score < -0.2:
        return "#dc3545"  # Red
    return "#ffc107"  # Yellow

def analyze_dividend_history(ticker):
    """Analyze dividend history to predict next payout"""
    try:
        # Get dividend history with caching
        div_history = get_dividend_history(ticker)
        if not div_history:
            return None
        
        # Convert to pandas DataFrame
        df = pd.DataFrame(div_history)
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date')
        
        # Get the most recent dividend
        last_div = df.iloc[-1]
        last_amount = last_div['amount']
        last_date = last_div['date']
        
        # Calculate average days between payments
        df['days_between'] = df['date'].diff().dt.days
        avg_days = df['days_between'].mean()
        
        # Calculate next expected date
        next_date = last_date + timedelta(days=round(avg_days))
        
        # Calculate 3-month moving average for amount prediction
        df['amount_ma3'] = df['amount'].rolling(3).mean()
        predicted_amount = df['amount_ma3'].iloc[-1]
        
        # Calculate monthly total based on predicted amount
        monthly_total = predicted_amount
        
        # Calculate weekly average
        weekly_avg = monthly_total / 4.33  # Average weeks per month
        
        return {
            'ticker': ticker,
            'last_date': last_date,
            'last_amount': last_amount,
            'next_date': next_date,
            'predicted_amount': predicted_amount,
            'monthly_total': monthly_total,
            'weekly_avg': weekly_avg
        }
    except Exception as e:
        st.warning(f"Could not analyze dividends for {ticker}: {str(e)}")
        return None

def create_dividend_income_widget(metrics):
    """Create the dividend income metrics widget."""
    st.markdown("### 💰 Dividend Income")

    # Income metrics
    st.markdown(f"""
    <div style='color: #88C0D0'>
        <div style='display: flex; justify-content: space-between; margin: 10px 0;'>
            <span>Weekly:</span>
            <span style='color: #D8DEE9; font-weight: 600'>${metrics['weekly']:,.2f}</span>
        </div>
        <div style='display: flex; justify-content: space-between; margin: 10px 0;'>
            <span>Monthly:</span>
            <span style='color: #D8DEE9; font-weight: 600'>${metrics['monthly']:,.2f} {format_trend_indicator(metrics['monthly_trend'])}</span>
        </div>
        <div style='display: flex; justify-content: space-between; margin: 10px 0;'>
            <span>Annual:</span>
            <span style='color: #D8DEE9; font-weight: 600'>${metrics['annual']:,.2f} {format_trend_indicator(metrics['annual_trend'])}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Portfolio stats
    st.markdown(f"""
    <div style='margin: 15px 0; padding-top: 15px; border-top: 1px solid #3B4252; text-align: center; color: #88C0D0'>
        {metrics['positions']} dividend-paying positions<br>
        Monthly change: {format_trend_indicator(metrics['monthly_trend'], include_value=True)}
    </div>
    """, unsafe_allow_html=True)

def create_upcoming_payments_widget(calendar_data):
    """Create the upcoming payments calendar widget."""
    st.markdown("### 📅 Upcoming Payments")
    
    current_month = calendar_data[0] if calendar_data else None
    if current_month and current_month['dividends']:
        st.markdown(f"#### {current_month['month']}")
        
        # Sort payments by day
        payments = sorted(current_month['dividends'], key=lambda x: x['day'])
        for payment in payments:
            st.markdown(f"""
            <div style='display: flex; justify-content: space-between; margin: 8px 0; color: #88C0D0'>
                <span>Day {payment['day']}: {payment['ticker']}</span>
                <span style='color: #A3BE8C; font-weight: 600'>${payment['amount']:,.2f}</span>
            </div>
            """, unsafe_allow_html=True)
        
        # Month total
        st.markdown(f"""
        <div style='margin: 15px 0; padding-top: 15px; border-top: 1px solid #3B4252'>
            <div style='display: flex; justify-content: space-between'>
                <span style='color: #88C0D0; font-weight: 600'>Month Total:</span>
                <span style='color: #A3BE8C; font-weight: 600'>${current_month['total']:,.2f}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("<div style='color: #88C0D0; text-align: center; font-style: italic'>No upcoming dividend payments scheduled</div>", unsafe_allow_html=True)

def create_dividend_forecast_section(portfolio):
    """Create the dividend forecast section"""
    metrics = calculate_portfolio_dividend_metrics(portfolio)
    
    # Generate calendar data
    calendar_data = []
    current_month = datetime.datetime.now(pacific_tz).replace(day=1)
    
    for _ in range(2):  # Next 2 months
        month_name = current_month.strftime("%B %Y")
        month_divs = []
        month_total = 0
        
        days_in_month = monthrange(current_month.year, current_month.month)[1]
        
        for ticker, position in portfolio.items():
            div_history = analyze_dividend_history(ticker)
            if div_history and div_history['next_date']:
                next_date = div_history['next_date']
                if (next_date.year == current_month.year and 
                    next_date.month == current_month.month):
                    amount = position['shares'] * div_history['predicted_amount']
                    month_total += amount
                    month_divs.append({
                        'day': next_date.day,
                        'ticker': ticker,
                        'amount': amount
                    })
        
        calendar_data.append({
            'month': month_name,
            'total': month_total,
            'dividends': month_divs
        })
        
        current_month = (current_month + timedelta(days=days_in_month)).replace(day=1)

    return metrics, calendar_data

def calculate_portfolio_dividend_metrics(portfolio):
    """Calculate portfolio-wide dividend metrics with trends"""
    total_monthly = 0
    total_weekly = 0
    total_annual = 0
    prev_monthly = 0  # Previous month's rate
    prev_annual = 0   # Previous annual rate
    dividend_positions = 0
    
    for ticker in portfolio.keys():
        forecast = analyze_dividend_history(ticker)
        if forecast:
            shares = portfolio[ticker]['shares']
            monthly = forecast['monthly_total'] * shares
            total_monthly += monthly
            total_weekly += forecast['weekly_avg'] * shares
            total_annual += monthly * 12
            
            # Calculate previous month's metrics
            if 'last_amount' in forecast and forecast['last_amount']:
                prev_month = forecast['last_amount'] * shares
                prev_monthly += prev_month
                prev_annual += prev_month * 12
            
            dividend_positions += 1
    
    # Calculate trend percentages
    monthly_trend = ((total_monthly - prev_monthly) / prev_monthly * 100) if prev_monthly > 0 else 0
    annual_trend = ((total_annual - prev_annual) / prev_annual * 100) if prev_annual > 0 else 0
    
    return {
        'monthly': total_monthly,
        'weekly': total_weekly,
        'annual': total_annual,
        'positions': dividend_positions,
        'monthly_trend': monthly_trend,
        'annual_trend': annual_trend,
        'prev_monthly': prev_monthly,
        'prev_annual': prev_annual,
        'weekly_income': total_weekly,
        'monthly_income': total_monthly,
        'annual_income': total_annual,
        'portfolio_yield': ((total_monthly + total_weekly + total_annual) / sum(position['shares'] * position['buy_nav'] for position in portfolio.values()) * 100)
    }

def format_trend_indicator(value, include_value=True):
    """Format trend indicator with arrow and color"""
    if abs(value) < 0.01:  # No significant change
        arrow = "→"
        color = "#D8DEE9"  # Neutral color
    elif value > 0:
        arrow = "↑"
        color = "#A3BE8C"  # Green
    else:
        arrow = "↓"
        color = "#BF616A"  # Red
    
    if include_value:
        return f'<span style="color: {color}">{arrow} {abs(value):.1f}%</span>'
    return f'<span style="color: {color}">{arrow}</span>'

def create_position_table(positions, status, color_key, action_text):
    """Create a formatted table for a group of positions"""
    if not positions:
        return
        
    color = COLORS[color_key]
    
    st.markdown(f"""
    <div style='background-color: {color['bg']}; 
                padding: 20px; 
                border-radius: 10px; 
                margin-bottom: 20px;
                border: 1px solid {color.get("border", color["bg"])};'>
        <h3 style='color: {color["text"]}; 
                   margin: 0; 
                   text-shadow: {color["shadow"]};
                   font-weight: 600;'>{status} ({len(positions)} positions)</h3>
    </div>
    """, unsafe_allow_html=True)
    
    position_data = []
    for pos in positions:
        current_price = pos['current_price']
        buy_nav = pos['buy_nav']
        shares = pos['shares']
        current_value = pos['current_value']
        initial_value = pos['initial_value']
        dollar_change = current_value - initial_value
        gain_pct = pos['gain_pct']
        
        # Get latest NAV from tracker
        latest_nav = nav_tracker.get(pos['ticker'], [])[-1]['nav'] if nav_tracker.get(pos['ticker'], []) else None
        
        # Calculate premium/discount to NAV
        nav_premium_pct = ((current_price - latest_nav) / latest_nav * 100) if latest_nav else None
        
        position_data.append({
            "Ticker": pos['ticker'],
            "Current Price": current_price,
            "Current Price Display": f"${current_price:.2f}",
            "Buy Price": buy_nav,
            "Buy Price Display": f"${buy_nav:.2f}",
            "Current NAV": latest_nav,
            "Current NAV Display": f"${latest_nav:.2f}" if latest_nav else "N/A",
            "NAV Premium": nav_premium_pct,
            "NAV Premium Display": f"{nav_premium_pct:+.2f}%" if nav_premium_pct is not None else "N/A",
            "Shares": shares,
            "Shares Display": f"{shares:,}",
            "Position Value": current_value,
            "Position Value Display": f"${current_value:,.2f}",
            "Total Return": dollar_change,
            "Total Return Display": f"${dollar_change:+,.2f}",
            "Total Return %": gain_pct,
            "Total Return % Display": f"{gain_pct:+.2f}%",
            "Initial Investment": initial_value,
            "Initial Investment Display": f"${initial_value:,.2f}",
            "Action": action_text
        })

    if position_data:
        position_df = pd.DataFrame(position_data)
        display_cols = [
            "Ticker",
            "Current Price Display",
            "Buy Price Display",
            "Current NAV Display",
            "NAV Premium Display",
            "Shares Display",
            "Position Value Display",
            "Total Return Display",
            "Total Return % Display",
            "Initial Investment Display",
            "Action"
        ]

        display_df = position_df[display_cols].copy()
        display_df.columns = [col.replace(" Display", "") for col in display_cols]

        # Style the DataFrame
        def style_negative_positive(v):
            if '-' in str(v):
                return 'color: red'
            return 'color: green'

        def style_nav_premium(v):
            try:
                if v != "N/A" and float(v.strip('%+')) > 5:
                    return 'color: red'
            except:
                pass
            return ''

        styled_df = display_df.style\
            .map(style_negative_positive, subset=['Total Return', 'Total Return %'])\
            .map(style_nav_premium, subset=['NAV Premium'])

        st.dataframe(
            styled_df,
            use_container_width=True,
            hide_index=True
        )

# --- Load All Data ---
nav_tracker = load_json("nav_tracker.json")
market_tracker = load_json("market_price_tracker.json")
aum_tracker = load_json("aum_tracker.json")
portfolio = load_json("portfolio.json")

# --- Header ---
st.title("🎯 YieldMax ETF Risk Dashboard")

# Convert current time to Pacific timezone
pacific_tz = pytz.timezone('America/Los_Angeles')
current_time = datetime.datetime.now(pacific_tz)
st.write(f"Last Updated: {current_time.strftime('%Y-%m-%d %I:%M:%S %p %Z')}")

# Refresh button
if st.button("🔄 Refresh Data"):
    st.rerun()

# --- Main Layout ---

# Calculate portfolio positions and metrics
total_current_value = 0
total_initial_value = 0
healthy_positions = []  # >15% gain
monitor_positions = []  # 5-15% gain
high_risk_positions = []  # <5% gain

for ticker, position in portfolio.items():
    if ticker in market_tracker and market_tracker[ticker]:
        current_price = market_tracker[ticker][-1]['price']
        shares = position['shares']
        buy_nav = position['buy_nav']
        
        current_value = current_price * shares
        initial_value = buy_nav * shares
        gain_pct = ((current_price - buy_nav) / buy_nav * 100)
        
        total_current_value += current_value
        total_initial_value += initial_value

        position_data = {
            'ticker': ticker,
            'gain_pct': gain_pct,
            'current_price': current_price,
            'buy_nav': buy_nav,
            'shares': shares,
            'current_value': current_value,
            'initial_value': initial_value
        }
        
        if gain_pct > 15:
            healthy_positions.append(position_data)
        elif gain_pct > 5:
            monitor_positions.append(position_data)
        else:
            high_risk_positions.append(position_data)

total_gain_pct = ((total_current_value - total_initial_value) / total_initial_value * 100)
total_positions = len([p for p in portfolio.items() if p[0] in market_tracker])

health_color = COLORS['healthy'] if total_gain_pct > 15 else COLORS['at_risk'] if total_gain_pct > 5 else COLORS['high_risk']

# First row: Portfolio Health, Dividend Income, Upcoming Payments
health_col, div_col, payments_col = st.columns([2, 1, 1])

with health_col:
    # Portfolio Health Overview
    st.markdown(f"""
    <div style='background-color: {health_color['bg']}; 
                padding: 20px; 
                border-radius: 10px; 
                margin-bottom: 20px;
                border: 1px solid {health_color.get("border", health_color["bg"])};'>
        <h2 style='color: {health_color['text']}; 
                   margin: 0; 
                   text-shadow: {health_color['shadow']};
                   font-weight: 600;'>Portfolio Health Overview</h2>
        <p style='color: {health_color['text']}; 
                  font-size: 18px; 
                  margin: 10px 0; 
                  text-shadow: {health_color['shadow']};
                  font-weight: 500;'>
            Total Portfolio Gain: {total_gain_pct:.2f}% (${(total_current_value - total_initial_value):+,.2f})
        </p>
        <p style='color: {health_color['text']}; 
                  font-size: 16px; 
                  margin: 5px 0; 
                  text-shadow: {health_color['shadow']};'>
            Current Value: ${total_current_value:,.2f} | Initial Value: ${total_initial_value:,.2f}
        </p>
        <p style='color: {health_color['text']}; 
                  font-size: 16px; 
                  margin: 5px 0; 
                  text-shadow: {health_color['shadow']};'>
            Position Distribution:
            🟢 Healthy ({len(healthy_positions)} positions) |
            🟡 Monitor ({len(monitor_positions)} positions) |
            🔴 High Risk ({len(high_risk_positions)} positions)
        </p>
    </div>
    """, unsafe_allow_html=True)

# Get dividend data
metrics, calendar_data = create_dividend_forecast_section(portfolio)

with div_col:
    create_dividend_income_widget(metrics)

with payments_col:
    create_upcoming_payments_widget(calendar_data)

# Second row: Positions and Market News
positions_col, news_col = st.columns([2, 1])

with positions_col:
    # Display positions by category
    if healthy_positions:
        create_position_table(
            healthy_positions,
            "🟢 Healthy Positions (>15% gain)",
            'healthy',
            "✅ MAINTAIN"
        )

    if monitor_positions:
        create_position_table(
            monitor_positions,
            "🟡 Monitor Positions (5-15% gain)",
            'at_risk',
            "👀 WATCH"
        )

    if high_risk_positions:
        create_position_table(
            high_risk_positions,
            "🔴 High Risk Positions (<5% gain)",
            'high_risk',
            "🚨 REVIEW"
        )

with news_col:
    # Market Sentiment & News section
    st.markdown("""
    <div style='background-color: #4C566A; padding: 20px; border-radius: 10px; margin-bottom: 20px;'>
        <h2 style='color: white; margin: 0;'>Market Sentiment & News</h2>
    </div>
    """, unsafe_allow_html=True)

    # Display news with sentiment analysis
    tickers_list = list(portfolio.keys())
    news_items = fetch_seeking_alpha_news(tickers_list)
    
    for item in news_items:
        sentiment_color = get_sentiment_color(item['sentiment'])
        sentiment_label = "Positive" if item['sentiment'] > 0.2 else "Negative" if item['sentiment'] < -0.2 else "Neutral"
        
        st.markdown(f"""
        <div style='border-left: 4px solid {sentiment_color}; 
                    padding: 10px; 
                    margin-bottom: 10px; 
                    background-color: rgba(76, 86, 106, 0.2); 
                    border-radius: 0 10px 10px 0;'>
            <div style='display: flex; justify-content: space-between; align-items: center;'>
                <span style='color: #88C0D0; font-size: 12px;'>{item['ticker']} | {sentiment_label}</span>
                <span style='color: #4C566A; font-size: 12px;'>{item['date']}</span>
            </div>
            <a href='{item['link']}' 
               target='_blank' 
               style='color: #D8DEE9; 
                      text-decoration: none; 
                      font-weight: 600; 
                      display: block; 
                      margin: 5px 0;'>{item['title']}</a>
        </div>
        """, unsafe_allow_html=True)

# Add explanatory notes at the bottom
st.markdown("""
**Position Categories:**
- 🟢 **Healthy**: Positions with >15% gain - Continue holding and monitoring
- 🟡 **Monitor**: Positions with 5-15% gain - Watch closely for changes in trend
- 🔴 **High Risk**: Positions with <5% gain - Consider rebalancing or exit strategy

**Notes:**
- NAV Premium shows how much the current price is above/below the NAV
- Total Return includes both price appreciation and any distributions
- All percentages are relative to original buy price
- Sentiment analysis is based on recent news articles and may not reflect long-term trends
""")

# --- Risk Analysis Heatmap ---
st.header("🔥 Risk Analysis Matrix")

# Create meaningful risk metrics for each position
risk_data = []
for ticker in TICKERS:
    if ticker in market_tracker and market_tracker[ticker]:
        current_price = market_tracker[ticker][-1]['price']
        nav_history = nav_tracker.get(ticker, [])
        latest_nav = nav_history[-1]['nav'] if nav_history else None
        
        if latest_nav:
            # Calculate risk metrics
            nav_premium = ((current_price - latest_nav) / latest_nav * 100)
            
            # NAV Stability (based on last 5 days of data)
            recent_navs = [entry['nav'] for entry in nav_history[-5:]]
            nav_volatility = np.std(recent_navs) / np.mean(recent_navs) * 100 if len(recent_navs) >= 5 else 0
            
            # Price Momentum (5-day trend)
            price_history = [entry['price'] for entry in market_tracker[ticker][-5:]]
            price_momentum = ((price_history[-1] - price_history[0]) / price_history[0] * 100) if len(price_history) >= 5 else 0
            
            # Portfolio Position
            in_portfolio = ticker in portfolio
            if in_portfolio:
                position = portfolio[ticker]
                gain_pct = ((current_price - position['buy_nav']) / position['buy_nav'] * 100)
                position_size = position['shares'] * current_price
            else:
                gain_pct = 0
                position_size = 0
            
            risk_data.append({
                'Ticker': ticker,
                'NAV Premium': nav_premium,
                'NAV Volatility': nav_volatility,
                'Price Momentum': price_momentum,
                'Position Size': position_size,
                'Gain/Loss': gain_pct if in_portfolio else None
            })

# Convert to DataFrame for heatmap
risk_df = pd.DataFrame(risk_data)
risk_df = risk_df.set_index('Ticker')

# Create heatmap
fig = go.Figure()

# Only include relevant metrics for the heatmap
heatmap_metrics = ['NAV Premium', 'NAV Volatility', 'Price Momentum', 'Gain/Loss']
z_data = risk_df[heatmap_metrics].values.T

fig.add_trace(go.Heatmap(
    z=z_data,
    x=risk_df.index,
    y=heatmap_metrics,
    colorscale='RdYlGn',  # Red for high risk, yellow for medium, green for low
    showscale=True
))

fig.update_layout(
    title="Position Risk Factors",
    height=400,
    margin=dict(t=30, b=0, l=0, r=0),
    yaxis_title="Risk Metrics",
    xaxis_title="Positions"
)

st.plotly_chart(fig, use_container_width=True)

st.markdown("""
**Risk Matrix Legend:**
- **NAV Premium**: Current premium/discount to NAV (red = high premium)
- **NAV Volatility**: 5-day NAV stability (red = high volatility)
- **Price Momentum**: 5-day price trend (green = positive momentum)
- **Gain/Loss**: Current position gain/loss (green = high gain)
""")

# --- Footer ---
st.markdown("---")
st.markdown("*Data refreshes hourly. All calculations based on latest available data.*") 