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

#mqtt v311
client = mqtt.Client(protocol=mqtt.MQTTv311) 

# Configuration
MQTT_TOPIC = "controlPower"
username = os.getenv('ZAPTEC_USER')
password = os.getenv('ZAPTEC_PASSWORD')
ZAPTEC_AUTH_URL = "https://api.zaptec.com/oauth/token"
ZAPTEC_API_URL = "https://api.zaptec.com/api/installation/{installation_id}/update"
ZAPTEC_API_KEY = os.getenv("ZAPTEC_API_KEY")
CHARGER_ID = os.getenv("ZAPTEC_CHARGER_ID")
ENTSOE_API_KEY = os.getenv("ENTSOE_API_KEY")
MQTT_BROKER = os.getenv("MQTT_BROKER", "192.168.86.54")
MQTT_PORT = "1883"
TOTAL_DEVICES = 6  # 5 floors + 1 water heater
WATER_HEATER_TOPIC = f"{MQTT_TOPIC}/water_heater"
WATER_HEATER_POWER_TOPIC = f"{WATER_HEATER_TOPIC}/power"
WATER_HEATER_PRIORITY_THRESHOLD = 20 * 60  # 20 minutes in seconds
FLOOR_TOPICS = [f"{MQTT_TOPIC}/floor_heating/floor_{i}" for i in range(1, 6)]  # Topics for 5 floors
FLOOR_WATTAGE = [500, 500, 500, 500, 500]  # Estimated wattage for each floor
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
water_heater_active_since = None
water_heater_power = 0.0  # Initialize water_heater_power
# Global variable to store rolling load values
rolling_loads = []

def track_water_heater_priority(water_heater_power):
    """
    Track the water heater's power draw duration and prioritize it if necessary.

    Args:
        water_heater_power (float): Current power draw of the water heater in Watts.

    Returns:
        bool: True if the water heater should be prioritized, False otherwise.
    """
    global water_heater_active_since

    if water_heater_power > 0:  # Water heater is drawing power
        if water_heater_active_since is None:
            water_heater_active_since = time.time()  # Start tracking
        else:
            elapsed_time = time.time() - water_heater_active_since
            if elapsed_time >= WATER_HEATER_PRIORITY_THRESHOLD:
                print("Water heater needs prioritization due to prolonged usage.")
                return True
    else:  # Water heater is not drawing power
        water_heater_active_since = None  # Reset tracking

    return False
def update_rolling_loads(current_power, window_size=15):
    """
    Updates the rolling load values and calculates the average over the last 15 minutes.

    Args:
        current_power (float): Current power usage in watts.
        window_size (int): The number of minutes to consider for the rolling average (default: 15).

    Returns:
        float: The average power usage over the rolling window.
    """
    global rolling_loads

    # Add the current power reading to the rolling loads list
    rolling_loads.append(current_power)

    # Ensure the list doesn't exceed the window size
    if len(rolling_loads) > window_size:
        rolling_loads.pop(0)

    # Calculate the average power usage
    average_load = sum(rolling_loads) / len(rolling_loads)

    logging.info(f"Updated rolling load average: {average_load:.2f} Watts over the last {len(rolling_loads)} minutes.")
    return average_load

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
    global last_zaptec_update, installation_id
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
        logging.info(f"Using Installation ID: {installation_id}")
    else:
        raise Exception("No installations found or unexpected response format.")

    # API endpoint for updating available current
    url = ZAPTEC_API_URL.format(installation_id=installation_id)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    # Try with simplified payload
    payload = {
        "AvailableCurrent": amperage
    }

    # Log payload before sending
    logging.info(f"Payload being sent to {url}: {payload}")

    # Make API request
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        logging.info(f"Installation available current set to {amperage}A successfully.")
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"HTTP error occurred: {http_err}")
        logging.error(f"Response content: {http_err.response.text}")
        raise
    except requests.exceptions.RequestException as req_err:
        logging.error(f"Request error occurred: {req_err}")
        raise

    # Update the timestamp of the last successful update
    last_zaptec_update = now
def publish_device_state(topic, state):
    """
    Publish the desired state of a device to MQTT with error handling.

    Args:
        topic (str): MQTT topic for the device.
        state (str): Desired state ('on' or 'off').

    Returns:
        bool: True if the state was published successfully, False otherwise.
    """
    try:
        if mqtt_publish(topic, state):
            logging.info(f"Successfully published state '{state}' to topic '{topic}'.")
            return True
        else:
            logging.error(f"Failed to publish state '{state}' to topic '{topic}'.")
            return False
    except Exception as e:
        logging.error(f"Unexpected error while publishing state to topic '{topic}': {e}")
        return False

