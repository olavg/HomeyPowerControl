import paho.mqtt.client as mqtt
import time
import subprocess
import requests
from entsoe import EntsoePandasClient
import pandas as pd
from datetime import datetime, timedelta
import os

# Configuration
BROKER = "192.168.86.54"
REBOOT_URL = "http://192.168.86.34/configuration"
API_KEY = os.getenv('ENTSOE_API_KEY')  # Ensure this environment variable is set
if not API_KEY:
    raise ValueError("No ENTSO-E API key found. Set the ENTSOE_API_KEY environment variable.")
BIDDING_ZONE = '10YNO-2--------U'  # Correct bidding zone code for NO_2
TIMEZONE = 'Europe/Oslo'  # Adjust if necessary

# Initialize variables
LAST_ACTIVITY_TIME = time.time()
prices = {}
last_consumption = None

def get_entsoe_prices(api_key, bidding_zone, start, end):
    """
    Fetch day-ahead electricity prices from ENTSO-E for a specific bidding zone.

    Parameters:
    - api_key (str): Your ENTSO-E API key.
    - bidding_zone (str): ENTSO-E bidding zone code.
    - start (pd.Timestamp): Start time with timezone.
    - end (pd.Timestamp): End time with timezone.

    Returns:
    - pd.Series: Series with timestamps as index and prices (EUR/kWh) as values.
    """
    client = EntsoePandasClient(api_key=api_key)
    try:
        prices = client.query_day_ahead_prices(bidding_zone, start=start, end=end)
        # Convert EUR/MWh to EUR/kWh
        prices_kwh = prices / 1000.0
        return prices_kwh
    except Exception as e:
        print(f"Error fetching prices: {e}")
        return pd.Series()

def update_prices():
    """
    Update the global prices dictionary with the latest day-ahead prices.
    """
    global prices
    client = EntsoePandasClient(api_key=API_KEY)
    now = datetime.now(tz=pd.Timestamp.now(tz=TIMEZONE).tz)
    # Fetch prices for today and the next day to ensure coverage
    start = pd.Timestamp(now.date(), tz=TIMEZONE)
    end = start + timedelta(days=2)
    price_series = get_entsoe_prices(API_KEY, BIDDING_ZONE, start=start, end=end)
    if not price_series.empty:
        # Convert the Pandas Series into a dictionary keyed by date and hour (e.g., "2024-12-16_14")
        for timestamp, price in price_series.iteritems():
            hour = timestamp.hour
            date = timestamp.date()
            prices_key = f"{date}_{hour}"
            prices[prices_key] = price
        print("Prices updated from ENTSO-E.")
    else:
        print("Failed to update prices from ENTSO-E.")

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to MQTT broker.")
        client.subscribe("ams/meter/import/active")
        # No longer subscribing to 'ams/price/#' since we're getting prices directly
    else:
        print(f"Connection failed with code {rc}")

def on_message(client, userdata, msg):
    global LAST_ACTIVITY_TIME, last_consumption, prices
    topic = msg.topic
    payload = msg.payload.decode("utf-8")

    # Update last activity time on every message
    LAST_ACTIVITY_TIME = time.time()

    if topic.startswith("ams/price/"):
        # If you still receive price data via MQTT, handle it here if needed
        pass
    elif topic == "ams/meter/import/active":
        try:
            consumption = float(payload)  # in Watts
            last_consumption = consumption
            print(f"Current power consumption: {consumption} Watts")
            
            # Perform calculations based on current time and price
            current_time = datetime.now(tz=pd.Timestamp.now(tz=TIMEZONE).tz)
            current_hour = current_time.hour
            current_date = current_time.date()
            prices_key = f"{current_date}_{current_hour}"
            current_price = prices.get(prices_key, None)
            if current_price is not None:
                consumption_kw = consumption / 1000.0
                cost_per_hour = consumption_kw * current_price
                print(f"At current consumption, cost per hour: {cost_per_hour:.4f} EUR")
                
                # Example decision-making based on thresholds
                if current_price > 0.50 and consumption_kw > 2.0:
                    print("High price and high consumption detected! Consider taking action.")
                    # Implement action here (e.g., reduce load, send notification, etc.)
            else:
                print("Price data not available for the current time.")
        except ValueError:
            print(f"Received non-numeric consumption value: {payload}")

def reboot_ams_reader():
    print("No activity for 5 minutes. Rebooting AMS reader now...")
    try:
        response = requests.get(REBOOT_URL, timeout=5)
        if response.status_code == 200:
            print("Reboot request sent successfully.")
        else:
            print(f"Reboot request failed with status code: {response.status_code}")
    except requests.RequestException as e:
        print(f"Reboot request encountered an error: {e}")

def main():
    global LAST_ACTIVITY_TIME
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    # If MQTT broker requires authentication:
    # client.username_pw_set("username", "password")

    client.connect(BROKER, 1883, 60)
    client.loop_start()

    # Initial price update
    update_prices()

    try:
        while True:
            current_time = time.time()
            elapsed = current_time - LAST_ACTIVITY_TIME
            if elapsed > 300:  # 5 minutes
                reboot_ams_reader()
                # After reboot, reset the timestamp to avoid repeated reboots
                LAST_ACTIVITY_TIME = time.time()
            
            # Schedule price updates at a specific time, e.g., midnight local time
            now = datetime.now(tz=pd.Timestamp.now(tz=TIMEZONE).tz)
            if now.hour == 0 and now.minute == 0 and now.second < 10:
                update_prices()
            
            time.sleep(10)  # Check every 10 seconds
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()

if __name__ == "__main__":
    main()
