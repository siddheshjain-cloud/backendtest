# scripts/set_telegram_webhook.py

import os
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
WEBHOOK_URL = os.getenv('TELEGRAM_WEBHOOK_URL')  # e.g., https://yourdomain.com/api/telegram/webhook


def set_webhook():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    payload = {
        'url': WEBHOOK_URL,
        'allowed_updates': ['message']
    }

    response = requests.post(url, json=payload)
    print(response.json())


if __name__ == '__main__':
    set_webhook()