Here’s an updated version of the function that introduces a variable to track the power consumption (in watts) for each floor. This allows you to assess the impact of each floor individually, giving you better granularity in managing power consumption.

Updated Function with Power Tracking per Floor
import time
import logging
import paho.mqtt.client as mqtt

def assess_device_impact(current_power, topics, mqtt_client=client, threshold_load=None):
    """
    Assess the power impact of devices based on their current MQTT states and track watts per floor.

    Args:
        current_power (float): Current total power usage in watts.
        topics (list): List of MQTT topics for devices.
        mqtt_client (mqtt.Client): An active MQTT client to fetch states.
        threshold_load (float, optional): Threshold load to determine if devices should remain off.

    Returns:
        dict: Mapping of topics to their desired state ('on' or 'off').
        dict: Mapping of topics to their respective power consumption in watts.
    """

    # Dictionary to store the power consumption of each floor
    floor_watts = {topic: 0 for topic in topics}

    # Dictionary to store the current state of each device
    mqtt_states = {}

    def on_message(client, userdata, msg):
        """Callback for processing received messages."""
        try:
            state = int(msg.payload.decode("utf-8"))
            mqtt_states[msg.topic] = state
            # Example: Assign wattage per topic dynamically (customize as needed)
            if state == 1:  # If the device is on
                floor_watts[msg.topic] = 500  # Example: 500 watts per active floor
            else:
                floor_watts[msg.topic] = 0
            logging.info(f"Received state from {msg.topic}: {state}, Power: {floor_watts[msg.topic]} Watts")
        except ValueError:
            logging.warning(f"Non-numeric state received from {msg.topic}: {msg.payload.decode('utf-8')}")

    # Subscribe to all topics and wait for responses
    mqtt_client.on_message = on_message
    for topic in topics:
        mqtt_client.subscribe(topic)
        logging.info(f"Subscribed to {topic}")

    # Allow time to receive messages
    mqtt_client.loop_start()
    time.sleep(2)  # Adjust timeout as needed for all messages to be received
    mqtt_client.loop_stop()

    # Process states and assess device impacts
    device_states = {}
    for topic in topics:
        current_state = mqtt_states.get(topic, None)
        if current_state is None:
            logging.warning(f"State for topic {topic} is unavailable. Skipping...")
            continue

        # Calculate power impact based on the current state
        if current_state == 1:  # Device is on
            logging.info(f"Device {topic} is currently on, consuming {floor_watts[topic]} Watts.")
            estimated_impact = floor_watts[topic]  # Impact is the floor's power consumption
            logging.info(f"Impact of turning off {topic}: {estimated_impact:.2f} Watts")

            # Check against the threshold_load if provided
            if threshold_load is not None and (current_power - estimated_impact) < threshold_load:
                logging.info(f"Threshold load reached. Keeping {topic} on.")
                device_states[topic] = 'on'
            else:
                device_states[topic] = 'off'
        else:  # Device is off
            logging.info(f"Device {topic} is already off.")
            device_states[topic] = 'off'

    return device_states, floor_watts

def assess_device_impact_old(current_power, topics, threshold_load=None):
    """
    Assess the power impact of turning off devices controlled by MQTT topics.

    Args:
        current_power (float): Current power usage in watts.
        topics (list): List of MQTT topics to control devices.
        threshold_load (float, optional): Threshold load to determine if devices should remain off.

    Returns:
        dict: Mapping of topics to their desired state ('on' or 'off').
    """
    device_states = {}
    for topic in topics:
        # Turn off the device
        publish_device_state(topic, 'off')
        time.sleep(5)  # Wait for power reading to stabilize

        # Measure the impact
        new_power = get_current_power_usage()
        if new_power is None:
            logging.warning(f"Power usage could not be fetched for topic {topic}. Skipping...")
            continue

        # Log the impact
        impact = current_power - new_power
        logging.info(f"Impact of turning off {topic}: {impact:.2f} Watts")

        # Check against the threshold_load if provided
        if threshold_load is not None and new_power < threshold_load:
            logging.info(f"Threshold load reached. Turning {topic} back on.")
            publish_device_state(topic, 'on')
            device_states[topic] = 'on'
        else:
            device_states[topic] = 'off'

    return device_states


# MQTT Handlers
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logging.info("Connected to MQTT broker.")
        topics = [
            "ams/meter/import/active",
            "home/water_heater/power"
        ]
        for topic in topics:
            client.subscribe(topic)
            logging.info(f"Subscribed to topic: {topic}")
    else:
        logging.error(f"Connection failed with code {rc}")

