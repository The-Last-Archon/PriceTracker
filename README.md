# PriceTracker

Simple Python script to fetch and track product prices.

Usage:

1. Create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Run the tracker once:

```powershell
python track_price.py "https://superette.co.nz/products/malbon-veronica-polo-black-onyxcream-mlbmw0228"
```

3. Optional: set `PRICE_TRACKER_WEBHOOK` environment variable to receive JSON POSTs when a price changes.

Data is stored in `price_data.json` in the working directory.

Notifications
-
- Webhook: set `PRICE_TRACKER_WEBHOOK` to an HTTP endpoint to receive JSON POSTs on price change.
 - Webhook: set `PRICE_TRACKER_WEBHOOK` to an HTTP endpoint to receive alerts when a price *drops*.
	 - Discord: provide a Discord incoming webhook URL (e.g. `https://discord.com/api/webhooks/...`). The tracker will send a readable message to the channel when a tracked price decreases.
	 - Other endpoints: the tracker posts a JSON payload `{"url","old_price","new_price","timestamp"}`.
- Email (SMTP): set the following environment variables to enable email notifications:
	- `PRICE_TRACKER_SMTP_HOST` — SMTP host
	- `PRICE_TRACKER_SMTP_PORT` — SMTP port (default 587)
	- `PRICE_TRACKER_SMTP_USER` — SMTP username (optional)
	- `PRICE_TRACKER_SMTP_PASS` — SMTP password (optional)
	- `PRICE_TRACKER_SMTP_FROM` — From address (optional)
	- `PRICE_TRACKER_NOTIFY_EMAIL_TO` — Comma-separated recipient addresses

Scheduling
-
- Windows Task Scheduler (PowerShell example): create a daily task to run the script:

```powershell
$action = New-ScheduledTaskAction -Execute 'python' -Argument 'D:\"Github Projects"\PriceTracker\track_price.py "https://superette.co.nz/products/malbon-veronica-polo-black-onyxcream-mlbmw0228"'
$trigger = New-ScheduledTaskTrigger -Daily -At 9am
Register-ScheduledTask -Action $action -Trigger $trigger -TaskName "PriceTracker_Superette"
```

- cron (Linux/macOS) example (run every 6 hours):

```cron
0 */6 * * * /path/to/python /home/user/PriceTracker/track_price.py "https://superette.co.nz/products/malbon-veronica-polo-black-onyxcream-mlbmw0228"
```

Tracking specific sizes
-
- If the product page shows different prices per size, prefer using a CSS selector that targets the price element for that size:

```powershell
python track_price.py "<product-url>" --selector ".variant-size.M .price"
```

- Or provide the size label and the script will try to find the size on the page and read the nearby price:

```powershell
python track_price.py "<product-url>" --size "M"
```

Notes: when using `--selector` or `--size` the tracker stores prices separately per selector/size so different variants don't overwrite each other.

Variant filters
-
- Use `--selector` to target an exact price element, `--size` for the size label, or `--variant` for arbitrary variant filters (case-insensitive).

- Pass `--variant` one or more times using the format `Name=Value`. Matching is case-insensitive for labels and values. Examples:

```powershell
python track_price.py "<product-url>" --variant "Size=M"
python track_price.py "<product-url>" --variant "Color=Black" --variant "Size=M"
```

The tracker will search for the provided variant text on the page and then try to read a nearby price. Stored keys include the variant pairs so different variants are tracked separately.

