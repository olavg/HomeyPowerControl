import os
import requests
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
import threading
import logging
import json
import pytz

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("ams_data.log"),
        logging.StreamHandler()
    ]
)

# Load environment variables
load_dotenv()

AMS_READER_URL = "http://192.168.86.34/data.json"
POLL_INTERVAL = 10  # Polling interval in seconds
local_timezone = pytz.timezone('Europe/Oslo')

# Globals
prices = {}
last_consumption = None

# Fetch data from AMS reader
def fetch_ams_data():
    try:
        response = requests.get(AMS_READER_URL, timeout=5)
        response.raise_for_status()
        data = response.json()

        # Extract relevant fields
        power = data.get("power", 0)  # Power consumption in watts
        timestamp = datetime.now(local_timezone).strftime("%Y-%m-%d %H:%M:%S")

        logging.info(f"Power Consumption: {power} W at {timestamp}")
        return power
    except (requests.RequestException, json.JSONDecodeError) as e:
        logging.error(f"Failed to fetch data from AMS reader: {e}")
        return None

# Periodically poll the AMS reader
def poll_ams_reader():
    global last_consumption
    while True:
        power = fetch_ams_data()
        if power is not None:
            last_consumption = power
            calculate_cost(power)
        time.sleep(POLL_INTERVAL)

# Calculate cost based on prices and consumption
def calculate_cost(consumption):
    current_time_local = datetime.now(local_timezone)
    hour_str = str(current_time_local.hour)
    if hour_str in prices:
        current_price = prices[hour_str]
        cost_per_hour = (consumption / 1000.0) * current_price
        logging.info(f"At current consumption, cost per hour: {cost_per_hour:.2f} currency units.")
    else:
        logging.warning("No price data available for the current hour.")

# Fetch ENTSO-E day-ahead prices
def collect_entsoe_prices():
    from entsoe import EntsoePandasClient
    import pandas as pd

    ENTSOE_API_KEY = os.getenv('ENTSOE_API_KEY')
    if not ENTSOE_API_KEY:
        logging.error("ENTSOE_API_KEY not found in environment variables.")
        return

    client_entsoe = EntsoePandasClient(api_key=ENTSOE_API_KEY)
    bidding_zone = '10YNO-2--------T'
    global prices
    try:
        start = pd.Timestamp(datetime.now(pytz.utc).replace(hour=0, minute=0, second=0), tz="UTC")
        end = start + timedelta(days=1)
        prices_series = client_entsoe.query_day_ahead_prices(bidding_zone, start=start, end=end)

        prices.clear()
        for ts, price in prices_series.items():
            hour = ts.tz_convert(local_timezone).hour
            prices[str(hour)] = float(price)
        logging.info("Fetched ENTSO-E day-ahead prices successfully.")
    except Exception as e:
        logging.error(f"Error fetching ENTSO-E prices: {e}")

# Schedule daily price updates
def schedule_price_updates():
    while True:
        current_time = datetime.now(local_timezone)
        next_update = (current_time + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait_seconds = (next_update - current_time).total_seconds()
        logging.info(f"Next price update in {wait_seconds / 3600:.2f} hours.")
        time.sleep(wait_seconds)
        collect_entsoe_prices()

# Main function
def main():
    # Fetch initial prices
    collect_entsoe_prices()

    # Start price update scheduler
    threading.Thread(target=schedule_price_updates, daemon=True).start()

    # Start AMS reader polling
    threading.Thread(target=poll_ams_reader, daemon=True).start()

    try:
        while True:
            time.sleep(10)  # Keep the main thread alive
    except KeyboardInterrupt:
        logging.info("Shutting down gracefully...")

if __name__ == "__main__":
    main()
