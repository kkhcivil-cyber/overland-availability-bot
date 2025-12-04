import os
import re
import json
from pathlib import Path
from datetime import datetime, date

import requests
from bs4 import BeautifulSoup

# The Overland Track availability page
URL = "https://azapps.customlinc.com.au/tasparksoverland/BookingCat/Availability/?Category=OVERLAND"

# Date window you care about:
#   - START_DATE is "today" (the day the script runs)
#   - END_DATE is fixed: 31 May 2026
END_DATE = date(2026, 5, 31)

# File used to remember the last availability list
STATE_FILE = Path("state.json")


def get_start_date() -> date:
    """
    Return today's date. This will be 'today' in the GitHub server's timezone.
    Good enough for our purpose.
    """
    return date.today()


def parse_availability(lines):
    """
    Parse lines of text from the page into a list of
    (date, status, spots) tuples.

    Example pattern on the page:
      Saturday 6 Dec 2025 025
      Available
      1 Available

      Monday 8 Dec 2025 025
      Fully Booked
    """
    date_pattern = re.compile(
        r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+"
        r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})"
    )

    results = []

    for i, line in enumerate(lines):
        m = date_pattern.search(line)
        if not m:
            continue

        weekday, day, mon_abbr, year = m.groups()
        date_str = f"{day} {mon_abbr} {year}"
        try:
            dt = datetime.strptime(date_str, "%d %b %Y").date()
        except ValueError:
            # If parsing fails for some reason, skip this line
            continue

        status = None
        spots = None

        # Look ahead a few lines for status and number of spots
        for j in range(1, 5):
            if i + j >= len(lines):
                break
            t = lines[i + j]

            if status is None:
                if "Fully Booked" in t:
                    status = "Fully Booked"
                elif "Available" in t:
                    # This may be "Available" or "1 Available"
                    status = t

            if "Available" in t:
                m2 = re.search(r"(\d+)", t)
                if m2:
                    spots = int(m2.group(1))

        results.append((dt, status, spots))

    return results


def load_previous_state():
    """
    Load the last known availability from state.json.

    Returns:
      - None if this is the first run / file missing / unreadable
      - a list of {"date": "YYYY-MM-DD", "spots": int} otherwise
    """
    if not STATE_FILE.exists():
        return None

    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("availabilities", [])
    except Exception:
        # If anything goes wrong, treat as no previous state
        return None


def save_state(avail_list):
    """
    Save the current availability list to state.json.
    avail_list is a list of {"date": "YYYY-MM-DD", "spots": int}
    """
    data = {"availabilities": avail_list}
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def send_telegram_message(text: str):
    """
    Send a raw text message to Telegram using the bot.
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
    'interesting' is a list of (dt, status, spots).
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
            lines.append(f"- {date_str}: {status}")

    text = "\n".join(lines)
    send_telegram_message(text)


def send_no_availability_message(start_date: date):
    """
    Send a message saying there is no availability in the window
    (used when previously there WAS availability).
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

    # Turn into clean, non-empty lines
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    all_days = parse_availability(lines)

    # Filter by date range and availability
    interesting = []
    for dt, status, spots in all_days:
        if not (start_date <= dt <= END_DATE):
            continue

        # We treat any date with spots > 0 as available
        if spots is not None and spots > 0:
            interesting.append((dt, status, spots))

    # Build a simplified representation of the current availability
    current_state = [
        {"date": dt.isoformat(), "spots": spots}
        for dt, status, spots in sorted(interesting, key=lambda x: x[0])
    ]

    previous_state = load_previous_state()

    # FIRST RUN: no previous state
    if previous_state is None:
        print("No previous state found (first run).")
        # Save state
        save_state(current_state)

        # Only send a message if there is something available
        if interesting:
            print("Initial availability found; sending full list.")
            send_availability_list_message(start_date, interesting)
        else:
            print("Initial run: no availability; no notification sent.")
        return

    # SUBSEQUENT RUNS: compare current vs previous
    if current_state == previous_state:
        print("Availability unchanged; no notification sent.")
        return

    # At this point, the availability list HAS changed
    print("Availability has changed since last run.")

    if interesting:
        # There is at least one available date now
        send_availability_list_message(start_date, interesting)
    else:
        # Previously there was availability, now there is none
        send_no_availability_message(start_date)

    # After successfully sending the message, update state.json
    save_state(current_state)


if __name__ == "__main__":
    check_overland()
