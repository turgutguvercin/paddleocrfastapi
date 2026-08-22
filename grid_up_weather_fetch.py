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

# Exact strings used by train.csv and test.csv. The final component is the
# district used for geocoding; the full string is preserved for the model join.
LOCATIONS = [
    "MANİSA>AHMETLİ", "MANİSA>AKHİSAR", "MANİSA>ALAŞEHİR", "MANİSA>DEMİRCİ",
    "MANİSA>GÖLMARMARA", "MANİSA>GÖRDES", "MANİSA>KIRKAĞAÇ", "MANİSA>KULA",
    "MANİSA>KÖPRÜBAŞI", "MANİSA>SALİHLİ", "MANİSA>SARIGÖL", "MANİSA>SARUHANLI",
    "MANİSA>SELENDİ", "MANİSA>SOMA", "MANİSA>TURGUTLU", "MANİSA>YUNUSEMRE",
    "MANİSA>ŞEHZADELER",
    "İZMİR>GÜNEY BÖLGE>BAYINDIR", "İZMİR>GÜNEY BÖLGE>BEYDAĞ",
    "İZMİR>GÜNEY BÖLGE>KARABURUN", "İZMİR>GÜNEY BÖLGE>KEMALPAŞA",
    "İZMİR>GÜNEY BÖLGE>KİRAZ", "İZMİR>GÜNEY BÖLGE>MENDERES",
    "İZMİR>GÜNEY BÖLGE>SEFERİHİSAR", "İZMİR>GÜNEY BÖLGE>SELÇUK",
    "İZMİR>GÜNEY BÖLGE>TORBALI", "İZMİR>GÜNEY BÖLGE>TİRE",
    "İZMİR>GÜNEY BÖLGE>URLA", "İZMİR>GÜNEY BÖLGE>ÇEŞME",
    "İZMİR>GÜNEY BÖLGE>ÖDEMİŞ",
    "İZMİR>KUZEY BÖLGE>ALİAĞA", "İZMİR>KUZEY BÖLGE>BERGAMA",
    "İZMİR>KUZEY BÖLGE>DİKİLİ", "İZMİR>KUZEY BÖLGE>FOÇA",
    "İZMİR>KUZEY BÖLGE>KINIK", "İZMİR>KUZEY BÖLGE>MENEMEN",
    "İZMİR>METROPOL>BALÇOVA", "İZMİR>METROPOL>BAYRAKLI",
    "İZMİR>METROPOL>BORNOVA", "İZMİR>METROPOL>BUCA",
    "İZMİR>METROPOL>GAZİEMİR", "İZMİR>METROPOL>GÜZELBAHÇE",
    "İZMİR>METROPOL>KARABAĞLAR", "İZMİR>METROPOL>KARŞIYAKA",
    "İZMİR>METROPOL>KONAK", "İZMİR>METROPOL>NARLIDERE",
    "İZMİR>METROPOL>ÇİĞLİ",
]

PROVINCE_FALLBACK = {
    "İZMİR": (38.4237, 27.1428),
    "MANİSA": (38.6191, 27.4289),
}

# These are documented daily variables in the Open-Meteo historical API.
DAILY_VARS = [
    "temperature_2m_mean", "temperature_2m_max", "temperature_2m_min",
    "apparent_temperature_mean", "apparent_temperature_max", "apparent_temperature_min",
    "precipitation_sum", "rain_sum", "snowfall_sum", "precipitation_hours",
    "wind_speed_10m_max", "wind_gusts_10m_max",
    "shortwave_radiation_sum", "sunshine_duration", "daylight_duration",
    "et0_fao_evapotranspiration",
]

# Hourly variables are aggregated to daily means so humidity/cloud information
# can be included without creating a large hourly modelling table.
HOURLY_VARS = ["relative_humidity_2m", "dew_point_2m", "cloud_cover"]


def norm(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("ı", "i").replace("İ", "I")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.upper().split())


def make_session() -> requests.Session:
    retry = Retry(
        total=7, connect=7, read=7, backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",), raise_on_status=False,
    )
    session = requests.Session()
    session.headers.update({"User-Agent": "grid-up-weather-research/1.1"})
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def geocode(session: requests.Session, district: str, province: str) -> dict[str, Any]:
    variants = [district.title(), district, norm(district).title()]
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in variants:
        if query in seen:
            continue
        seen.add(query)
        response = session.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": query, "count": 30, "language": "tr", "format": "json"},
            timeout=60,
        )
        response.raise_for_status()
        candidates.extend(response.json().get("results", []))
        time.sleep(0.12)

    candidates = [r for r in candidates if str(r.get("country_code", "")).upper() == "TR"]
    p_norm, d_norm = norm(province), norm(district)

    def score(row: dict[str, Any]) -> tuple[int, int, float]:
        name, admin1, admin2 = norm(row.get("name")), norm(row.get("admin1")), norm(row.get("admin2"))
        points = 0
        points += 100 if admin1 == p_norm else 0
        points += 40 if p_norm in admin1 else 0
        points += 35 if name == d_norm else 0
        points += 25 if admin2 == d_norm else 0
        points += 10 if d_norm in name or d_norm in admin2 else 0
        feature_rank = {"PPLA2": 5, "PPLA3": 4, "PPLA": 3, "PPL": 2, "PPLC": 1}.get(str(row.get("feature_code", "")), 0)
        return points, feature_rank, float(row.get("population") or 0)

    if candidates:
        chosen = max(candidates, key=score)
        if score(chosen)[0] >= 35:
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
        "latitude": lat, "longitude": lon, "elevation": None,
        "geocoder_name": None, "admin1": province, "admin2": district,
        "source": "province_fallback",
    }


