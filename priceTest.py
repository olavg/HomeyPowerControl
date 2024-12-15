import paho.mqtt.client as mqtt

# Define your broker address (could be localhost or an IP)
BROKER = "192.168.86.54"

# Callback when the client receives a CONNACK response from the server
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to MQTT broker.")
        # Subscribe to topics
        client.subscribe("ams/price/#")  # This will subscribe to all price intervals
        client.subscribe("ams/meter/import/active")
    else:
        print("Connection failed with code {}".format(rc))

# Callback when a PUBLISH message is received from the server
def on_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode("utf-8")
    
    # Handle price topics
    if topic.startswith("ams/price/"):
        # For example, "ams/price/0", "ams/price/1", etc.
        # You can extract the hour/index from the topic:
        hour = topic.split("/")[-1]
        print(f"Price for hour {hour} is: {payload}")
    
    # Handle power consumption
    elif topic == "ams/meter/import/active":
        print(f"Current power consumption: {payload} Watts")

# Set up the client
client = mqtt.Client()
client.on_connect = on_connect
client.on_message = on_message

# If your MQTT broker requires authentication, set it here:
# client.username_pw_set("username", "password")

# Connect to the broker
client.connect(BROKER, 1883, 60)

# Blocking call that processes network traffic, dispatches callbacks and
# handles reconnecting.
client.loop_forever()
