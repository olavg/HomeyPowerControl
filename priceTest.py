import os
import paho.mqtt.client as mqtt
import time
import subprocess
import requests
from datetime import datetime, timedelta
from entsoe import EntsoePandasClient
from dotenv import load_dotenv
import threading
import pytz
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("priceTest.log"),
        logging.StreamHandler()
    ]
)

# Load environment variables from .env file
load_dotenv()

# Retrieve the API key
ENTSOE_API_KEY = os.getenv('ENTSOE_API_KEY')

if not ENTSOE_API_KEY:
    logging.error("No ENTSOE_API_KEY found in environment variables.")
    raise ValueError("No ENTSOE_API_KEY found in environment variables.")

# Define your MQTT broker address
BROKER = "192.168.86.54"
REBOOT_URL = "http://192.168.86.34/configuration"
LAST_ACTIVITY_TIME = time.time()

# Store prices by hour index (0-23)
prices = {}
last_consumption = None  # store the most recent consumption in Watts

# Define your local timezone
local_timezone = pytz.timezone('Europe/Oslo')  # Adjust if different

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logging.info("Connected to MQTT broker.")
        # Subscribe to the consumption topic
        client.subscribe("ams/meter/import/active")
        # If you still want to subscribe to price topics via MQTT, uncomment below
        # client.subscribe("ams/price/#")
    else:
        logging.error(f"Connection failed with code {rc}")

def on_message(client, userdata, msg):
    global LAST_ACTIVITY_TIME, last_consumption, prices
    topic = msg.topic
    payload = msg.payload.decode("utf-8")

    # Update last activity time on every message
    LAST_ACTIVITY_TIME = time.time()

    if topic.startswith("ams/price/"):
        hour = topic.split("/")[-1]
        # Convert payload to float if it’s numeric
        try:
            price = float(payload)
            prices[hour] = price
            logging.info(f"Price for hour {hour}: {price} (currency per kWh)")
        except ValueError:
            logging.warning(f"Received non-numeric price value for hour {hour}: {payload}")

    elif topic == "ams/meter/import/active":
        try:
            consumption = float(payload)  # in Watts
            last_consumption = consumption
            logging.info(f"Current power consumption: {consumption} Watts")

            # Get current time in local timezone
            current_time_local = datetime.now(local_timezone)
            current_hour = current_time_local.hour
            hour_str = str(current_hour)
            if hour_str in prices:
                current_price = prices[hour_str]
                consumption_kw = consumption / 1000.0
                cost_per_hour = consumption_kw * current_price
                logging.info(f"At current consumption, cost per hour: {cost_per_hour:.2f} currency units.")

                # Example threshold action
                if current_price > 0.50 and consumption_kw > 2.0:
                    logging.warning("High price and high consumption detected! Consider taking action now.")
                    # Add your logic here (e.g., send a notification, reduce load, etc.)
            else:
                logging.warning("No price data available for the current hour yet.")

        except ValueError:
            logging.warning(f"Received non-numeric consumption value: {payload}")

def reboot_ams_reader():
    logging.info("No activity for 5 minutes. Rebooting AMS reader now...")
    try:
        response = requests.get(REBOOT_URL, timeout=5)
        if response.status_code == 200:
            logging.info("Reboot request sent successfully.")
        else:
            logging.error(f"Reboot request failed with status code: {response.status_code}")
    except requests.RequestException as e:
        logging.error(f"Reboot request encountered an error: {e}")

def collect_entsoe_prices():
    """
    Collect day-ahead prices from ENTSO-E for NO_2 bidding zone.
    """
    client_entsoe = EntsoePandasClient(api_key=ENTSOE_API_KEY)

    # Define the time range for which to collect prices
    # ENTSO-E day-ahead prices are typically published daily
    end_time = datetime.now(pytz.utc)
    start_time = end_time - timedelta(days=1)

    # Replace with your actual bidding zone code for NO_2
    bidding_zone = '10YNO-2--------T'  # Verify this code from ENTSO-E documentation
    global prices
    try:
        # Query day-ahead prices
        # Set start and end times with UTC timezone
        utc = pytz.utc
        end = datetime.now(utc)  # End time is now in UTC
        start = end - timedelta(days=1)  # Start is 24 hours before
        prices = client_entsoe.query_day_ahead_prices(bidding_zone, start=start, end=end)
        print(prices)

        prices_series = client_entsoe.query_day_ahead_prices(bidding_zone, start=start_time, end=end_time)
        # Convert to dictionary with hour as key (0-23) in local time

        prices = {}
        for ts, price in prices_series.iteritems():
            # Convert UTC timestamp to local timezone
            ts_local = ts.tz_convert(local_timezone)
            hour = ts_local.hour
            prices[str(hour)] = float(price)
        
        logging.info("Successfully fetched ENTSO-E day-ahead prices:")
        for hour in sorted(prices.keys(), key=lambda x: int(x)):
            logging.info(f"Hour {hour}: {prices[hour]} currency units per kWh")
    except Exception as e:
        logging.error(f"Error fetching ENTSO-E prices: {e}")

def schedule_price_updates():
    """
    Schedule price updates to run daily at a specified time (e.g., midnight).
    """
    while True:
        current_time = datetime.now(local_timezone)
        # Define the next update time (e.g., next midnight)
        next_update = (current_time + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait_seconds = (next_update - current_time).total_seconds()
        logging.info(f"Scheduled next price update in {wait_seconds / 3600:.2f} hours.")
        time.sleep(wait_seconds)
        collect_entsoe_prices()

def main():
    global LAST_ACTIVITY_TIME
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    # If your MQTT broker requires authentication:
    # client.username_pw_set("username", "password")

    client.connect(BROKER, 1883, 60)
    client.loop_start()

    # Collect ENTSO-E prices initially
    collect_entsoe_prices()

    # Start a separate thread to schedule daily price updates
    price_thread = threading.Thread(target=schedule_price_updates, daemon=True)
    price_thread.start()

    try:
        while True:
            current_time_epoch = time.time()
            elapsed = current_time_epoch - LAST_ACTIVITY_TIME
            if elapsed > 300:  # 5 minutes
                reboot_ams_reader()
                LAST_ACTIVITY_TIME = time.time()
            time.sleep(10)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
    finally:
        client.loop_stop()

if __name__ == "__main__":
    main()