def fetch_daily(session: requests.Session, latitude: float, longitude: float) -> tuple[pd.DataFrame, dict[str, Any]]:
    response = session.get(
        "https://archive-api.open-meteo.com/v1/archive",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "start_date": START_DATE,
            "end_date": END_DATE,
            "daily": ",".join(DAILY_VARS),
            "hourly": ",".join(HOURLY_VARS),
            "timezone": "Europe/Istanbul",
        },
        timeout=240,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(payload)

    daily = payload.get("daily", {})
    hourly = payload.get("hourly", {})
    if "time" not in daily or "time" not in hourly:
        raise RuntimeError(f"Incomplete weather response: {payload}")

    frame = pd.DataFrame(daily).rename(columns={"time": "tarih"})
    frame["tarih"] = pd.to_datetime(frame["tarih"], errors="raise")

    h = pd.DataFrame(hourly).rename(columns={"time": "datetime"})
    h["datetime"] = pd.to_datetime(h["datetime"], errors="raise")
    h["tarih"] = h["datetime"].dt.normalize()
    hourly_daily = h.groupby("tarih", as_index=False).agg(
        relative_humidity_2m_mean=("relative_humidity_2m", "mean"),
        relative_humidity_2m_max=("relative_humidity_2m", "max"),
        dew_point_2m_mean=("dew_point_2m", "mean"),
        cloud_cover_mean=("cloud_cover", "mean"),
    )
    frame = frame.merge(hourly_daily, on="tarih", how="left", validate="one_to_one")

    metadata = {
        "returned_latitude": payload.get("latitude"),
        "returned_longitude": payload.get("longitude"),
        "returned_elevation": payload.get("elevation"),
        "timezone": payload.get("timezone"),
        "generationtime_ms": payload.get("generationtime_ms"),
        "daily_units": payload.get("daily_units"),
        "hourly_units": payload.get("hourly_units"),
    }
    return frame, metadata


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    session = make_session()
    all_weather: list[pd.DataFrame] = []
    coordinate_rows: list[dict[str, Any]] = []
    fetch_log: list[dict[str, Any]] = []

    for counter, lokasyon in enumerate(LOCATIONS, 1):
        parts = lokasyon.split(">")
        province, district = parts[0], parts[-1]
        print(f"[{counter:02d}/{len(LOCATIONS)}] {lokasyon}", flush=True)
        geo = geocode(session, district, province)
        coordinate_rows.append({"lokasyon": lokasyon, "province": province, "district": district, **geo})

        frame, metadata = fetch_daily(session, geo["latitude"], geo["longitude"])
        frame.insert(0, "lokasyon", lokasyon)
        all_weather.append(frame)
        fetch_log.append({"lokasyon": lokasyon, **metadata})
        time.sleep(0.18)

    weather = pd.concat(all_weather, ignore_index=True)
    weather["tarih"] = pd.to_datetime(weather["tarih"], errors="raise").dt.strftime("%Y-%m-%d")
    weather = weather.sort_values(["lokasyon", "tarih"]).reset_index(drop=True)

    expected_days = len(pd.date_range(START_DATE, END_DATE, freq="D"))
    counts = weather.groupby("lokasyon")["tarih"].nunique()
    if len(counts) != len(LOCATIONS) or not (counts == expected_days).all():
        raise RuntimeError(f"Coverage validation failed: {counts.describe().to_dict()}")
    if weather.duplicated(["lokasyon", "tarih"]).any():
        raise RuntimeError("Duplicate lokasyon-tarih rows found")

    numeric_cols = [c for c in weather.columns if c not in {"lokasyon", "tarih"}]
    missing = weather[numeric_cols].isna().mean().sort_values(ascending=False)
    if float(missing.max()) > 0.05:
        raise RuntimeError(f"Unexpected weather missingness: {missing.head().to_dict()}")

    weather.to_csv(OUT_DIR / "weather_daily.csv", index=False)
    pd.DataFrame(coordinate_rows).to_csv(OUT_DIR / "district_coordinates.csv", index=False)
    metadata = {
        "start_date": START_DATE,
        "end_date": END_DATE,
        "expected_days_per_location": expected_days,
        "locations": len(LOCATIONS),
        "rows": int(len(weather)),
        "daily_variables": DAILY_VARS,
        "hourly_variables": HOURLY_VARS,
        "missing_fraction": {k: float(v) for k, v in missing.items()},
        "fallback_locations": [r["lokasyon"] for r in coordinate_rows if r["source"] == "province_fallback"],
        "fetch_log": fetch_log,
    }
    (OUT_DIR / "fetch_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in metadata.items() if k != "fetch_log"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