def on_message(client, userdata, msg):
    global LAST_ACTIVITY_TIME, last_consumption, prices, water_heater_power
    LAST_ACTIVITY_TIME = time.time()
    try:
        topic = msg.topic
        payload = msg.payload.decode("utf-8")
        try:
            payload = float(payload)
        except ValueError:
            logging.warning(f"Non-numeric payload received on topic {topic}: {payload}")
            return

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
    except Exception as e:
        logging.warning(f"Unexpected error processing message on topic {msg.topic}: {e}")

def on_disconnect(client, userdata, rc):
    logging.warning(f"Disconnected with return code {rc}. Attempting to reconnect...")
    if rc != 0:
        try:
            client.reconnect()
        except Exception as e:
            logging.error(f"Reconnection failed: {e}")


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
  
def get_current_power_usage(api_base_url=AMS_METER_API_BASE_URL, timeout=5, fallback=0.0):
    """
    Fetch the current power usage from the AMS Leser HTTP API.

    Args:
        api_base_url (str): Base URL of the AMS Leser API.
        timeout (int): Request timeout in seconds.
        fallback (float): Value to return if the request fails.

    Returns:
        float: Current power usage in watts or the fallback value.
    """
    endpoint = f"{api_base_url}/data.json"
    try:
        response = requests.get(endpoint, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        current_power = float(data.get("w", fallback))
        logging.info(f"Current power usage: {current_power:.2f} Watts")
        return current_power
    except requests.RequestException as e:
        logging.error(f"Error fetching power usage: {e}")
        return fallback

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

def mqtt_publish(topic, message, broker=MQTT_BROKER, port=1883, username=None, password=None):
    """
    Publishes a message to an MQTT topic with error handling.

    Args:
        topic (str): The MQTT topic to publish to.
        message (str): The message payload.
        broker (str): MQTT broker address.
        port (int): MQTT broker port (default: 1883).
        username (str): Optional MQTT username.
        password (str): Optional MQTT password.

    Returns:
        bool: True if the message was published successfully, False otherwise.
    """

    # Optional authentication
    if username and password:
        client.username_pw_set(username, password)

    try:
        client.connect(broker, port)
        result, mid = client.publish(topic, message)

        if result == mqtt.MQTT_ERR_SUCCESS:
            logging.info(f"Message '{message}' published to topic '{topic}' successfully.")
            return True
        else:
            logging.error(f"Failed to publish message '{message}' to topic '{topic}'. Return code: {result}")
            return False
    except Exception as e:
        logging.error(f"Failed to publish message '{message}' to topic '{topic}': {e}")
        return False
    finally:
        client.disconnect()

# Control Water Heater via MQTT
def control_water_heater(state):
    print(state)
    if mqtt_publish(WATER_HEATER_TOPIC, state):
        logging.info(f"Successfully set water heater state to {state}.")
    else:
        logging.error(f"Failed to set water heater state to {state}.")
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

def get_messaging_connection_details(installation_id):
    """
    Retrieves the messaging connection details for a given installation.

    Parameters:
        installation_id (str): The ID of the installation.

    Returns:
        dict: Messaging connection details if successful.
        None: If the request fails.
    """
    # Define the API URL for retrieving messaging connection details
    api_url = f'https://api.zaptec.com/api/installation/{installation_id}/messagingConnectionDetails'
    
    # Retrieve the access token
    tokens = get_access_token()
    access_token = tokens["access_token"]
    
    # Set the headers, including the Authorization header with the bearer token
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }
    
    try:
        # Make the GET request to retrieve messaging connection details
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes
        
        # Parse the JSON response to extract connection details
        connection_details = response.json()
        return connection_details
    
    except requests.exceptions.HTTPError as http_err:
        print(f'HTTP error occurred: {http_err}')
    except Exception as err:
        print(f'An error occurred: {err}')
    
    return None

def get_user_group_messaging_connection_details(user_group_id):
    """
    Retrieves the messaging connection details for a given user group.

    Parameters:
        user_group_id (str): The ID of the user group.

    Returns:
        dict: Messaging connection details if successful.
        None: If the request fails.
    """
    # Define the API URL for retrieving messaging connection details
    api_url = f'https://api.zaptec.com/api/userGroups/{user_group_id}/messagingConnectionDetails'
    
    # Retrieve the access token
    tokens = get_access_token()
    access_token = tokens["access_token"]
    
    # Set the headers, including the Authorization header with the bearer token
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }
    
    try:
        # Make the GET request to retrieve messaging connection details
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes
        
        # Parse the JSON response to extract connection details
        connection_details = response.json()
        return connection_details
    
    except requests.exceptions.HTTPError as http_err:
        print(f'HTTP error occurred: {http_err}')
    except Exception as err:
        print(f'An error occurred: {err}')
    
    return None

