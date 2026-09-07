import argparse
import json
import os
import re
import sys
from datetime import datetime
from urllib.parse import urlparse, parse_qs
import smtplib
from email.message import EmailMessage

import requests
from bs4 import BeautifulSoup


def get_html(url, headers=None, timeout=15):
    headers = headers or {"User-Agent": "Mozilla/5.0 (compatible; PriceTracker/1.0)"}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def get_handle_and_base(url):
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    # path like /products/<handle>
    parts = p.path.strip("/").split("/")
    handle = None
    if len(parts) >= 2 and parts[0] == "products":
        handle = parts[1]
    elif len(parts) >= 1:
        # try last part
        handle = parts[-1]
    return handle, base


def fetch_product_json(handle, base_url):
    # Try Shopify-style endpoints
    urls = [f"{base_url}/products/{handle}.js", f"{base_url}/products/{handle}.json"]
    for u in urls:
        try:
            r = requests.get(u, timeout=10, headers={"User-Agent": "Mozilla/5.0 (compatible; PriceTracker/1.0)"})
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    # some .js endpoints return raw JS; attempt to extract JSON
                    txt = r.text
                    try:
                        return json.loads(txt)
                    except Exception:
                        continue
        except Exception:
            continue
    return None


def resolve_variant_from_product_json(product_json, variant_id):
    if not product_json:
        return None
    variants = product_json.get("variants") or product_json.get("product", {}).get("variants") if isinstance(product_json, dict) else None
    if not variants:
        return None
    for v in variants:
        try:
            vid = int(v.get("id") or v.get("variant_id") or 0)
        except Exception:
            continue
        if str(vid) == str(variant_id):
            return v
    return None


def find_variant_by_filters(product_json, filters):
    if not product_json:
        return None
    variants = product_json.get("variants") or product_json.get("product", {}).get("variants") if isinstance(product_json, dict) else None
    if not variants:
        return None
    for v in variants:
        ok = True
        for k, val in filters.items():
            val_s = str(val).strip().lower()
            # check title and option fields
            title = str(v.get("title", "") or "").strip().lower()
            opt1 = str(v.get("option1", "") or "").strip().lower()
            opt2 = str(v.get("option2", "") or "").strip().lower()
            opt3 = str(v.get("option3", "") or "").strip().lower()
            if val_s == "":
                continue
            if val_s not in title and val_s != opt1 and val_s != opt2 and val_s != opt3:
                ok = False
                break
        if ok:
            return v
    return None


