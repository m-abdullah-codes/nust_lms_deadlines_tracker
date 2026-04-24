# NUST LMS Deadlines Tracker

A lightweight desktop app that helps students track LMS deadlines quickly, without repeatedly opening the browser and logging in every time.

## Why this project

Students often miss assignment updates because checking LMS manually takes time.  
This app keeps your upcoming deadlines in one place with a tray-based desktop view so you can check tasks instantly.

## Features

- Login once and keep credentials/token locally on your machine
- Fetch and display LMS action events (sorted by due time)
- Group deadlines by date with clear status badges:
  - Overdue
  - Today
  - Tomorrow
  - Upcoming
- Fast manual refresh
- Optional notifications for next-day deadlines
- System tray integration (show/hide, refresh, startup toggle, quit)
- Optional start with Windows

## Tech stack

- Python
- `customtkinter` for modern UI
- `requests` for LMS API calls
- `pystray` + `Pillow` for tray icon
- `plyer` for desktop notifications

## Project files

- `nust_deadlines.py` - Main desktop application
- `nust_config.json` - Local user config/cache (ignored in Git)

## Setup

1. Install Python 3.10+ (Windows recommended for full feature support).
2. Install dependencies:

```bash
pip install customtkinter requests pystray pillow plyer urllib3
```

3. Run the app:

```bash
python nust_deadlines.py
```

## Notes

- This project is intended for educational/personal use.
- Your local config file may contain private account details and tokens, so it should not be committed to GitHub.

# nust_lms_deadlines_tracker