### Car charging
def manage_car_charging(current_time, current_house_load, current_price, high_price_threshold, max_total_load=MAX_TOTAL_LOAD):
    """
    Manage car charging based on current load, price, and user happiness.

    Args:
        current_time (datetime): Current time.
        current_house_load (float): Current house power draw in watts.
        current_price (float): Current electricity price.
        high_price_threshold (float): Price threshold to pause/reduce charging.
        max_total_load (int): Maximum allowable load for the house in watts.

    Returns:
        int: Desired charging amperage.
    """
    available_power = max_total_load - current_house_load

    # Adjust charging based on price
    if current_price > high_price_threshold:
        logging.info("Price is too high; reducing charging to minimum.")
        return MIN_AMPERAGE

    # Calculate desired amperage based on available power
    desired_amperage = available_power // NOMINAL_VOLTAGE
    desired_amperage = min(max(desired_amperage, MIN_AMPERAGE), MAX_AMPERAGE)

    logging.info(f"Setting charging to {desired_amperage}A based on available power and price.")
    return desired_amperage

def adjust_charging_for_water_heater(average_load, threshold_load, current_power, water_heater_power, nominal_voltage=230, min_amperage=6, max_amperage=32):
    """
    Adjusts the charging amperage for the EV charger based on average load, water heater power,
    and the total threshold load.

    Args:
        average_load (float): Average power usage in watts over a rolling window.
        threshold_load (float): Maximum allowable total load in watts.
        current_power (float): Current household power usage in watts.
        water_heater_power (float): Power draw of the water heater in watts.
        nominal_voltage (int): Nominal voltage in volts (default: 230).
        min_amperage (int): Minimum allowable charging current in amperes (default: 6).
        max_amperage (int): Maximum allowable charging current in amperes (default: 32).

    Returns:
        int: Desired charging amperage within the allowable range.
    """
    # Calculate available capacity by subtracting average load and water heater power from the threshold
    available_capacity = threshold_load - average_load

    # Include water heater power only if it's currently active
    #if water_heater_power > 0:
    #    logging.info(f"Including water heater power in calculation: {water_heater_power} W")
    #    available_capacity -= water_heater_power

    # Calculate the maximum allowable amperage based on available capacity
    desired_amperage = available_capacity // nominal_voltage

    # Constrain the desired amperage within the allowed range
    if desired_amperage < min_amperage:
        logging.warning(f"Desired amperage ({desired_amperage}A) is below minimum. Using minimum: {min_amperage}A")
        return min_amperage
    elif desired_amperage > max_amperage:
        logging.info(f"Desired amperage ({desired_amperage}A) exceeds maximum. Using maximum: {max_amperage}A")
        return max_amperage

    logging.info(f"Calculated charging amperage: {int(desired_amperage)}A")
    return int(desired_amperage)
def setup_mqtt_client(broker, port=1883, keepalive=60, username=None, password=None):
    """
    Sets up and connects an MQTT client with error handling and optional authentication,
    using MQTT version 3.1.1 for compatibility.

    Args:
        broker (str): MQTT broker address.
        port (int): MQTT broker port (default: 1883).
        keepalive (int): Keepalive interval in seconds (default: 60).
        username (str): Optional MQTT username.
        password (str): Optional MQTT password.

    Returns:
        mqtt.Client: Configured and connected MQTT client.
    """
    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            logging.info("Connected to MQTT broker successfully.")
        else:
            logging.error(f"Failed to connect to MQTT broker. Return code: {rc}")

    def on_disconnect(client, userdata, rc):
        if rc != 0:
            logging.warning("Unexpected disconnection. Reconnecting to MQTT broker...")
            reconnect_mqtt_client(client)

    def on_message(client, userdata, msg):
        logging.info(f"Received message on topic {msg.topic}: {msg.payload.decode()}")

    def reconnect_mqtt_client(client):
        try:
            client.reconnect()
            logging.info("Reconnected to MQTT broker.")
        except Exception as e:
            logging.error(f"Reconnection failed: {e}")
            time.sleep(5)  # Retry after a delay

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    # Optional authentication
    if username and password:
        client.username_pw_set(username, password)

    # Establish connection with retries
    connected = False
    for attempt in range(3):
        try:
            client.connect(broker, port, keepalive)
            connected = True
            break
        except Exception as e:
            logging.error(f"Connection attempt {attempt + 1} failed: {e}")
            time.sleep(5)

    if not connected:
        logging.critical("Unable to connect to MQTT broker after multiple attempts. Exiting...")
        raise ConnectionError("MQTT broker connection failed.")

    client.loop_start()
    return client


