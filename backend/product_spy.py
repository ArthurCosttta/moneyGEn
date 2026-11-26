from __future__ import annotations

import html as html_lib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def parse_compact_number(raw: Any) -> int:
    """Parse counts like 12500, '10K+', '1,2 mil', '1.5 mi', '1K+ vendido(s)'."""
    if raw is None or isinstance(raw, bool):
        return 0
    if isinstance(raw, (int, float)):
        return max(0, int(raw))
    text = str(raw).strip().lower()
    if not text:
        return 0
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(mil\b|mi(?:lh(?:[õo]es|[ãa]o))?\b|m\b|k\b|b\b)?", text)
    if not m:
        digits = re.sub(r"\D+", "", text)
        return int(digits) if digits else 0
    num_str = m.group(1)
    suffix = (m.group(2) or "").lower()
    if suffix:
        num_val = float(num_str.replace(",", "."))
    else:
        # Handle Brazilian thousand separators like "10.000" vs "10,5"
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", num_str):
            num_val = float(num_str.replace(".", ""))
        elif "," in num_str and "." not in num_str:
            num_val = float(num_str.replace(",", "."))
        else:
            num_val = float(num_str)
    mult = 1
    if suffix in ("k", "mil"):
        mult = 1_000
    elif suffix in ("m",) or suffix.startswith("mi"):
        mult = 1_000_000
    elif suffix == "b":
        mult = 1_000_000_000
    return max(0, int(round(num_val * mult)))


def parse_price_brl(raw: Any) -> float | None:
    if isinstance(raw, (int, float)):
        return round(float(raw), 2)
    text = str(raw or "")
    m = re.search(r"(\d{1,3}(?:[.\s]\d{3})*|\d+)(?:,(\d{1,2}))?", text)
    if not m:
        return None
    whole = re.sub(r"\D+", "", m.group(1))
    cents = m.group(2) or "0"
    if not whole:
        return None
    return round(float(f"{whole}.{cents.ljust(2, '0')[:2]}"), 2)


def extract_page_copy_summary(html_text: str, max_chars: int = 5000) -> str:
    """Strip scripts/styles/tags from cloned HTML and extract clean commercial copy for GPT."""
    if not html_text:
        return ""
    cleaned = re.sub(r"<(script|style|noscript|svg)[^>]*>[\s\S]*?</\1>", " ", html_text, flags=re.IGNORECASE)
    cleaned = re.sub(r"<!--[\s\S]*?-->", " ", cleaned)
    # Preserve line boundaries around headings and list items
    cleaned = re.sub(r"</?(?:h[1-6]|p|li|section|article|div|br|tr)[^>]*>", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html_lib.unescape(cleaned)
    lines = []
    seen = set()
    for line in cleaned.splitlines():
        norm = re.sub(r"\s+", " ", line).strip()
        if len(norm) < 3:
            continue
        low = norm.casefold()
        if low in seen:
            continue
        seen.add(low)
        lines.append(norm)
    return "\n".join(lines)[:max_chars]


def parse_aliexpress_html(html_text: str, *, limit: int = 20) -> list[dict[str, Any]]:
    needle = '"itemList":'
    idx = html_text.find(needle)
    if idx == -1:
        return []
    try:
        obj, _ = json.JSONDecoder().raw_decode(html_text[idx + len(needle) :])
    except ValueError:
        return []
    rows = obj.get("content") if isinstance(obj, dict) else []
    if not isinstance(rows, list):
        return []
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("productId") or row.get("redirectedId") or "").strip()
        title = str(((row.get("title") or {}).get("displayTitle")) or "").strip()
        if not pid or not title:
            continue
        sale = (row.get("prices") or {}).get("salePrice") or {}
        price_str = str(sale.get("formattedPrice") or "").strip()
        min_price = sale.get("minPrice")
        discount = sale.get("discount")
        trade_desc = str(((row.get("trade") or {}).get("tradeDesc")) or "").strip()
        sold_count = parse_compact_number(trade_desc)
        rating = ((row.get("evaluation") or {}).get("starRating"))
        img = str(((row.get("image") or {}).get("imgUrl")) or "").strip()
        if img.startswith("//"):
            img = "https:" + img
        store = ((row.get("store") or {}).get("storeName")) or "AliExpress"
        url = f"https://www.aliexpress.com/item/{pid}.html"
        price_val = float(min_price) if isinstance(min_price, (int, float)) else parse_price_brl(price_str)
        est_rev_min = round(price_val * sold_count, 2) if (price_val and sold_count) else None
        items.append({
            "id": f"aliexpress:{pid}",
            "source": "aliexpress",
            "source_label": "AliExpress",
            "media_type": "product",
            "title": title[:160],
            "text": " · ".join(p for p in (price_str, trade_desc, f"★ {rating}" if rating else "", f"-{discount}%" if discount else "") if p),
            "author": str(store),
            "url": url,
            "landing_url": url,
            "domain": "aliexpress.com",
            "thumbnail": img,
            "price_text": price_str,
            "price_value": price_val,
            "sold_text": trade_desc,
            "sold_count": sold_count,
            "est_revenue_min": est_rev_min,
            "rating": float(rating) if isinstance(rating, (int, float)) else None,
            "likes": sold_count,
            "views": sold_count * 12 if sold_count else 0,
            "active_ads": None,
            "published_at": "",
        })
        if len(items) >= limit:
            break
    return items


