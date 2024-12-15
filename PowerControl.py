import json
import time
import paho.mqtt.client as mqtt
from datetime import datetime

# Configuration
MQTT_BROKER = "192.168.86.54"     # Replace with your broker's IP/hostname
MQTT_PORT = 1883
MQTT_KEEPALIVE = 60

# Topics from which we read data
TOPIC_POWER_USAGE = "home/power/usage"                # kW (float)
TOPIC_POWER_PRICES = "home/power/prices"              # JSON array of 24 hourly prices
TOPIC_EXPENSIVE_HOURS = "home/power/expensive_hours"  # JSON array like [18,19,20]

# Topics to publish targets/setpoints
BASE_TOPIC = "home/control"
# Example:
# Panel ovens: home/control/panel_oven/<device_name>/target_temp
# Floor heating: home/control/floor/<device_name>/target_temp
# Water heater: home/control/waterheater/onoff

NORMAL_TEMPERATURES = {
    "toalett_panelovn": 22,
    "stue_panelovn": 20,
    "soverom_panelovn": 18
}

NORMAL_FLOOR_TEMPS = {
    "gang_gulvvarme": 21.5,
    "garderobe_gulvvarme": 21.5
}

MINIMUM_TEMP = 14

# Global data storage for MQTT callbacks
current_power_usage = None
heating_prices = None
expensive_hours = None

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to MQTT Broker!")
        # Subscribe to required topics
        client.subscribe(TOPIC_POWER_USAGE)
        client.subscribe(TOPIC_POWER_PRICES)
        client.subscribe(TOPIC_EXPENSIVE_HOURS)
    else:
        print(f"Failed to connect, return code {rc}")

def on_message(client, userdata, msg):
    global current_power_usage, heating_prices, expensive_hours
    topic = msg.topic
    payload = msg.payload.decode("utf-8")
    
    if topic == TOPIC_POWER_USAGE:
        try:
            current_power_usage = float(payload)
        except ValueError:
            print("Invalid power usage value received.")
    elif topic == TOPIC_POWER_PRICES:
        try:
            heating_prices = json.loads(payload)  # Array of floats for each hour
        except json.JSONDecodeError:
            print("Invalid JSON for heating prices.")
    elif topic == TOPIC_EXPENSIVE_HOURS:
        try:
            expensive_hours = json.loads(payload) # Array of ints representing hours
        except json.JSONDecodeError:
            print("Invalid JSON for expensive hours.")

def calculate_setpoints():
    """
    This function performs the logic that was previously in HomeyScript:
    - Determine if current hour is expensive or extremely expensive.
    - Adjust setpoints for panel ovens and floor heating.
    - Decide if water heater should be on/off.
    """
    if heating_prices is None or expensive_hours is None:
        print("Insufficient data (prices or expensive hours) to calculate setpoints.")
        return None
    
    now = datetime.now()
    current_hour = now.hour

    # Compute average price
    valid_prices = [p for p in heating_prices if p is not None]
    if not valid_prices:
        print("No valid prices, cannot compute setpoints.")
        return None
    avg_price = sum(valid_prices) / len(valid_prices)
    extreme_threshold = avg_price * 2

    # Identify the most expensive chosen hour from expensive_hours
    chosen_hours_with_prices = []
    for h in expensive_hours:
        idx = (h - current_hour) % 24
        price = heating_prices[idx] if 0 <= idx < len(heating_prices) else None
        if price is not None:
            chosen_hours_with_prices.append({"hour": h, "price": price})
    chosen_hours_with_prices.sort(key=lambda x: x["price"], reverse=True)
    most_expensive_hour = chosen_hours_with_prices[0] if chosen_hours_with_prices else None

    is_extremely_expensive = False
    if most_expensive_hour:
        is_extremely_expensive = (most_expensive_hour["price"] > extreme_threshold and 
                                  most_expensive_hour["hour"] == current_hour)

    # Calculate setpoints for panel ovens
    panel_ovens_setpoints = {}
    for name, desired in NORMAL_TEMPERATURES.items():
        if current_hour in expensive_hours:
            target = max(desired - 3, MINIMUM_TEMP)
        else:
            target = desired
        panel_ovens_setpoints[name] = target

    # Calculate setpoints for floor heating
    floor_setpoints = {}
    for name, desired in NORMAL_FLOOR_TEMPS.items():
        if current_hour in expensive_hours:
            target = max(desired - 3, MINIMUM_TEMP)
        else:
            target = desired
        floor_setpoints[name] = target

    # Water heater on/off
    water_heater_on = current_hour not in expensive_hours

    return {
        "panel_ovens": panel_ovens_setpoints,
        "floor_heating": floor_setpoints,
        "water_heater_on": water_heater_on,
        "is_extremely_expensive": is_extremely_expensive
    }

def publish_setpoints(client, setpoints):
    # Publish panel oven target temps
    for device_name, temp in setpoints["panel_ovens"].items():
        topic = f"{BASE_TOPIC}/panel_oven/{device_name}/target_temp"
        client.publish(topic, str(temp))
        print(f"Published {temp} to {topic}")

    # Publish floor heating target temps
    for device_name, temp in setpoints["floor_heating"].items():
        topic = f"{BASE_TOPIC}/floor/{device_name}/target_temp"
        client.publish(topic, str(temp))
        print(f"Published {temp} to {topic}")

    # Publish water heater state
    waterheater_topic = f"{BASE_TOPIC}/waterheater/onoff"
    client.publish(waterheater_topic, "true" if setpoints["water_heater_on"] else "false")
    print(f"Published {setpoints['water_heater_on']} to {waterheater_topic}")

    # Publish extremely expensive state
    extreme_topic = f"{BASE_TOPIC}/mode/extreme"
    client.publish(extreme_topic, "true" if setpoints["is_extremely_expensive"] else "false")
    print(f"Published {setpoints['is_extremely_expensive']} to {extreme_topic}")


if __name__ == "__main__":
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    client.connect(MQTT_BROKER, MQTT_PORT, MQTT_KEEPALIVE)

    # Start MQTT loop to process messages
    client.loop_start()

    # Give a few seconds to receive initial data from MQTT
    time.sleep(5)

    # Run logic once (if you want this periodically, you can loop or schedule it)
    setpoints = calculate_setpoints()
    if setpoints:
        publish_setpoints(client, setpoints)

    # If this script should just run once and exit, stop the loop:
    # If you want it to run continuously and update periodically, you could:
    # - Use a while True loop with a sleep interval
    # - Or run it from cron every 5 minutes
    client.loop_stop()
    client.disconnect()
