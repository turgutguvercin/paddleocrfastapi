from __future__ import annotations

import json
import time
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

START_DATE = "2025-01-01"
END_DATE = "2026-07-31"
OUT_DIR = Path("weather_output")

LOCATIONS = {
    "İZMİR": [
        "ALİAĞA", "BALÇOVA", "BAYINDIR", "BAYRAKLI", "BERGAMA", "BEYDAĞ",
        "BORNOVA", "BUCA", "DİKİLİ", "FOÇA", "GAZİEMİR", "GÜZELBAHÇE",
        "KARABAĞLAR", "KARABURUN", "KARŞIYAKA", "KEMALPAŞA", "KINIK",
        "KİRAZ", "KONAK", "MENDERES", "MENEMEN", "NARLIDERE", "SEFERİHİSAR",
        "SELÇUK", "TİRE", "TORBALI", "URLA", "ÇEŞME", "ÇİĞLİ", "ÖDEMİŞ",
    ],
    "MANİSA": [
        "AHMETLİ", "AKHİSAR", "ALAŞEHİR", "DEMİRCİ", "GÖLMARMARA", "GÖRDES",
        "KIRKAĞAÇ", "KULA", "KÖPRÜBAŞI", "SALİHLİ", "SARIGÖL", "SARUHANLI",
        "SELENDİ", "SOMA", "TURGUTLU", "YUNUSEMRE", "ŞEHZADELER",
    ],
}

# Used only if the geocoder cannot resolve a district.
PROVINCE_FALLBACK = {
    "İZMİR": (38.4237, 27.1428),
    "MANİSA": (38.6191, 27.4289),
}

DAILY_VARS = [
    "temperature_2m_mean",
    "temperature_2m_max",
    "temperature_2m_min",
    "apparent_temperature_mean",
    "relative_humidity_2m_mean",
    "precipitation_sum",
    "rain_sum",
    "wind_speed_10m_mean",
    "wind_speed_10m_max",
    "shortwave_radiation_sum",
    "sunshine_duration",
    "daylight_duration",
    "et0_fao_evapotranspiration",
]


def norm(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("ı", "i").replace("İ", "I")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.upper().split())


def make_session() -> requests.Session:
    retry = Retry(
        total=7,
        connect=7,
        read=7,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    session = requests.Session()
    session.headers.update({"User-Agent": "grid-up-weather-research/1.0"})
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def geocode(session: requests.Session, district: str, province: str) -> dict[str, Any]:
    variants = [district.title(), district, norm(district).title()]
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []

    for query in variants:
        if query in seen:
            continue
        seen.add(query)
        response = session.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": query, "count": 20, "language": "tr", "format": "json"},
            timeout=60,
        )
        response.raise_for_status()
        candidates.extend(response.json().get("results", []))
        time.sleep(0.15)

    tr = [row for row in candidates if str(row.get("country_code", "")).upper() == "TR"]
    province_norm = norm(province)
    district_norm = norm(district)

    def score(row: dict[str, Any]) -> tuple[int, int, float]:
        row_name = norm(row.get("name"))
        admin1 = norm(row.get("admin1"))
        admin2 = norm(row.get("admin2"))
        s = 0
        s += 100 if admin1 == province_norm else 0
        s += 35 if province_norm in admin1 else 0
        s += 30 if row_name == district_norm else 0
        s += 20 if admin2 == district_norm else 0
        s += 10 if district_norm in row_name or district_norm in admin2 else 0
        feature_rank = {"PPLA2": 4, "PPLA3": 3, "PPLA": 2, "PPL": 1}.get(str(row.get("feature_code", "")), 0)
        population = float(row.get("population") or 0)
        return s, feature_rank, population

    if tr:
        chosen = max(tr, key=score)
        if score(chosen)[0] >= 30:
            return {
                "latitude": float(chosen["latitude"]),
                "longitude": float(chosen["longitude"]),
                "elevation": chosen.get("elevation"),
                "geocoder_name": chosen.get("name"),
                "admin1": chosen.get("admin1"),
                "admin2": chosen.get("admin2"),
                "source": "open_meteo_geocoder",
            }

    lat, lon = PROVINCE_FALLBACK[province]
    return {
        "latitude": lat,
        "longitude": lon,
        "elevation": None,
        "geocoder_name": None,
        "admin1": province,
        "admin2": district,
        "source": "province_fallback",
    }


def fetch_daily(
    session: requests.Session,
    latitude: float,
    longitude: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    response = session.get(
        "https://archive-api.open-meteo.com/v1/archive",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "start_date": START_DATE,
            "end_date": END_DATE,
            "daily": ",".join(DAILY_VARS),
            "timezone": "Europe/Istanbul",
            "models": "best_match",
        },
        timeout=180,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(payload)
    daily = payload.get("daily", {})
    if "time" not in daily:
        raise RuntimeError(f"No daily data returned: {payload}")
    frame = pd.DataFrame(daily)
    metadata = {
        "returned_latitude": payload.get("latitude"),
        "returned_longitude": payload.get("longitude"),
        "returned_elevation": payload.get("elevation"),
        "timezone": payload.get("timezone"),
        "generationtime_ms": payload.get("generationtime_ms"),
        "daily_units": payload.get("daily_units"),
    }
    return frame, metadata


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    session = make_session()
    all_weather: list[pd.DataFrame] = []
    coordinate_rows: list[dict[str, Any]] = []
    fetch_log: list[dict[str, Any]] = []

    total = sum(len(v) for v in LOCATIONS.values())
    counter = 0
    for province, districts in LOCATIONS.items():
        for district in districts:
            counter += 1
            lokasyon = f"{province}>{district}"
            print(f"[{counter:02d}/{total}] {lokasyon}", flush=True)
            geo = geocode(session, district, province)
            coordinate_rows.append({"lokasyon": lokasyon, "province": province, "district": district, **geo})

            frame, metadata = fetch_daily(session, geo["latitude"], geo["longitude"])
            frame.insert(0, "lokasyon", lokasyon)
            frame = frame.rename(columns={"time": "tarih"})
            all_weather.append(frame)
            fetch_log.append({"lokasyon": lokasyon, **metadata})
            time.sleep(0.2)

    weather = pd.concat(all_weather, ignore_index=True)
    weather["tarih"] = pd.to_datetime(weather["tarih"], errors="raise").dt.strftime("%Y-%m-%d")
    weather = weather.sort_values(["lokasyon", "tarih"]).reset_index(drop=True)

    expected_days = len(pd.date_range(START_DATE, END_DATE, freq="D"))
    counts = weather.groupby("lokasyon")["tarih"].nunique()
    if len(counts) != total or not (counts == expected_days).all():
        raise RuntimeError(f"Coverage validation failed: {counts.describe().to_dict()}")
    if weather.duplicated(["lokasyon", "tarih"]).any():
        raise RuntimeError("Duplicate lokasyon-tarih rows found")

    weather.to_csv(OUT_DIR / "weather_daily.csv", index=False)
    pd.DataFrame(coordinate_rows).to_csv(OUT_DIR / "district_coordinates.csv", index=False)
    metadata = {
        "start_date": START_DATE,
        "end_date": END_DATE,
        "expected_days_per_location": expected_days,
        "locations": total,
        "rows": int(len(weather)),
        "variables": DAILY_VARS,
        "fallback_locations": [r["lokasyon"] for r in coordinate_rows if r["source"] == "province_fallback"],
        "fetch_log": fetch_log,
    }
    (OUT_DIR / "fetch_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in metadata.items() if k != "fetch_log"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
