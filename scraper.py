import os
import re
import json
from pathlib import Path
from datetime import datetime, date

import requests
from bs4 import BeautifulSoup

# Overland Track availability page
URL = "https://azapps.customlinc.com.au/tasparksoverland/BookingCat/Availability/?Category=OVERLAND"

# Date window end
END_DATE = date(2026, 5, 31)

# File used to remember the last availability list
STATE_FILE = Path("state.json")


def get_start_date() -> date:
    """
    Start date is 'today' on the GitHub runner.
    """
    return date.today()


def parse_availability(lines):
    """
    Parse lines of text into a list of (date, status, spots) tuples.

    We treat each "date line" as the start of a block, up to the next date line.
    """

    date_pattern = re.compile(
        r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+"
        r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})"
    )

    # Collect indices of all lines that look like dates
    date_indices = []
    for i, line in enumerate(lines):
        if date_pattern.search(line):
            date_indices.append(i)

    results = []

    for idx, start_idx in enumerate(date_indices):
        # End of this block is the next date line, or the end of the list
        end_idx = date_indices[idx + 1] if idx + 1 < len(date_indices) else len(lines)
        block = lines[start_idx:end_idx]

        # First line of block is the date line
        m = date_pattern.search(block[0])
        if not m:
            continue

        _, day, mon_abbr, year = m.groups()
        date_str = f"{day} {mon_abbr} {year}"

        try:
            dt = datetime.strptime(date_str, "%d %b %Y").date()
        except ValueError:
            continue

        status = None
        spots = None

        # Look through this block only for status and spots
        for line in block[1:]:
            if "Fully Booked" in line:
                status = "Fully Booked"
            elif "Available" in line and status is None:
                # Status is "Available" only if not already Fully Booked
                status = "Available"

            # Look for "X Available"
            if "Available" in line:
                m2 = re.search(r"(\d+)\s+Available", line)
                if m2:
                    spots = int(m2.group(1))

        results.append((dt, status, spots))

    return results


def load_previous_state():
    """
    Load last known availability from state.json.

    Returns:
      - None if first run / file missing
      - list of {"date": "YYYY-MM-DD", "spots": int} otherwise
    """
    if not STATE_FILE.exists():
        return None

    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("availabilities", [])
    except Exception:
        return None


def save_state(avail_list):
    """
    Save current availability list to state.json.
    """
    data = {"availabilities": avail_list}
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def send_telegram_message(text: str):
    """
    Send a text message via Telegram bot.
    """
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, data={"chat_id": chat_id, "text": text})
    resp.raise_for_status()
    print("Telegram notification sent.")


def send_availability_list_message(start_date: date, interesting):
    """
    Send a message listing all currently available dates.
    """
    lines = [
        "📋 Overland Track availability",
        f"Window: {start_date.strftime('%d %b %Y')} – {END_DATE.strftime('%d %b %Y')}",
        "",
    ]

    for dt, status, spots in sorted(interesting, key=lambda x: x[0]):
        date_str = dt.strftime("%A %d %b %Y")
        if spots is not None:
            lines.append(f"- {date_str}: {spots} spots ({status})")
        else:
            lines.append(f"- {date_str}: {status or 'Unknown status'}")

    text = "\n".join(lines)
    send_telegram_message(text)


def send_no_availability_message(start_date: date):
    """
    Send a message saying there is currently no availability.
    """
    text = (
        "⚠️ Overland Track availability changed.\n"
        f"There is currently NO availability between "
        f"{start_date.strftime('%d %b %Y')} and {END_DATE.strftime('%d %b %Y')}."
    )
    send_telegram_message(text)


def check_overland():
    start_date = get_start_date()
    print(f"Checking availability between {start_date} and {END_DATE}...")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; OverlandChecker/1.0; "
            "+https://github.com/yourname/overland-availability-bot)"
        )
    }

    resp = requests.get(URL, headers=headers, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text(separator="\n")

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    all_days = parse_availability(lines)
    print(f"Found {len(all_days)} date entries on the page.")

    # Filter by date range and availability
    interesting = []
    for dt, status, spots in all_days:
        if not (start_date <= dt <= END_DATE):
            continue

        # Treat as available if status contains "Available" and spots > 0 (if known)
        if status and "Available" in status and (spots is None or spots > 0):
            interesting.append((dt, status, spots))

    print(f"Found {len(interesting)} available dates in the desired window.")

    # Build simplified state representation
    current_state = [
        {"date": dt.isoformat(), "spots": spots}
        for dt, status, spots in sorted(interesting, key=lambda x: x[0])
    ]

    previous_state = load_previous_state()

    # FIRST RUN
    if previous_state is None:
        print("No previous state found (first run).")
        save_state(current_state)

        if interesting:
            print("Initial availability found; sending full list.")
            send_availability_list_message(start_date, interesting)
        else:
            print("Initial run: no availability; no notification sent.")
        return

    # SUBSEQUENT RUNS
    if current_state == previous_state:
        print("Availability unchanged; no notification sent.")
        return

    print("Availability has changed since last run.")

    if interesting:
        send_availability_list_message(start_date, interesting)
    else:
        send_no_availability_message(start_date)

    save_state(current_state)


if __name__ == "__main__":
    check_overland()
