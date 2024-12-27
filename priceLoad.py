import os
import requests
import time
import logging
import threading
import paho.mqtt.client as mqtt
from datetime import datetime, timedelta
from dotenv import load_dotenv
import pytz
import pandas as pd
from entsoe import EntsoePandasClient
import json
import logging

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# Load environment variables
load_dotenv()

# Configuration
username = os.getenv('ZAPTEC_USER')
password = os.getenv('ZAPTEC_PASSWORD')
ZAPTEC_AUTH_URL = "https://api.zaptec.com/oauth/token"
ZAPTEC_API_URL = "https://api.zaptec.com/api/installation/{installation_id}/update"
ZAPTEC_API_KEY = os.getenv("ZAPTEC_API_KEY")
CHARGER_ID = os.getenv("ZAPTEC_CHARGER_ID")
ENTSOE_API_KEY = os.getenv("ENTSOE_API_KEY")
MQTT_BROKER = os.getenv("MQTT_BROKER", "192.168.86.54")
MQTT_PORT = "1883"
WATER_HEATER_TOPIC = "control/water_heater"
AMS_METER_API_BASE_URL = "http://192.168.86.34"
MAX_TOTAL_LOAD = 10000  # Maximum household load in watts
NOMINAL_VOLTAGE = 230  # Voltage in volts
MIN_AMPERAGE = 6  # Minimum charging current in amperes
MAX_AMPERAGE = 32  # Maximum charging current in amperes
BATTERY_TARGET_KWH = 29  # 50% of a 58 kWh battery
CAR_CHARGER_POWER = 3680  # 16A at 230V ~= 3.7 kW
LOCAL_TZ = pytz.timezone("Europe/Oslo")
high_price_threshold = 100

# Globals
prices = {}
cheapest_schedule = []
last_zaptec_update = None
water_heater_power = 0.0  # Initialize water heater power consumption
last_consumption = 0.0  # Initialize last consumption
LAST_ACTIVITY_TIME = time.time()

def make_api_request(
    url,
    method="GET",
    headers=None,
    payload=None,
    params=None,
    max_retries=3,
    initial_delay=5,
    timeout=10,
    use_json=True
):
    """
    Make an API request with retry logic.

    Args:
        url (str): The API endpoint URL.
        method (str): HTTP method ("GET", "POST", "PUT", "DELETE"). Default is "GET".
        headers (dict): Request headers. Default is None.
        payload (dict): Request payload for "POST" or "PUT". Default is None.
        params (dict): Query parameters for "GET" requests. Default is None.
        max_retries (int): Maximum number of retries for the request. Default is 3.
        initial_delay (int): Initial delay between retries in seconds. Default is 5.
        timeout (int): Request timeout in seconds. Default is 10.
        use_json (bool): Whether to send the payload as JSON or form-encoded. Default is True.

    Returns:
        dict: Parsed JSON response from the API.

    Raises:
        Exception: If all retries fail or the response status is not successful.
    """
    delay = initial_delay

    for attempt in range(max_retries):
        try:
            if method.upper() == "GET":
                response = requests.get(url, headers=headers, params=params, timeout=timeout)
            elif method.upper() in ["POST", "PUT"]:
                # Choose between JSON and form-encoded payload
                request_args = {"headers": headers, "timeout": timeout}
                if use_json:
                    request_args["json"] = payload
                else:
                    request_args["data"] = payload
                if method.upper() == "POST":
                    response = requests.post(url, **request_args)
                else:
                    response = requests.put(url, **request_args)
            elif method.upper() == "DELETE":
                response = requests.delete(url, headers=headers, timeout=timeout)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()  # Raise an error for bad HTTP status codes
            return response.json()  # Return the parsed JSON response

        except requests.exceptions.HTTPError as http_err:
            logging.error(f"HTTP error occurred: {http_err} (Attempt {attempt + 1})")
        except requests.exceptions.RequestException as req_err:
            logging.error(f"Request error occurred: {req_err} (Attempt {attempt + 1})")
            logging.error(f"Request params: {params}, payload: {payload} ")
  
        # Wait before retrying
        time.sleep(delay)
        delay *= 2  # Exponential backoff

    logging.error(f"All retries failed for API URL: {url}")
    raise Exception(f"Failed to complete {method} request to {url} after {max_retries} attempts.")
