import os
import requests

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    text = "✅ Test message from GitHub Actions (test_telegram.py)"

    resp = requests.post(url, data={"chat_id": chat_id, "text": text})
    print("Status code:", resp.status_code)
    print("Response text:", resp.text)
    resp.raise_for_status()
    print("Done.")

if __name__ == "__main__":
    main()
