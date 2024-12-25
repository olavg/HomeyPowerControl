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