###ZAPTEC
def get_access_token():
    username = os.getenv("ZAPTEC_USER")
    password = os.getenv("ZAPTEC_PASSWORD")

    if not username or not password:
        raise ValueError("Environment variables ZAPTEC_USER and ZAPTEC_PASSWORD must be set")

    payload = {
        "grant_type": "password",
        "username": username,
        "password": password,
        "scope": "offline_access"
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }

    return make_api_request(ZAPTEC_AUTH_URL, method="POST", headers=headers, payload=payload, use_json=False)

def refresh_access_token(refresh_token):
    """
    Refresh the access token using the refresh token.

    Args:
        refresh_token (str): The refresh token to use for getting a new access token.

    Returns:
        dict: The new access and refresh tokens.

    Raises:
        Exception: If the API call fails after retries.
    """
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }

    return make_api_request(ZAPTEC_AUTH_URL, method="POST", headers=headers, payload=payload, use_json=False)
def get_installations(access_token):
    """
    Fetch the list of installations associated with the given access token.

    Args:
        access_token (str): The access token for API authentication.

    Returns:
        dict: The JSON response containing the list of installations.

    Raises:
        Exception: If the API call fails after retries.
    """
    url = "https://api.zaptec.com/api/installation"
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    return make_api_request(url, method="GET", headers=headers)

def set_charging_amperage(amperage):
    """
    Set the available charging current for the entire installation.

    Args:
        amperage (int): Desired charging current in amperes.

    Raises:
        Exception: If the API call fails after retries.
    """
    global last_zaptec_update
    now = datetime.now()

    # Check rate limiting: ensure at least 15 minutes between updates
    if last_zaptec_update and (now - last_zaptec_update) < timedelta(minutes=15):
        logging.info("Skipping Zaptec update to comply with rate limiting.")
        return

    # Fetch access token
    tokens = get_access_token()
    access_token = tokens["access_token"]

    # Retrieve installation details
    installations_response = get_installations(access_token)

    # Ensure installations are available
    if 'Data' in installations_response and installations_response['Data']:
        first_installation = installations_response['Data'][0]
        installation_id = first_installation.get('Id')
        installation_name = first_installation.get('Name')
        logging.info(f"First Installation ID: {installation_id}, Name: {installation_name}")
    else:
        raise Exception("No installations found or unexpected response format.")

    # API endpoint for updating available current
    url = ZAPTEC_API_URL.format(installation_id=installation_id)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "maxChargeCurrent": amperage,
        "minChargeCurrent": amperage  # Setting min and max to the same for consistent control
    }
    # Send the request using the generic function
    logging.info(f"Attempting to set installation available current to {amperage}A.")
    try:
        response = make_api_request(url, method="POST", headers=headers, payload=payload, use_json=True)
        logging.info(f"Installation available current set to {amperage}A successfully.")
    except Exception as e:
        logging.error(f"Failed to set charging amperage: {e}")
        raise

    # Update the timestamp of the last successful update
    last_zaptec_update = now

# MQTT Handlers
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logging.info("Connected to MQTT broker.")
        client.subscribe("ams/meter/import/active")
        client.subscribe("home/water_heater/power")  # Subscribe to water heater power topic
    else:
        logging.error(f"Connection failed with code {rc}")

def on_message(client, userdata, msg):
    global LAST_ACTIVITY_TIME, last_consumption, prices, water_heater_power
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
        elif topic == "home/water_heater/power":
            water_heater_power = payload
            logging.info(f"Water heater power consumption: {payload:.2f} Watts")
    except ValueError as e:
        logging.warning(f"Error processing message: {e}")

