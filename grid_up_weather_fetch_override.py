from __future__ import annotations

import grid_up_weather_fetch as base

# Open-Meteo's name search occasionally resolves Turkish district names to a
# homonym in another province or falls back to the province centre. These
# district-centre coordinates override only those ambiguous cases.
MANUAL_COORDS = {
    ("MANİSA", "KIRKAĞAÇ"): (39.1064, 27.6693),
    ("MANİSA", "KÖPRÜBAŞI"): (38.7490, 28.4040),
    ("MANİSA", "SARIGÖL"): (38.2390, 28.6970),
    ("MANİSA", "SARUHANLI"): (38.7340, 27.5680),
    ("MANİSA", "YUNUSEMRE"): (38.6250, 27.4050),
    ("MANİSA", "ŞEHZADELER"): (38.6140, 27.4250),
    ("İZMİR", "BAYINDIR"): (38.2170, 27.6480),
    ("İZMİR", "KEMALPAŞA"): (38.4270, 27.4170),
    ("İZMİR", "TORBALI"): (38.1550, 27.3630),
    ("İZMİR", "KINIK"): (39.0870, 27.3830),
    ("İZMİR", "BAYRAKLI"): (38.4620, 27.1670),
    ("İZMİR", "KARŞIYAKA"): (38.4560, 27.1130),
    ("İZMİR", "NARLIDERE"): (38.3940, 27.0050),
}

_original_geocode = base.geocode


def patched_geocode(session, district: str, province: str):
    key = (province, district)
    if key in MANUAL_COORDS:
        latitude, longitude = MANUAL_COORDS[key]
        return {
            "latitude": latitude,
            "longitude": longitude,
            "elevation": None,
            "geocoder_name": district,
            "admin1": province,
            "admin2": district,
            "source": "manual_district_centroid",
        }
    return _original_geocode(session, district, province)


base.geocode = patched_geocode
base.main()
