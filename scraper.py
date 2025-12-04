import os
import re
from datetime import datetime, date

import requests
from bs4 import BeautifulSoup

URL = "https://azapps.customlinc.com.au/tasparksoverland/BookingCat/Availability/?Category=OVERLAND"

# We keep your window: from today to 31 May 2026
END_DATE = date(2026, 5, 31)


def get_start_date() -> date:
    return date.today()


DATE_PATTERN = re.compile(
    r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+"
    r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})"
)


def parse_availability_from_html(html_text):
    """
    VERY simple parser: just report everything it sees on the page.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    results = []

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

        for line in block[1:]:
            if "Fully Booked" in line:
                status = "Fully Booked"
            elif "Available" in line and status is None:
                status = "Available"

            if "Available" in line:
                # Look for "X Available"
                m2 = re.search(r"(\d+)\s+Available", line)
                if m2:
                    spots = int(m2.group(1))

        results.append((dt, status, spots))

    return results


def send_telegram_message(text: str):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, data={"chat_id": chat_id, "text": text})
    print("Telegram status code:", resp.status_code)
    print("Telegram response:", resp.text)
    resp.raise_for_status()
    print("Telegram notification sent.")


def check_overland():
    start_date = get_start_date()
    print(f"Checking availability page: {URL}")
    print(f"Window: {start_date} to {END_DATE}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; OverlandChecker/1.0; "
            "+https://github.com/yourname/overland-availability-bot)"
        )
    }

    resp = requests.get(URL, headers=headers, timeout=30)
    resp.raise_for_status()

    all_days = parse_availability_from_html(resp.text)
    print(f"Found {len(all_days)} date entries on the page.")

    # Build a debug message for Telegram
    lines = [
        "🔍 Debug run: Overland availability (FIRST PAGE ONLY)",
        f"Window: {start_date.strftime('%d %b %Y')} – {END_DATE.strftime('%d %b %Y')}",
        "",
        f"Total dates on first page: {len(all_days)}",
        "",
        "Sample:",
    ]

    # Take up to first 10 items to keep message small
    for dt, status, spots in all_days[:10]:
        date_str = dt.strftime("%A %d %b %Y")
        lines.append(f"- {date_str}: status={status}, spots={spots}")

    # Also show how many entries in the window
    in_window = [
        (dt, status, spots)
        for dt, status, spots in all_days
        if start_date <= dt <= END_DATE
    ]
    lines.append("")
    lines.append(f"In window (today–31 May 2026): {len(in_window)} dates")

    text = "\n".join(lines)
    send_telegram_message(text)


if __name__ == "__main__":
    check_overland()