# Exponential Backoff Retry
def exponential_backoff_retry(func, max_retries=3, initial_delay=5):
    delay = initial_delay
    for attempt in range(max_retries):
        try:
            return func()
        except requests.RequestException as e:
            logging.error(f"Attempt {attempt + 1} failed: {e}. Retrying in {delay} seconds.")
            time.sleep(delay)
            delay *= 2  # Exponential backoff
    raise Exception("All retries failed.")

# Fetch ENTSO-E Day-Ahead Prices
def fetch_entsoe_prices():
    global prices
    client = EntsoePandasClient(api_key=ENTSOE_API_KEY)
    bidding_zone = '10YNO-2--------T'
    try:
        now = datetime.now(pytz.utc)
        start = pd.Timestamp(now.replace(hour=0, minute=0, second=0))
        end = start + timedelta(days=2)  # Fetch for 36 hours
        prices_series = client.query_day_ahead_prices(bidding_zone, start=start, end=end)
        prices.clear()
        for ts, price in prices_series.items():
            hour = ts.tz_convert(LOCAL_TZ).hour
            day = ts.tz_convert(LOCAL_TZ).day
            prices[f"{day}-{hour}"] = float(price)
        logging.info("Fetched ENTSO-E day-ahead prices successfully.")
    except Exception as e:
        logging.error(f"Error fetching ENTSO-E prices: {e}")

# Plan Cheapest Charging Schedule
def plan_charging_schedule():
    global cheapest_schedule
    kwh_required = BATTERY_TARGET_KWH
    total_hours = int(kwh_required / (CAR_CHARGER_POWER / 1000))  # Estimate required hours

    # Sort prices
    sorted_prices = sorted(prices.items(), key=lambda x: x[1])
    cheapest_schedule = sorted_prices[:total_hours]
    logging.info(f"Planned charging schedule: {cheapest_schedule}")

