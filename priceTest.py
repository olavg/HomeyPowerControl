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

# Retrieve API key and MQTT configuration
ENTSOE_API_KEY = os.getenv('ENTSOE_API_KEY')
BROKER = os.getenv('MQTT_BROKER', '192.168.86.54')  # Default value
REBOOT_URL = os.getenv('REBOOT_URL', 'http://192.168.86.34/configuration')

if not ENTSOE_API_KEY:
    logging.error("No ENTSOE_API_KEY found in environment variables.")
    raise ValueError("No ENTSOE_API_KEY found in environment variables.")

# Global variables
LAST_ACTIVITY_TIME = time.time()
prices = {}  # Store prices by hour index (0-23)
last_consumption = None
local_timezone = pytz.timezone('Europe/Oslo')

# MQTT Event Handlers
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logging.info("Connected to MQTT broker.")
        client.subscribe("ams/meter/import/active")
    else:
        logging.error(f"Connection failed with code {rc}")

def on_disconnect(client, userdata, rc):
    logging.warning(f"Disconnected from MQTT broker with code {rc}. Reconnecting...")
    while rc != 0:
        try:
            rc = client.reconnect()
            logging.info("Reconnected successfully.")
        except Exception as e:
            logging.error(f"Reconnect failed: {e}. Retrying...")
            time.sleep(5)

def on_message(client, userdata, msg):
    global LAST_ACTIVITY_TIME, last_consumption, prices
    LAST_ACTIVITY_TIME = time.time()
    try:
        topic = msg.topic
        payload = float(msg.payload.decode("utf-8"))
        
        if topic.startswith("ams/price/"):
            hour = topic.split("/")[-1]
            prices[hour] = payload
            logging.info(f"Price for hour {hour}: {payload} (currency per kWh)")
        elif topic == "ams/meter/import/active":
            last_consumption = payload
            logging.info(f"Current power consumption: {payload} Watts")
            calculate_cost(payload)
    except ValueError as e:
        logging.warning(f"Error processing message: {e}")

# Utility Functions
def calculate_cost(consumption):
    current_time_local = datetime.now(local_timezone)
    hour_str = str(current_time_local.hour)
    if hour_str in prices:
        current_price = prices[hour_str]
        cost_per_hour = (consumption / 1000.0) * current_price
        logging.info(f"At current consumption, cost per hour: {cost_per_hour:.2f} currency units.")

        if current_price > 0.50 and (consumption / 1000.0) > 2.0:
            logging.warning("High price and consumption detected. Consider reducing load.")
    else:
        logging.warning("No price data available for the current hour.")

def reboot_ams_reader():
    logging.info("No activity for 5 minutes. Rebooting AMS reader...")
    try:
        response = requests.get(REBOOT_URL, timeout=5)
        response.raise_for_status()
        logging.info("Reboot request successful.")
    except requests.RequestException as e:
        logging.error(f"Reboot request failed: {e}")

def collect_entsoe_prices():
    client_entsoe = EntsoePandasClient(api_key=ENTSOE_API_KEY)
    bidding_zone = '10YNO-2--------T'
    global prices
    try:
        start = pd.Timestamp(datetime.now(pytz.utc).replace(hour=0, minute=0, second=0))
        end = start + timedelta(days=1)
        prices_series = client_entsoe.query_day_ahead_prices(bidding_zone, start=start, end=end)

        prices.clear()
        for ts, price in prices_series.items():
            hour = ts.tz_convert(local_timezone).hour
            prices[str(hour)] = float(price)
        logging.info("Fetched ENTSO-E day-ahead prices successfully.")
    except Exception as e:
        logging.error(f"Error fetching ENTSO-E prices: {e}")

def schedule_price_updates():
    while True:
        current_time = datetime.now(local_timezone)
        next_update = (current_time + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait_seconds = (next_update - current_time).total_seconds()
        logging.info(f"Next price update in {wait_seconds / 3600:.2f} hours.")
        time.sleep(wait_seconds)
        collect_entsoe_prices()

def main():
    global LAST_ACTIVITY_TIME
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
            if time.time() - LAST_ACTIVITY_TIME > 300:  # 5 minutes
                reboot_ams_reader()
                LAST_ACTIVITY_TIME = time.time()
            time.sleep(10)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
    finally:
        client.loop_stop()

if __name__ == "__main__":
    main()