def setup_mqtt_client___(broker, port=1883, keepalive=60, username=None, password=None):
    """
    Sets up and connects an MQTT client with improved error handling, optional authentication,
    and support for the latest callback API version.

    Args:
        broker (str): MQTT broker address.
        port (int): MQTT broker port (default: 1883).
        keepalive (int): Keepalive interval in seconds (default: 60).
        username (str): Optional MQTT username.
        password (str): Optional MQTT password.

    Returns:
        mqtt.Client: Configured and connected MQTT client.
    """
    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            logging.info("Connected to MQTT broker successfully.")
        else:
            logging.error(f"Failed to connect to MQTT broker. Return code: {rc}")

    def on_disconnect(client, userdata, rc):
        if rc != 0:
            logging.warning(f"Unexpected disconnection. Reconnecting to MQTT broker...")
            reconnect_mqtt_client(client)

    def on_message(client, userdata, msg, properties=None):
        logging.info(f"Received message on topic {msg.topic}: {msg.payload.decode()}")

    def reconnect_mqtt_client(client):
        try:
            client.reconnect()
            logging.info("Reconnected to MQTT broker.")
        except Exception as e:
            logging.error(f"Reconnection failed: {e}")
            time.sleep(5)  # Retry after a delay

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    # Optional authentication
    if username and password:
        client.username_pw_set(username, password)

    # Establish connection with retries
    connected = False
    for attempt in range(3):
        try:
            client.connect(broker, port, keepalive)
            connected = True
            break
        except Exception as e:
            logging.error(f"Connection attempt {attempt + 1} failed: {e}")
            time.sleep(5)

    if not connected:
        logging.critical("Unable to connect to MQTT broker after multiple attempts. Exiting...")
        raise ConnectionError("MQTT broker connection failed.")

    client.loop_start()
    return client

# Main Function

def main():
    global LAST_ACTIVITY_TIME, water_heater_power
    water_heater_power = 2000  # Initialize water heater power draw (2kW)
    # MQTT Client Setup
    client = setup_mqtt_client(
        broker=MQTT_BROKER,
        port=1883,
        keepalive=60,
        username=username,  
        password=password)  

    # Fetch initial prices and plan schedule
    fetch_entsoe_prices()
    plan_charging_schedule()

    try:
        while True:
            current_time = datetime.now(LOCAL_TZ)
            current_power = get_current_power_usage()
            logging.info(f"Current power usage: {current_power} Watts")
            # Check water heater priority
            prioritize_water_heater = track_water_heater_priority(water_heater_power)
            
            if current_power is not None:
                # Update rolling window with current power usage
                average_load = update_rolling_loads(current_power)
                if prioritize_water_heater:
                    print("Prioritizing water heater; reducing charging load.")
                    ###not implemented
                # Assess device impact and control devices
                device_states = assess_device_impact(
                    current_power=current_power,
                    topics=FLOOR_TOPICS + [WATER_HEATER_TOPIC],
                    threshold_load=MAX_TOTAL_LOAD
                )
                for topic, state in device_states.items():
                    publish_device_state(topic, state)

                # Adjust charging current to accommodate other devices
                desired_amperage = adjust_charging_for_water_heater(
                    average_load=average_load,
                    threshold_load=MAX_TOTAL_LOAD,
                    current_power=current_power,
                    water_heater_power=water_heater_power
                )
                set_charging_amperage(desired_amperage)

                # Schedule water heater for cheaper periods
                desired_water_heater_state = schedule_water_heater(prices, current_time, 'off')
                control_water_heater(desired_water_heater_state)

            # Refresh prices at 2 PM
            if current_time.hour == 14 and (current_time - timedelta(minutes=1)).hour != 14:
                fetch_entsoe_prices()
                plan_charging_schedule()

            # Log charger settings (optional)
            charger_settings()

            time.sleep(60)  # Check every minute

    except KeyboardInterrupt:
        logging.info("Script terminated by user.")
    finally:
        client.loop_stop()
        client.disconnect()



if __name__ == "__main__":
    main()

