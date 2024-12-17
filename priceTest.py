import os
import paho.mqtt.client as mqtt
import time
import requests
from datetime import datetime, timedelta
from entsoe import EntsoePandasClient
from dotenv import load_dotenv
import threading
import pytz
import logging
import pandas as pd
import random

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("priceTest.log"),
        logging.StreamHandler()
    ]
)

# Load environment variables
load_dotenv()

# Retrieve API key and MQTT configuration
ENTSOE_API_KEY = os.getenv('ENTSOE_API_KEY')
BROKER = os.getenv('MQTT_BROKER', '192.168.86.54')
REBOOT_URL = os.getenv('REBOOT_URL', 'http://192.168.86.34/configuration')

if not ENTSOE_API_KEY:
    logging.error("No ENTSOE_API_KEY found in environment variables.")
    raise ValueError("No ENTSOE_API_KEY found in environment variables.")

# Globals
LAST_ACTIVITY_TIME = time.time()
prices = {}
last_consumption = None
local_timezone = pytz.timezone('Europe/Oslo')

# MQTT Handlers
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        logging.info("Connected to MQTT broker.")
        client.subscribe("ams/meter/import/active")
    else:
        logging.error(f"Connection failed with code {rc}")

def on_disconnect(client, userdata, rc):
    logging.warning(f"Disconnected from MQTT broker with code {rc}. Reconnecting...")
    client.reconnect_delay_set(min_delay=5, max_delay=60)

def on_message(client, userdata, msg, properties=None):
    global LAST_ACTIVITY_TIME, last_consumption, prices
    LAST_ACTIVITY_TIME = time.time()
    try:
        topic = msg.topic
        payload = float(msg.payload.decode("utf-8"))
        
        if topic.startswith("ams/price/"):
            hour = topic.split("/")[-1]
            prices[hour] = payload
            logging.info(f"Price for hour {hour}: {payload:.2f} currency per kWh")
        elif topic == "ams/meter/import/active":
            last_consumption = payload
            logging.info(f"Current power consumption: {payload:.2f} Watts")
            calculate_cost(payload)
    except ValueError as e:
        logging.warning(f"Error processing message: {e}")

# Cost Calculation
def calculate_cost(consumption):
    current_time_local = datetime.now(local_timezone)
    hour_str = str(current_time_local.hour)
    if hour_str in prices:
        current_price = prices[hour_str]
        cost_per_hour = (consumption / 1000.0) * current_price
        logging.info(f"Cost per hour at {current_price:.2f} currency/kWh: {cost_per_hour:.2f}")
    else:
        logging.warning("No price data available for the current hour.")

# AMS Reader Reboot
def reboot_ams_reader():
    logging.info("No activity for 5 minutes. Attempting to reboot AMS reader...")
    retries = 0
    while retries < 3:
        try:
            response = requests.get(REBOOT_URL, timeout=5)
            response.raise_for_status()
            logging.info("Reboot successful.")
            return
        except requests.RequestException as e:
            retries += 1
            logging.error(f"Reboot attempt {retries} failed: {e}")
            time.sleep(5)
    logging.error("All reboot attempts failed.")

def collect_entsoe_prices(max_retries=5):
    """
    Fetches ENTSO-E day-ahead prices with exponential backoff on failure.

    Args:
        max_retries (int): Maximum number of retry attempts.

    Global:
        Updates the `prices` dictionary with fetched price data.
    """
    client_entsoe = EntsoePandasClient(api_key=ENTSOE_API_KEY)
    bidding_zone = '10YNO-2--------T'
    global prices

    retries = 0
    backoff = 2  # Initial backoff time in seconds

    while retries < max_retries:
        try:
            start = pd.Timestamp(datetime.now(pytz.utc).replace(hour=0, minute=0, second=0))
            end = start + timedelta(days=1)
            prices_series = client_entsoe.query_day_ahead_prices(bidding_zone, start=start, end=end)

            # Clear and populate the prices dictionary
            prices.clear()
            for ts, price in prices_series.items():
                try:
                    hour = ts.tz_convert(local_timezone).hour
                    prices[str(hour)] = float(price)
                except Exception as e:
                    logging.error(f"Invalid price data: {e}")

            logging.info("Fetched ENTSO-E day-ahead prices successfully.")
            return  # Exit the function on success

        except requests.RequestException as e:
            logging.error(f"Connection error: {e}. Retrying in {backoff} seconds...")
        except Exception as e:
            logging.error(f"Error fetching ENTSO-E prices: {e}. Retrying in {backoff} seconds...")

        retries += 1
        time.sleep(backoff + random.uniform(0, 1))  # Add jitter to prevent synchronized retries
        backoff *= 2  # Exponential backoff

    logging.error("All attempts to fetch ENTSO-E prices have failed.")

