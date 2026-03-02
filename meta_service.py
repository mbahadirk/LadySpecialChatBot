import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

META_API_URL = "https://graph.facebook.com/v17.0"
PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")

def send_whatsapp_message(to_number, message_text):
    """
    Send a text message via WhatsApp Cloud API.
    """
    url = f"{META_API_URL}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {
            "body": message_text
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        print(f"Message sent to {to_number}: {response.json()}")
        return response.json()
    except Exception as e:
        print(f"Failed to send message: {e}")
        if response:
             print(f"Response: {response.text}")
        return None
