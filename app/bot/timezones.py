"""Timezone presets: all Europe/* zones + Russian zones (which live in Asia/)."""
import zoneinfo

# Canonical zone lists (validated against tzdata at import time)
RUSSIA_ZONES = sorted([
    "Europe/Kaliningrad", "Europe/Moscow", "Europe/Simferopol", "Europe/Kirov",
    "Europe/Volgograd", "Europe/Astrakhan", "Europe/Saratov", "Europe/Ulyanovsk",
    "Europe/Samara", "Asia/Yekaterinburg", "Asia/Omsk", "Asia/Novosibirsk",
    "Asia/Barnaul", "Asia/Tomsk", "Asia/Novokuznetsk", "Asia/Krasnoyarsk",
    "Asia/Irkutsk", "Asia/Chita", "Asia/Yakutsk", "Asia/Khandyga",
    "Asia/Vladivostok", "Asia/Ust-Nera", "Asia/Magadan", "Asia/Sakhalin",
    "Asia/Srednekolymsk", "Asia/Kamchatka", "Asia/Anadyr",
])

EUROPE_ZONES = sorted(z for z in zoneinfo.available_timezones() if z.startswith("Europe/"))

UTC = ["UTC"]

# Flat list: Russia first, then the rest of Europe, then UTC
TIMEZONES = RUSSIA_ZONES + [z for z in EUROPE_ZONES if z not in RUSSIA_ZONES] + UTC

PAGE_SIZE = 8