# Fetch Prices from ENTSO-E
def collect_entsoe_prices_old():
    client_entsoe = EntsoePandasClient(api_key=ENTSOE_API_KEY)
    bidding_zone = '10YNO-2--------T'
    global prices
    try:
#        start = pd.Timestamp(datetime.now(pytz.utc).replace(hour=0, minute=0, second=0), tz="UTC")
        start = pd.Timestamp(datetime.now(pytz.utc).replace(hour=0, minute=0, second=0))
        end = start + timedelta(days=1)
        prices_series = client_entsoe.query_day_ahead_prices(bidding_zone, start=start, end=end)
        prices.clear()
        for ts, price in prices_series.items():
            try:
                hour = ts.tz_convert(local_timezone).hour
                prices[str(hour)] = float(price)
            except Exception as e:
                logging.error(f"Invalid price data: {e}")
        logging.info("Fetched ENTSO-E day-ahead prices successfully.")
    except Exception as e:
        logging.error(f"Error fetching ENTSO-E prices: {e}")

# Schedule Daily Price Updates
def schedule_price_updates():
    while True:
        try:
            current_time = datetime.now(local_timezone)
            next_update = (current_time + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            wait_seconds = (next_update - current_time).total_seconds()
            logging.info(f"Next price update in {wait_seconds / 3600:.2f} hours.")
            time.sleep(wait_seconds)
            collect_entsoe_prices()
        except Exception as e:
            logging.error(f"Error in price update thread: {e}")

def get_current_power_usage(api_base_url="http://192.168.86.34", timeout=5):
    """
    Fetch the current power usage from the AMS Leser HTTP API.

    Args:
        api_base_url (str): The base URL of the AMS Leser API.
        timeout (int): Request timeout in seconds.

    Returns:
        float: Current power usage in Watts.

    Raises:
        Exception: If the API call fails or returns invalid data.
    """
    endpoint = f"{api_base_url}/data.json"
    try:
        response = requests.get(endpoint, timeout=timeout)
        response.raise_for_status()
        data = response.json()

        # Extracting the "power" value (ensure the API structure matches the docs)
        current_power = float(data.get("w", 0.0))
        logging.info(f"Current power usage fetched: {current_power} Watts")
        return current_power
    except requests.RequestException as e:
        logging.error(f"Failed to fetch power usage: {e}")
        raise
    except (ValueError, KeyError) as e:
        logging.error(f"Invalid data received from AMS Leser API: {e}")
        raise

# Main Function
def main():
    global LAST_ACTIVITY_TIME
    collect_entsoe_prices()
    threading.Thread(target=schedule_price_updates, daemon=True).start()

    try:
        while True:
            # Fetch current power usage directly from the AMS Leser API
            try:
                current_power = get_current_power_usage()
                calculate_cost(current_power)
                LAST_ACTIVITY_TIME = time.time()
            except Exception as e:
                logging.warning(f"Failed to retrieve power usage: {e}")

            # Check for inactivity and attempt reboot if necessary
            if time.time() - LAST_ACTIVITY_TIME > 300:
                reboot_ams_reader()
                LAST_ACTIVITY_TIME = time.time()

            time.sleep(10)
    except KeyboardInterrupt:
        logging.info("Shutting down...")

def main_old():
    global LAST_ACTIVITY_TIME
    #client = mqtt.Client()
    # Specify protocol version to enforce updated API
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.connect(BROKER, 1883, 60)
    client.loop_start()
    collect_entsoe_prices()
    threading.Thread(target=schedule_price_updates, daemon=True).start()
    try:
        while True:
            if time.time() - LAST_ACTIVITY_TIME > 300:
                reboot_ams_reader()
                LAST_ACTIVITY_TIME = time.time()
            time.sleep(10)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
    finally:
        client.loop_stop()

if __name__ == "__main__":
    main()
