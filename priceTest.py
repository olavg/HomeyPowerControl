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
    # This function should contain the logic needed to reboot your AMS reader.
    # This might be sending a command over SSH, or running a local script, etc.
    # Example: run a script called "reboot_ams.sh" that handles the reboot process.
    # subprocess.run(["/path/to/reboot_ams.sh"])
    print("No activity for 5 minutes. Rebooting AMS reader now...")
    # Example: If the AMS reader is a device connected via a local command:
    # subprocess.run(["sudo", "systemctl", "restart", "ams-reader.service"])

def main():
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