# Fetch Current Power Usage
def get_current_power_usage(api_base_url=AMS_METER_API_BASE_URL, timeout=5):
    endpoint = f"{api_base_url}/data.json"
    try:
        response = requests.get(endpoint, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        current_power = float(data.get("w", 0.0))
        logging.info(f"Current power usage: {current_power} Watts")
        return current_power
    except Exception as e:
        logging.error(f"Error fetching power usage: {e}")
        return None

# Set Zaptec Charging Amperage
def set_charging_amperage_old(amperage):
    """
    Set the available charging current for the entire installation.
    """
    global last_zaptec_update
    now = datetime.now()

    # Check rate limiting: ensure at least 15 minutes between updates
    if last_zaptec_update and (now - last_zaptec_update) < timedelta(minutes=15):
        logging.info("Skipping Zaptec update to comply with rate limiting.")
        return

    def api_call():
        tokens = get_access_token()
        access_token = tokens["access_token"]
        print(access_token)
        installations_response = get_installations(access_token)
        print(installations_response)
        # Check if 'Data' key exists and contains installations
        if 'Data' in installations_response and installations_response['Data']:
            first_installation = installations_response['Data'][0]
            installation_id = first_installation.get('Id')
            installation_name = first_installation.get('Name')
            print(f"First Installation ID: {installation_id}, Name: {installation_name}")
        else:
            print("No installations found or unexpected response format.")
        url = ZAPTEC_API_URL.format(installation_id=installation_id)

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        payload = {
                "AvailableCurrent": amperage
            }
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response

    # Attempt the API call with exponential backoff
    exponential_backoff_retry(api_call)

    # Update the timestamp of the last successful update
    last_zaptec_update = now
    logging.info(f"Installation available current set to {amperage}A successfully.")

def set_charging_amperage_old(amperage):
    global last_zaptec_update
    now = datetime.now()

    # Check rate limiting: ensure at least 15 minutes between updates
    if last_zaptec_update and (now - last_zaptec_update) < timedelta(minutes=15):
        logging.info("Skipping Zaptec update to comply with rate limiting.")
        return

    def api_call():
        try:            # Fetch the initial access token
            tokens = get_access_token()
            access_token = tokens["access_token"]
            refresh_token = tokens["refresh_token"]

            print("Access Token:", access_token)
            print("Refresh Token:", refresh_token)

            # Example: Refresh the access token
            new_tokens = refresh_access_token(refresh_token)
        except:
            pass
        url = ZAPTEC_API_URL.format(charger_id=CHARGER_ID)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        payload = {
            "maxChargeCurrent": amperage,
            "minChargeCurrent": amperage  # Assuming min and max are set the same for control
        }
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response

    # Attempt the API call with exponential backoff
    exponential_backoff_retry(api_call)

    # Update the timestamp of the last successful update
    last_zaptec_update = now
    logging.info(f"Charging amperage set to {amperage}A successfully.")

# Calculate Desired Amperage
def calculate_desired_amperage(current_power_usage, water_heater_power, max_total_load=MAX_TOTAL_LOAD, nominal_voltage=NOMINAL_VOLTAGE, min_amperage=MIN_AMPERAGE, max_amperage=MAX_AMPERAGE):
    """
    Calculate the desired charging amperage for the EV charger based on current household power usage and water heater power consumption.

    Args:
        current_power_usage (float): Current household power usage in watts.
        water_heater_power (float): Current water heater power consumption in watts.
        max_total_load (int): Maximum allowable total load in watts.
        nominal_voltage (int): Nominal voltage in volts.
        min_amperage (int): Minimum allowable charging current in amperes.
        max_amperage (int): Maximum allowable charging current in amperes.

    Returns:
        int: Desired charging amperage within the allowable range.
    """
    # Calculate total power usage
    total_power_usage = current_power_usage + water_heater_power

    # Calculate available capacity in watts
    available_capacity = max_total_load - total_power_usage

    # Calculate desired amperage
    desired_amperage = available_capacity // nominal_voltage

    # Ensure the desired amperage is within the allowable range
    if desired_amperage < min_amperage:
        return min_amperage
    elif desired_amperage > max_amperage:
        return max_amperage
    else:
        return int(desired_amperage)

# Schedule Water Heater
def schedule_water_heater_old(prices, current_time, water_heater_state):
    """
    Schedule the water heater operation based on electricity prices and time of day.

    Args:
        prices (dict): Dictionary with hour as key and price as value.
        current_time (datetime): Current datetime object.
        water_heater_state (str): Current state of the water heater; 'on' or 'off'.

    Returns:
        str: Desired state of the water heater; 'on' or 'off'.
    """
    # Define time ranges
    morning_deadline = current_time.replace(hour=7, minute=0, second=0, microsecond=0)
    afternoon_deadline = current_time.replace(hour=15, minute=0, second=0, microsecond=0)

    # Determine if heating is needed based on time
    if current_time < morning_deadline:
        target_deadline = morning_deadline
    elif current_time < afternoon_deadline:
        target_deadline = afternoon_deadline
    else:
        target_deadline = None

    if target_deadline:
        # Calculate remaining time until the target deadline
        time_remaining = (target_deadline - current_time).total_seconds() / 3600  # in hours

        # Determine the cheapest hour within the remaining time
        current_hour = current_time.hour
        upcoming_hours = {hour: price for hour, price in prices.items() if current_hour <= int(hour) < current_hour + time_remaining}
        if upcoming_hours:
            cheapest_hour = min(upcoming_hours, key=upcoming_hours.get)
            if int(cheapest_hour) == current_hour:
                return 'on'
            else:
                return 'off'
        else:
            return 'off'
    else:
        return 'off'

def schedule_water_heater(prices, current_time, water_heater_state, high_price_threshold=100):
    # Initialize variables
    total_on_hours = 0
    evening_off_hours = 0
    consecutive_off_hours = 0
    schedule = {}

    # Define time periods
    evening_start = 16
    evening_end = 23
    night_start = 23
    night_end = 7
    day_start = 7
    day_end = 16

    # Helper function to extract hour from 'day-hour' key
    def extract_hour(key):
        try:
            return int(key.split('-')[1])
        except (IndexError, ValueError):
            return None

    # Evening scheduling (16:00 - 23:00)
    for key in prices:
        hour = extract_hour(key)
        if hour is not None and evening_start <= hour <= evening_end:
            if prices[key] > high_price_threshold and evening_off_hours < 3 and consecutive_off_hours < 1:
                schedule[hour] = 'off'
                evening_off_hours += 1
                consecutive_off_hours += 1
            else:
                schedule[hour] = 'on'
                total_on_hours += 1
                consecutive_off_hours = 0

    # Night scheduling (23:00 - 07:00)
    for key in prices:
        hour = extract_hour(key)
        if hour is not None and (hour >= night_start or hour < night_end):
            schedule[hour] = 'on'
            total_on_hours += 1

    # Ensure 2 hours on before 07:00
    if schedule.get(5) == 'off' and schedule.get(6) == 'off':
        schedule[5] = 'on'
        schedule[6] = 'on'
        total_on_hours += 2

    # Daytime scheduling (07:00 - 16:00)
    for key in prices:
        hour = extract_hour(key)
        if hour is not None and day_start <= hour < day_end:
            if prices[key] > high_price_threshold:
                schedule[hour] = 'off'
            else:
                schedule[hour] = 'on'
                total_on_hours += 1

    # Ensure minimum 12 hours of operation
    if total_on_hours < 12:
        additional_hours_needed = 12 - total_on_hours
        # Turn on during the cheapest off hours
        off_hours = [hour for hour, state in schedule.items() if state == 'off']
        off_hours.sort(key=lambda x: prices[f"{current_time.day}-{x}"])
        for hour in off_hours[:additional_hours_needed]:
            schedule[hour] = 'on'
            total_on_hours += 1

    # Determine the desired state for the current hour
    current_hour = current_time.hour
    desired_state = schedule.get(current_hour, water_heater_state)
    print(desired_state)
    return desired_state

def schedule_water_heater_old(prices, current_time, water_heater_state, high_price_threshold = 100):
    """
    Schedule the water heater operation based on electricity prices and time constraints.

    Args:
        prices (dict): Dictionary with hour (0-23) as key and price as value.
        current_time (datetime): Current datetime object.
        water_heater_state (str): Current state of the water heater; 'on' or 'off'.
        high_price_threshold (float): Price threshold above which the water heater should be turned off during the day.

    Returns:
        str: Desired state of the water heater; 'on' or 'off'.
    """
    # Initialize variables
    total_on_hours = 0
    evening_off_hours = 0
    consecutive_off_hours = 0
    schedule = {}

    # Define time periods
    evening_start = 16
    evening_end = 23
    night_start = 23
    night_end = 7
    day_start = 7
    day_end = 16

    # Evening scheduling (16:00 - 23:00)
    for hour in range(evening_start, evening_end + 1):
        if prices[hour] > high_price_threshold and evening_off_hours < 3 and consecutive_off_hours < 1:
            schedule[hour] = 'off'
            evening_off_hours += 1
            consecutive_off_hours += 1
        else:
            schedule[hour] = 'on'
            total_on_hours += 1
            consecutive_off_hours = 0

    # Night scheduling (23:00 - 07:00)
    for hour in range(night_start, 24):
        schedule[hour] = 'on'
        total_on_hours += 1
    for hour in range(0, night_end):
        schedule[hour] = 'on'
        total_on_hours += 1

    # Ensure 2 hours on before 07:00
    if schedule[5] == 'off' and schedule[6] == 'off':
        schedule[5] = 'on'
        schedule[6] = 'on'
        total_on_hours += 2

    # Daytime scheduling (07:00 - 16:00)
    for hour in range(day_start, day_end):
        if prices[hour] > high_price_threshold:
            schedule[hour] = 'off'
        else:
            schedule[hour] = 'on'
            total_on_hours += 1

    # Ensure minimum 12 hours of operation
    if total_on_hours < 12:
        additional_hours_needed = 12 - total_on_hours
        # Turn on during the cheapest off hours
        off_hours = [hour for hour, state in schedule.items() if state == 'off']
        off_hours.sort(key=lambda x: prices[x])
        for hour in off_hours[:additional_hours_needed]:
            schedule[hour] = 'on'
            total_on_hours += 1

    # Determine the desired state for the current hour
    current_hour = current_time.hour
    desired_state = schedule.get(current_hour, water_heater_state)

    return desired_state

def mqtt_publish(topic, message, username=None, password=None):
    client = mqtt.Client()
    if username and password:
        client.username_pw_set(username, password)
    print(f"{MQTT_BROKER}, {MQTT_PORT}")
    #client.connect(MQTT_BROKER, MQTT_PORT)
    message = 1
    client.publish(topic, message)
    client.disconnect()
# Control Water Heater via MQTT
def control_water_heater(state):
    print(state)
    mqtt_publish(WATER_HEATER_TOPIC, state)
    try:
        mqtt_publish(WATER_HEATER_TOPIC, state)
        logging.info(f"Water heater turned {state}.")
    except Exception as e:
        logging.error(f"Failed to control water heater: {e}")
def charger_settings():
    # Define the API URL for retrieving chargers
    api_url = 'https://api.zaptec.com/api/chargers'
    tokens = get_access_token()
    access_token = tokens["access_token"]
    # Set the headers, including the Authorization header with the bearer token
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }

    # Make the GET request to retrieve charger information
    response = requests.get(api_url, headers=headers)
    response.raise_for_status()  # Raise an error for bad status codes

    # Parse the JSON response to extract charger data
    chargers_data = response.json()
    print(json.dumps(chargers_data, indent=4))