def search_aliexpress(query: str, *, limit: int = 100) -> dict[str, Any]:
    base_url = (
        "https://www.aliexpress.com/wholesale?SearchText="
        + urllib.parse.quote_plus(query)
        + "&SortType=total_tranpro_desc"
    )
    pages = [1, 2] if limit > 55 else [1]
    all_items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    last_err = None
    for page_num in pages:
        page_url = base_url if page_num == 1 else f"{base_url}&page={page_num}"
        try:
            req = urllib.request.Request(
                page_url,
                headers={"User-Agent": _UA, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
            )
            with urllib.request.urlopen(req, timeout=16) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            chunk = parse_aliexpress_html(raw, limit=max(60, limit))
            for it in chunk:
                if it["id"] not in seen_ids:
                    seen_ids.add(it["id"])
                    all_items.append(it)
                    if len(all_items) >= limit:
                        break
        except Exception as exc:
            last_err = f"AliExpress indisponível: {str(exc)[:160]}"
            break
        if len(all_items) >= limit:
            break
    return {
        "items": all_items[:limit],
        "error": last_err if not all_items else None,
        "search_url": base_url,
    }


def parse_shopify_products_json(payload: Any, store_url: str, *, query: str = "", limit: int = 18) -> list[dict[str, Any]]:
    products = payload.get("products") if isinstance(payload, dict) else []
    if not isinstance(products, list):
        return []
    parsed = urllib.parse.urlsplit(store_url)
    origin = f"{parsed.scheme or 'https'}://{parsed.netloc}"
    domain = parsed.netloc.lower().removeprefix("www.")
    q_low = (query or "").strip().lower()
    items: list[dict[str, Any]] = []
    for prod in products:
        if not isinstance(prod, dict):
            continue
        title = str(prod.get("title") or "").strip()
        handle = str(prod.get("handle") or "").strip()
        if not title or not handle:
            continue
        if q_low and q_low not in title.lower() and q_low not in str(prod.get("product_type") or "").lower():
            # Keep fallback items if few match strictly
            pass
        variants = prod.get("variants") if isinstance(prod.get("variants"), list) else []
        first_var = variants[0] if variants and isinstance(variants[0], dict) else {}
        price_str = str(first_var.get("price") or "").strip()
        images = prod.get("images") if isinstance(prod.get("images"), list) else []
        img = str((images[0] or {}).get("src") or "") if images and isinstance(images[0], dict) else ""
        prod_url = f"{origin}/products/{handle}"
        items.append({
            "id": f"shopify:{domain}:{handle}",
            "source": "shopify",
            "source_label": "Shopify",
            "media_type": "product",
            "title": title[:160],
            "text": f"Preço: {price_str} · Loja {domain}" if price_str else f"Produto Shopify · {domain}",
            "author": str(prod.get("vendor") or domain),
            "url": prod_url,
            "landing_url": prod_url,
            "domain": domain,
            "thumbnail": img,
            "price_text": price_str,
            "price_value": parse_price_brl(price_str),
            "sold_text": "",
            "sold_count": 0,
            "likes": None,
            "views": None,
            "active_ads": None,
            "published_at": str(prod.get("updated_at") or "")[:10],
        })
        if len(items) >= limit:
            break
    return items


def fetch_shopify_store_products(store_url: str, *, query: str = "", limit: int = 18) -> list[dict[str, Any]]:
    raw = (store_url or "").strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parts = urllib.parse.urlsplit(raw)
    if not parts.netloc:
        return []
    endpoint = f"{parts.scheme}://{parts.netloc}/products.json?limit={min(limit, 30)}"
    try:
        req = urllib.request.Request(endpoint, headers={"User-Agent": _UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return parse_shopify_products_json(data, raw, query=query, limit=limit)
    except Exception:
        return []
