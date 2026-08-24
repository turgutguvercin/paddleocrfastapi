from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE = "https://www.aa.com.tr"
CATEGORY = f"{BASE}/tr/enerjiterminali/elektrik"
OUT = Path("aa_load_output")
OUT.mkdir(exist_ok=True)

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.7",
})


def parse_tr_number(text: str) -> int | None:
    text = text.lower().strip().replace(".", " ").replace(",", " ")
    # Works for phrases such as "1 milyon 206 bin 547" and "843 bin 288".
    tokens = re.findall(r"\d+|milyon|bin", text)
    if not tokens:
        return None
    total = 0
    current = 0
    for tok in tokens:
        if tok.isdigit():
            current = int(tok)
        elif tok == "milyon":
            total += current * 1_000_000
            current = 0
        elif tok == "bin":
            total += current * 1_000
            current = 0
    total += current
    return total if total > 0 else None


def get(url: str, **kwargs) -> requests.Response:
    for attempt in range(6):
        try:
            r = S.get(url, timeout=60, **kwargs)
            if r.status_code == 200:
                return r
            if r.status_code in {429, 500, 502, 503, 504}:
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
        except Exception:
            if attempt == 5:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(url)


def inspect_category() -> dict:
    report = {}
    variants = []
    for n in [1, 2, 3, 5, 10, 20]:
        variants.extend([
            (f"page_{n}", CATEGORY, {"page": n}),
            (f"p_{n}", CATEGORY, {"p": n}),
            (f"sayfa_{n}", CATEGORY, {"sayfa": n}),
            (f"pagepath_{n}", f"{CATEGORY}/{n}", None),
        ])
    variants.insert(0, ("base", CATEGORY, None))
    for name, url, params in variants:
        try:
            r = get(url, params=params)
            html = r.text
            soup = BeautifulSoup(html, "html.parser")
            links = []
            for a in soup.find_all("a", href=True):
                href = urljoin(r.url, a["href"])
                label = " ".join(a.get_text(" ", strip=True).split())
                if (
                    "gunluk-elektrik-uretim-ve-tuketim-verileri" in href
                    or "loadmore" in href.lower()
                    or "page" in href.lower()
                    or "sayfa" in href.lower()
                ):
                    links.append({"href": href, "label": label})
            scripts = [urljoin(r.url, x["src"]) for x in soup.find_all("script", src=True)]
            dates = re.findall(r"\b\d{2}\.\d{2}\.20\d{2}\b", soup.get_text(" ", strip=True))
            report[name] = {
                "final_url": r.url,
                "status": r.status_code,
                "len": len(html),
                "title": soup.title.get_text(" ", strip=True) if soup.title else None,
                "daily_links": links[:300],
                "dates": dates[:100],
                "scripts": scripts,
            }
            (OUT / f"category_{name}.html").write_text(html, encoding="utf-8")
        except Exception as exc:
            report[name] = {"error": repr(exc)}
    (OUT / "category_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def collect_category_links(report: dict) -> set[str]:
    urls: set[str] = set()
    for value in report.values():
        for row in value.get("daily_links", []):
            href = row.get("href", "")
            if "gunluk-elektrik-uretim-ve-tuketim-verileri" in href:
                urls.add(href.split("?")[0])
    return urls


def parse_article(url: str) -> dict:
    r = get(url)
    soup = BeautifulSoup(r.text, "html.parser")
    text = " ".join(soup.get_text(" ", strip=True).split())
    title = soup.title.get_text(" ", strip=True) if soup.title else ""

    # Publication timestamp appears in visible page text and/or meta tags.
    published = None
    candidates = []
    for meta in soup.find_all("meta"):
        val = meta.get("content")
        key = (meta.get("property") or meta.get("name") or "").lower()
        if val and ("published" in key or "date" in key):
            candidates.append(val)
    candidates.extend(re.findall(r"\b\d{2}\.\d{2}\.20\d{2}(?:\s+\d{2}:\d{2})?\b", text))
    for value in candidates:
        for fmt in ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"]:
            try:
                published = datetime.strptime(value[:25], fmt)
                break
            except Exception:
                continue
        if published is not None:
            break

    # Main Turkish AA sentence.
    m = re.search(
        r"günlük bazda(?: dün)?\s+(.{1,80}?)\s+megavatsaat elektrik üretildi[,\s]+tüketim ise\s+(.{1,80}?)\s+megavatsaat",
        text,
        flags=re.I,
    )
    if not m:
        m = re.search(
            r"üretim(?:i)?\s+(.{1,80}?)\s+megavatsaat.*?tüketim(?:i)?(?: ise)?\s+(.{1,80}?)\s+megavatsaat",
            text,
            flags=re.I,
        )
    production = parse_tr_number(m.group(1)) if m else None
    consumption = parse_tr_number(m.group(2)) if m else None

    max_m = re.search(r"en yüksek elektrik tüketimi\s+(.{1,50}?)\s+megavatsaat(?:le| ile).*?(\d{1,2})[\.:]00", text, flags=re.I)
    min_m = re.search(r"en düşük tüketim(?: ise)?\s+(.{1,50}?)\s+megavatsaat(?:le| ile).*?(\d{1,2})[\.:]00", text, flags=re.I)
    export_m = re.search(r"(\d[\d\s\.]*?)\s+megavatsaat elektrik ihrac", text, flags=re.I)
    import_m = re.search(r"(\d[\d\s\.]*?)\s+megavatsaat elektrik ithalat", text, flags=re.I)

    target_date = None
    if published is not None and ("dün" in text.lower() or "dun" in text.lower()):
        target_date = published.date() - timedelta(days=1)

    return {
        "url": r.url,
        "article_id": int(re.search(r"/(\d+)(?:\?|$)", r.url).group(1)) if re.search(r"/(\d+)(?:\?|$)", r.url) else None,
        "title": title,
        "published": published.isoformat() if published else None,
        "target_date": target_date.isoformat() if target_date else None,
        "production_mwh": production,
        "consumption_mwh": consumption,
        "max_hourly_mwh": parse_tr_number(max_m.group(1)) if max_m else None,
        "max_hour": int(max_m.group(2)) if max_m else None,
        "min_hourly_mwh": parse_tr_number(min_m.group(1)) if min_m else None,
        "min_hour": int(min_m.group(2)) if min_m else None,
        "exports_mwh": parse_tr_number(export_m.group(1)) if export_m else None,
        "imports_mwh": parse_tr_number(import_m.group(1)) if import_m else None,
        "text_len": len(text),
    }


def main() -> None:
    report = inspect_category()
    urls = collect_category_links(report)

    # Category variants may expose only a recent subset. Also scan the article-ID
    # interval around known 2025-2026 AA Energy records. Nonmatching pages are ignored.
    known_ids = [51471, 52635, 53570]
    for article_id in known_ids:
        urls.add(f"{CATEGORY}/gunluk-elektrik-uretim-ve-tuketim-verileri/{article_id}")

    rows = []
    for i, url in enumerate(sorted(urls)):
        try:
            row = parse_article(url)
            rows.append(row)
            print(i + 1, len(urls), row.get("target_date"), row.get("consumption_mwh"), row.get("url"), flush=True)
        except Exception as exc:
            rows.append({"url": url, "error": repr(exc)})
        time.sleep(0.12)

    pd.DataFrame(rows).to_csv(OUT / "aa_daily_load_seed.csv", index=False)
    (OUT / "aa_daily_load_seed.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"urls": len(urls), "parsed": sum(bool(r.get("consumption_mwh")) for r in rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