# Main Function
def main():
    global LAST_ACTIVITY_TIME, water_heater_power
    water_heater_power = 0.0  # Initialize water_heater_power

    # MQTT Client Setup
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, 1883, 60)
    client.loop_start()

    # Fetch initial prices and plan schedule
    fetch_entsoe_prices()
    plan_charging_schedule()

    try:
        while True:
            current_time = datetime.now(LOCAL_TZ)
            current_power = get_current_power_usage()
            print(prices)
            if current_power is not None:
                # Calculate desired charging amperage
                desired_amperage = calculate_desired_amperage(current_power, water_heater_power)

                # Determine if current hour is in the cheapest schedule
                hour_key = f"{current_time.day}-{current_time.hour}"
                if hour_key in dict(cheapest_schedule):
                    set_charging_amperage(desired_amperage)
                    logging.info(f"Charging active: {desired_amperage}A during cheap hour {hour_key}")
                else:
                    set_charging_amperage(MIN_AMPERAGE)
                    logging.info(f"Not a charging hour. Setting amperage to minimum.")

                # Schedule water heater
                desired_water_heater_state = schedule_water_heater(prices, current_time, 'off')
                print(desired_water_heater_state)
                control_water_heater(desired_water_heater_state)

            # Refresh prices at 2 PM
            if current_time.hour == 14 and (current_time - timedelta(minutes=1)).hour != 14:
                fetch_entsoe_prices()
                plan_charging_schedule()

            charger_settings()
            time.sleep(60)  # Check every minute

    except KeyboardInterrupt:
        logging.info("Script terminated by user.")
    finally:
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()