def parse_price_from_ldjson(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        if isinstance(data, list):
            items = data
        else:
            items = [data]
        for item in items:
            offers = item.get("offers") if isinstance(item, dict) else None
            if offers:
                price = offers.get("price") or (offers[0].get("price") if isinstance(offers, list) and offers else None)
                if price:
                    return price
    return None


def parse_price_from_meta(soup):
    # common meta tags
    meta = soup.find("meta", attrs={"property": "product:price:amount"}) or soup.find("meta", attrs={"itemprop": "price"})
    if meta and meta.get("content"):
        return meta.get("content")
    return None


def parse_price_by_class(soup):
    # look for elements with 'price' in class or id
    candidates = []
    for tag in soup.find_all(True):
        attr = " ".join([str(x) for x in (tag.get("class") or [])]) + " " + str(tag.get("id") or "")
        if "price" in attr.lower():
            text = tag.get_text(strip=True)
            if text:
                candidates.append(text)
    return candidates[0] if candidates else None


def extract_price(html, selector=None, size=None, variants=None):
    soup = BeautifulSoup(html, "lxml")

    # If a CSS selector is provided, try it first (precise targeting)
    if selector:
        el = soup.select_one(selector)
        if el:
            val = normalize_price_str(el.get_text())
            if val is not None:
                return val

    # If variant filters are provided (e.g., size or color), try to find price near that label
    if variants or size:
        # build list of (label, value) pairs to search for; size is kept for backward compatibility
        filters = []
        if variants:
            for k, v in variants.items():
                filters.append((k, v))
        if size:
            filters.append(("size", size))

        # For each filter value, search for text nodes that mention the value (case-insensitive), then locate nearby price
        for label, val in filters:
            val_re = re.compile(re.escape(str(val)), re.I)
            for text_node in soup.find_all(string=val_re):
                container = text_node.parent
                # search container and nearby ancestors/descendants for price-like elements
                candidates = []
                # check container
                candidates.append(container)
                # check siblings
                candidates.extend(list(container.next_siblings)[:3])
                candidates.extend(list(container.previous_siblings)[:3])
                # check ancestors
                ancestor = container
                for _ in range(3):
                    if ancestor is None:
                        break
                    ancestor = ancestor.parent
                    if ancestor:
                        candidates.append(ancestor)

                for node in candidates:
                    try:
                        # look for price-like element inside node
                        if hasattr(node, "find_all"):
                            # by class/id
                            price_text = parse_price_by_class(node)
                            if price_text:
                                val2 = normalize_price_str(price_text)
                                if val2 is not None:
                                    return val2
                        # by searching text
                        pt = None
                        for t in node.stripped_strings:
                            m = re.search(r"(NZ\$|\$)\s?([0-9\.,]+)", t)
                            if m:
                                pt = m.group(2)
                                break
                        if pt:
                            val2 = normalize_price_str(pt)
                            if val2 is not None:
                                return val2
                    except Exception:
                        continue

    # try ld+json
    price = parse_price_from_ldjson(soup)
    if price:
        val = normalize_price_str(price)
        if val is not None:
            return val

    # try meta
    price = parse_price_from_meta(soup)
    if price:
        val = normalize_price_str(price)
        if val is not None:
            return val

    # try common classes
    price = parse_price_by_class(soup)
    if price:
        val = normalize_price_str(price)
        if val is not None:
            return val

    # fallback regex on raw html
    price = parse_price_by_regex(html)
    if price:
        val = normalize_price_str(price)
        if val is not None:
            return val

    return None


def parse_price_by_regex(html):
    # fallback: search for NZ$ or $ amounts
    # matches like NZ$ 129.00 or $129
    m = re.search(r"(NZ\$|\$)\s?([0-9\.,]+)", html)
    if m:
        return m.group(2)
    return None


def normalize_price_str(s):
    if s is None:
        return None
    # remove currency words and whitespace
    s = re.sub(r"[A-Za-z$NZ\s]", "", str(s))
    s = s.replace(",", "")
    s = s.strip()
    try:
        return float(s)
    except Exception:
        return None


def extract_price(html, selector=None, size=None, variants=None):
    soup = BeautifulSoup(html, "lxml")

    # If a CSS selector is provided, try it first (precise targeting)
    if selector:
        el = soup.select_one(selector)
        if el:
            val = normalize_price_str(el.get_text())
            if val is not None:
                return val

    # If variant filters are provided (e.g., size or color), try to find price near that label
    if variants or size:
        # build list of (label, value) pairs to search for; size is kept for backward compatibility
        filters = []
        if variants:
            for k, v in variants.items():
                filters.append((k, v))
        if size:
            filters.append(("size", size))

        # For each filter value, search for text nodes that mention the value (case-insensitive), then locate nearby price
        for label, val in filters:
            val_re = re.compile(re.escape(str(val)), re.I)
            for text_node in soup.find_all(string=val_re):
                container = text_node.parent
                # search container and nearby ancestors/descendants for price-like elements
                candidates = []
                # check container
                candidates.append(container)
                # check siblings
                candidates.extend(list(container.next_siblings)[:3])
                candidates.extend(list(container.previous_siblings)[:3])
                # check ancestors
                ancestor = container
                for _ in range(3):
                    if ancestor is None:
                        break
                    ancestor = ancestor.parent
                    if ancestor:
                        candidates.append(ancestor)

                for node in candidates:
                    try:
                        # look for price-like element inside node
                        if hasattr(node, "find_all"):
                            # by class/id
                            price_text = parse_price_by_class(node)
                            if price_text:
                                val2 = normalize_price_str(price_text)
                                if val2 is not None:
                                    return val2
                        # by searching text
                        pt = None
                        for t in node.stripped_strings:
                            m = re.search(r"(NZ\$|\$)\s?([0-9\.,]+)", t)
                            if m:
                                pt = m.group(2)
                                break
                        if pt:
                            val2 = normalize_price_str(pt)
                            if val2 is not None:
                                return val2
                    except Exception:
                        continue

    # try ld+json
    price = parse_price_from_ldjson(soup)
    if price:
        val = normalize_price_str(price)
        if val is not None:
            return val

    # try meta
    price = parse_price_from_meta(soup)
    if price:
        val = normalize_price_str(price)
        if val is not None:
            return val

    # try common classes
    price = parse_price_by_class(soup)
    if price:
        val = normalize_price_str(price)
        if val is not None:
            return val

    # fallback regex on raw html
    price = parse_price_by_regex(html)
    if price:
        val = normalize_price_str(price)
        if val is not None:
            return val

    return None


DATA_FILE = "price_data.json"


def load_last(key):
    if not os.path.exists(DATA_FILE):
        return None
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get(key)
    except Exception:
        return None


def save_price(key, price):
    data = {}
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data[key] = {"price": price, "timestamp": datetime.utcnow().isoformat()}
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def notify_change(url, old, new, drop=False):
    """Notify about a price change. Only send webhook alerts for drops (drop=True).

    - Discord webhooks are formatted for human-readable messages.
    - Other webhook endpoints receive a JSON body with url/old_price/new_price/timestamp.
    """
    webhook = os.getenv("PRICE_TRACKER_WEBHOOK")
    timestamp = datetime.utcnow().isoformat()
    msg = {"url": url, "old_price": old, "new_price": new, "timestamp": timestamp}
    print("Price change:", msg)

    if webhook and drop:
        try:
            # Discord webhook expects a `content` or `embeds` field
            if "discord.com/api/webhooks" in webhook:
                content = f"📉 **Price drop**\n{url}\n{old} → {new}\n{timestamp}"
                payload = {"content": content}
                requests.post(webhook, json=payload, timeout=10)
            else:
                requests.post(webhook, json=msg, timeout=10)
        except Exception as e:
            print("Failed to POST webhook:", e)

    # Optional: send email if SMTP env vars provided or NOTIFY_EMAIL_TO is set (still sent for any change)
    notify_to = os.getenv("PRICE_TRACKER_NOTIFY_EMAIL_TO") or os.getenv("NOTIFY_EMAIL_TO")
    smtp_host = os.getenv("PRICE_TRACKER_SMTP_HOST")
    if notify_to and smtp_host:
        try:
            send_email_notification(notify_to, url, old, new)
        except Exception as e:
            print("Failed to send email notification:", e)


def send_email_notification(to_addrs, url, old, new):
    smtp_host = os.getenv("PRICE_TRACKER_SMTP_HOST")
    smtp_port = int(os.getenv("PRICE_TRACKER_SMTP_PORT", "587"))
    smtp_user = os.getenv("PRICE_TRACKER_SMTP_USER")
    smtp_pass = os.getenv("PRICE_TRACKER_SMTP_PASS")
    from_addr = os.getenv("PRICE_TRACKER_SMTP_FROM") or smtp_user or "price-tracker@example.com"

    subject = f"Price changed: {old} → {new}"
    body = json.dumps({"url": url, "old_price": old, "new_price": new, "timestamp": datetime.utcnow().isoformat()}, indent=2)

    email = EmailMessage()
    email["From"] = from_addr
    email["To"] = to_addrs
    email["Subject"] = subject
    email.set_content(body)

    # Try TLS then fallback to plain
    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as s:
        s.ehlo()
        if smtp_port == 587:
            s.starttls()
            s.ehlo()
        if smtp_user and smtp_pass:
            s.login(smtp_user, smtp_pass)
        s.send_message(email)


def main():
    parser = argparse.ArgumentParser(description="Simple price tracker for a product page")
    parser.add_argument("url", help="Product page URL to check")
    parser.add_argument("--selector", help="CSS selector targeting the price element for the desired size")
    parser.add_argument("--size", help="Text label for the size variant to target (e.g., M, L, 10)")
    parser.add_argument("--variant", action="append", help="Variant filter in the form Name=Value (case-insensitive). Repeatable.")
    args = parser.parse_args()

    # parse variant args into dict like {Name: Value}
    variants = None
    if args.variant:
        variants = {}
        for item in args.variant:
            if "=" in item:
                name, val = item.split("=", 1)
                variants[name.strip()] = val.strip()
            else:
                # single value - treat as unspecified name
                variants[item.strip()] = ""
    # If the URL contains a variant=ID param, try to resolve via product JSON (Shopify-style)
    p = urlparse(args.url)
    q = parse_qs(p.query)
    variant_id = q.get("variant", [None])[0]

    price_from_json = None
    resolved_variant = None
    if variant_id:
        handle, base = get_handle_and_base(args.url)
        if handle and base:
            pj = fetch_product_json(handle, base)
            resolved_variant = resolve_variant_from_product_json(pj, variant_id)
            if resolved_variant:
                raw_price = resolved_variant.get("price") or resolved_variant.get("price_in_cents") or resolved_variant.get("compare_at_price")
                try:
                    price_from_json = float(raw_price) / 100.0 if raw_price and float(raw_price) > 1000 else float(raw_price)
                except Exception:
                    price_from_json = None

    # If user requested a specific variant (by --variant or --size), try to resolve that via product JSON too
    if (variants or args.size) and not price_from_json:
        handle, base = get_handle_and_base(args.url)
        if handle and base:
            pj = fetch_product_json(handle, base)
            # build filters dict
            filters = {}
            if variants:
                filters.update(variants)
            if args.size:
                filters["size"] = args.size
            found = find_variant_by_filters(pj, filters)
            if found:
                resolved_variant = found
                raw_price = found.get("price")
                try:
                    price_from_json = float(raw_price) / 100.0 if raw_price and float(raw_price) > 1000 else float(raw_price)
                except Exception:
                    price_from_json = None

    # If we found a price via JSON and no selector/variant targeting needed, use it directly
    if price_from_json is not None and not args.selector and not args.size and not variants:
        price = price_from_json
    else:
        try:
            html = get_html(args.url)
        except Exception as e:
            print("Failed to fetch URL:", e)
            sys.exit(1)

        price = extract_price(html, selector=args.selector, size=args.size, variants=variants)
    if price is None:
        print("Could not find price on page")
        sys.exit(2)
    # Use a composite key when tracking specific selector/size
    key = args.url
    # compose storage key with selector/size/variants or resolved variant id to separate variants
    if args.selector:
        key = f"{args.url}::selector={args.selector}"
    elif args.size:
        key = f"{args.url}::size={args.size}"
    elif variants:
        pairs = [f"{k}={v}" for k, v in variants.items()]
        key = f"{args.url}::variant={','.join(pairs)}"
    elif variant_id:
        # include resolved title if available for readability
        title = resolved_variant.get("title") if resolved_variant else None
        if title:
            key = f"{args.url}::variant={variant_id}::title={title}"
        else:
            key = f"{args.url}::variant={variant_id}"

    last = load_last(key)
    last_price = last.get("price") if last else None

    if last_price is None:
        print(f"Current price: {price} (no previous record)")
        save_price(key, price)
        return

    if price != last_price:
        dropped = False
        try:
            dropped = float(price) < float(last_price)
        except Exception:
            dropped = False
        notify_change(args.url, last_price, price, drop=dropped)
        save_price(key, price)
    else:
        print(f"Price unchanged: {price}")


if __name__ == "__main__":
    main()
