import paho.mqtt.client as mqtt
import time
import subprocess

# Define your broker address (could be localhost or an IP)
BROKER = "192.168.86.54"
LAST_ACTIVITY_TIME = time.time()

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to MQTT broker.")
        # Subscribe to the consumption topic
        client.subscribe("ams/meter/import/active")
        client.subscribe("ams/price/#")  # This will subscribe to all price intervals

    else:
        print("Connection failed with code {}".format(rc))

def on_message(client, userdata, msg):
    global LAST_ACTIVITY_TIME
    topic = msg.topic
    payload = msg.payload.decode("utf-8")

    if topic == "ams/meter/import/active":
        # Update last activity time whenever we get new consumption data
        LAST_ACTIVITY_TIME = time.time()
        print(f"Current power consumption: {payload} Watts")

def reboot_ams_reader():
    print("No activity for 5 minutes. Rebooting AMS reader now...")
    try:
        # Assuming GET request to the REBOOT_URL triggers a reboot.
        # If it's a button on a web page that triggers reboot, and not just a GET request,
        # you may need to replicate that behavior (e.g., a POST request or extra parameters).
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
    
    # If your MQTT broker requires auth:
    # client.username_pw_set("username", "password")
    
    client.connect(BROKER, 1883, 60)
    
    # Instead of using loop_forever, we use a loop that lets us periodically check activity.
    client.loop_start()

    try:
        while True:
            current_time = time.time()
            elapsed = current_time - LAST_ACTIVITY_TIME
            if elapsed > 300:  # 5 minutes
                reboot_ams_reader()
                # After reboot, reset the timestamp so we don't repeatedly reboot
                LAST_ACTIVITY_TIME = time.time()
            
            time.sleep(10)  # Check every 10 seconds
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()

if __name__ == "__main__":
    main()
