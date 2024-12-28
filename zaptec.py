import os
import requests
import os
from dotenv import load_dotenv
import requests

# Load environment variables from .env file
load_dotenv()

# Access environment variables
username = os.getenv('ZAPTEC_USER')
password = os.getenv('ZAPTEC_PASSWORD')

# Use the credentials in your application logic
response = requests.get('https://example.com/api', auth=(username, password))
# Process the response as needed

ZAPTEC_AUTH_URL = "https://api.zaptec.com/oauth/token"

def get_access_token():
    # Retrieve credentials from environment variables
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
    response = requests.post(ZAPTEC_AUTH_URL, data=payload, headers=headers)
    response.raise_for_status()
    return response.json()

def refresh_access_token(refresh_token):
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    response = requests.post(ZAPTEC_AUTH_URL, data=payload, headers=headers)
    response.raise_for_status()
    return response.json()

import os
import json
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Retrieve connection details from environment variables
service_bus_host = os.getenv('ZAPTEC_SERVICE_BUS_HOST')
service_bus_username = os.getenv('ZAPTEC_SERVICE_BUS_USERNAME')
service_bus_password = os.getenv('ZAPTEC_SERVICE_BUS_PASSWORD')
service_bus_topic = os.getenv('ZAPTEC_SERVICE_BUS_TOPIC')
service_bus_subscription = os.getenv('ZAPTEC_SERVICE_BUS_SUBSCRIPTION')

# Construct the connection string
connection_str = (
    f'Endpoint=sb://{service_bus_host}/;'
    f'SharedAccessKeyName={service_bus_username};'
    f'SharedAccessKey={service_bus_password}'
)

def process_message(message):
    # Decode the message body
    message_body = b"".join(message.body)
    message_content = json.loads(message_body.decode('utf-8'))

    # Extract relevant information
    charger_id = message_content.get('ChargerId')
    state_id = message_content.get('StateId')
    timestamp = message_content.get('Timestamp')
    value_as_string = message_content.get('ValueAsString')

    # Check if the message corresponds to session energy
    # Replace 'YOUR_SESSION_ENERGY_STATE_ID' with the actual StateId for session energy
    if state_id == 'YOUR_SESSION_ENERGY_STATE_ID':
        session_energy = float(value_as_string)
        print(f"Charger ID: {charger_id}")
        print(f"Timestamp: {timestamp}")
        print(f"Current Session Energy Consumption: {session_energy} kWh")

# Initialize the Service Bus client
servicebus_client = ServiceBusClient.from_connection_string(conn_str=connection_str, logging_enable=True)

# Function to receive messages
def receive_messages():
    with servicebus_client:
        receiver = servicebus_client.get_subscription_receiver(
            topic_name=service_bus_topic,
            subscription_name=service_bus_subscription
        )
        with receiver:
            for message in receiver:
                process_message(message)
                receiver.complete_message(message)

if __name__ == "__main__":
    receive_messages()


exit()

# Example usage
try:
    # Fetch the initial access token
    tokens = get_access_token()
    access_token = tokens["access_token"]
    refresh_token = tokens["refresh_token"]

    print("Access Token:", access_token)
    print("Refresh Token:", refresh_token)

    # Example: Refresh the access token
    new_tokens = refresh_access_token(refresh_token)
    print("New Access Token:", new_tokens["access_token"])

except Exception as e:
    print(f"Error: {e}")
