import os
import re
from datetime import datetime, date

import requests
from bs4 import BeautifulSoup

# The Overland Track availability page
URL = "https://azapps.customlinc.com.au/tasparksoverland/BookingCat/Availability/?Category=OVERLAND"

# Date window you care about
START_DATE = date(2026, 1, 1)   # 1 Jan 2026
END_DATE = date(2026, 4, 15)    # 15 Apr 2026


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


def send_telegram_alert(items):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    lines = [
        "🚨 Overland Track availability found!",
        f"Window: {START_DATE.strftime('%d %b %Y')} – {END_DATE.strftime('%d %b %Y')}",
        "",
    ]

    for dt, status, spots in items:
        date_str = dt.strftime("%A %d %b %Y")
        if spots is not None:
            lines.append(f"- {date_str}: {spots} spots ({status})")
        else:
            lines.append(f"- {date_str}: {status}")

    text = "\n".join(lines)

    resp = requests.post(url, data={"chat_id": chat_id, "text": text})
    resp.raise_for_status()
    print("Notification sent.")


def check_overland():
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
        if not (START_DATE <= dt <= END_DATE):
            continue

        # We treat any date with spots > 0 as available
        if spots is not None and spots > 0:
            interesting.append((dt, status, spots))

    if not interesting:
        print("No availability in desired window.")
        return

    send_telegram_alert(interesting)


if __name__ == "__main__":
    check_overland()
