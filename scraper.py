import os
import re
import json
from pathlib import Path
from datetime import datetime, date

import requests
from bs4 import BeautifulSoup

# Base URLs
BASE_URL = "https://azapps.customlinc.com.au/tasparksoverland/BookingCat/Availability/"
CATEGORY_URL = BASE_URL + "?Category=OVERLAND"

# We care about dates from "today" up to this fixed end date
END_DATE = date(2026, 5, 31)

# File used to remember the last availability list
STATE_FILE = Path("state.json")


def get_start_date() -> date:
    """
    Start date is 'today' on the GitHub runner.
    """
    return date.today()


# Pattern for lines like "Saturday 6 Dec 2025"
DATE_PATTERN = re.compile(
    r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+"
    r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})"
)


def parse_availability_from_html(html_text):
    """
    Parse a single HTML page into a list of (date, status, spots) tuples.
    We treat each "date line" as the start of a block until the next date line.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    results = []

    # Find all lines that look like dates
    date_indices = []
    for i, line in enumerate(lines):
        if DATE_PATTERN.search(line):
            date_indices.append(i)

    for idx, start_idx in enumerate(date_indices):
        end_idx = date_indices[idx + 1] if idx + 1 < len(date_indices) else len(lines)
        block = lines[start_idx:end_idx]

        m = DATE_PATTERN.search(block[0])
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

        # Look for status and "X Available" inside this block
        for line in block[1:]:
            if "Fully Booked" in line:
                status = "Fully Booked"
            elif "Available" in line and status is None:
                # If not already set to "Fully Booked", mark as "Available"
                status = "Available"

            if "Available" in line:
                m2 = re.search(r"(\d+)\s+Available", line)
                if m2:
                    spots = int(m2.group(1))

        results.append((dt, status, spots))

    return results


def fetch_all_days():
    """
    Simulate:
      1) Opening the Overland availability page
      2) Clicking "Next days" repeatedly (via ?changeDate&localtime=...)
    Collect all unique dates that appear until we reach/past END_DATE
    or run out of new dates.
    """
    session = requests.Session()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; OverlandChecker/1.0; "
            "+https://github.com/yourname/overland-availability-bot)"
        )
    }

    all_by_date = {}

    # 1) Initial page with ?Category=OVERLAND
    resp = session.get(CATEGORY_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    page_results = parse_availability_from_html(resp.text)
    print(f"Initial page has {len(page_results)} date rows.")

    for dt, status, spots in page_results:
        all_by_date[dt] = (status, spots)

    max_date_seen = max(all_by_date.keys()) if all_by_date else None

    # 2) Page forward repeatedly using changeDate
    for page_num in range(1, 80):  # safety limit on number of pages
        # Format similar to what the browser sends: "YYYY-MM-DD, HH:MM:SS:000"
        localtime_str = datetime.utcnow().strftime("%Y-%m-%d, %H:%M:%S:000")
        params = {"changeDate": "", "localtime": localtime_str}

        resp = session.get(BASE_URL, headers=headers, params=params, timeout=30)
        resp.raise_for_status()

        page_results = parse_availability_from_html(resp.text)
        print(f"Page {page_num + 1} has {len(page_results)} date rows.")

        if not page_results:
            print("No dates on this page; stopping pagination.")
            break

        new_dates = False
        for dt, status, spots in page_results:
            if dt not in all_by_date:
                new_dates = True
            all_by_date[dt] = (status, spots)

        current_max = max(all_by_date.keys())
        print(f"Current max date seen: {current_max}")

        # Stop if we've reached or passed END_DATE
        if current_max >= END_DATE:
            print("Reached or passed END_DATE; stopping pagination.")
            break

        # Stop if the max date hasn't moved forward (protect against loops)
        if max_date_seen is not None and current_max <= max_date_seen:
            print("No progress in max date; stopping pagination.")
            break

        max_date_seen = current_max

        # Stop if this page didn't add any new dates
        if not new_dates:
            print("No new dates found on this page; stopping pagination.")
            break

    # Turn dict back into a sorted list of tuples
    all_days = [(dt, status, spots) for dt, (status, spots) in all_by_date.items()]
    all_days.sort(key=lambda x: x[0])

    print(f"Total unique dates collected: {len(all_days)}")
    return all_days


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

    # Fetch ALL dates by simulating "Next days" clicks
    all_days = fetch_all_days()
    print(f"Total dates retrieved from site: {len(all_days)}")

    # Filter down to our window and "available" status
    interesting = []
    for dt, status, spots in all_days:
        if not (start_date <= dt <= END_DATE):
            continue

        # Treat as available if it says Available and spots >= 1 (or unknown)
        if status and "Available" in status and (spots is None or spots > 0):
            interesting.append((dt, status, spots))

    print(f"Found {len(interesting)} available dates in desired window.")

    # Build simple state representation (date + spots)
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
