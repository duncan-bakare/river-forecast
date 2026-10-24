"""Stations, forecast points and endpoints for the Dodder and Suir catchments."""

USER_AGENT = "river-forecast/0.1 (+https://github.com/duncan-bakare/river-forecast)"

OPW_BASE = "https://waterlevel.ie"
MET_FORECAST_URL = "http://openaccess.pf.api.met.ie/metno-wdb2ts/locationforecast?lat={lat};long={lon}"
MET_WARNINGS_URL = "https://www.met.ie/Open_Data/json/warning_IRELAND.json"
# Undocumented backend of met.ie; unknown slugs silently return Dublin Airport.
MET_OBS_URL = "https://prodapi.metweb.ie/observations/{slug}/today"

# OPW sensors we archive per station: 0001 = water level (m, local staff gauge),
# OD = level relative to Ordnance Datum. 0002 temperature, 0003 battery voltage and
# 0014 signal strength are not useful for forecasting.
OPW_SENSORS = ("0001", "OD")
# Stations without an OD sensor (the FFWS back-up loggers and Hollyford); their
# OD CSVs 404.
OPW_NO_OD = {"16149", "16150", "16151", "16153", "16154", "16201"}

# 5-digit OPW station numbers. Hydrometric area 09 = Liffey & Dublin Bay (only two
# of its stations are on the Dodder); area 16 = Suir.
CATCHMENTS = {
    "dodder": {
        "09010": "Waldron's Bridge",
        "09369": "Anglesea Road",
    },
    "suir": {
        "16001": "Athlummon",
        "16002": "Beakstown",
        "16003": "Rathkennan",
        "16004": "Thurles",
        "16005": "Aughnagross",
        "16006": "Ballinaclogh",
        "16007": "Killardry",
        "16008": "New Bridge Suir",
        "16009": "Caher Park",
        "16010": "Anner",
        "16011": "Clonmel",
        "16012": "Tar Bridge",
        "16013": "Fourmilewater FFWS Main Logger",
        "16051": "Clobanna",
        "16062": "Carrick On Suir",
        "16073": "Marlfield Reservoir",
        "16125": "Piltown",
        "16137": "Newcastle Bridge",
        "16139": "Knocklofty",
        "16146": "Sandy Banks",
        "16147": "Joyce's Lane",
        "16148": "Workhouse Bridge",
        "16149": "Clonmel FFWS back-up",
        "16150": "Caher Park FFWS back-up",
        "16151": "Tar Bridge FFWS back-up",
        "16152": "Fourmilewater FFWS back-up",
        "16153": "New Bridge Suir FFWS back-up",
        "16154": "Killardry FFWS back-up",
        "16155": "Ardfinnan",
        "16156": "Templemore",
        "16157": "Gleann Beag",
        "16158": "Borrisoleigh",
        "16159": "Grove",
        "16160": "Adelphi Quay",
        "16201": "Hollyford",
    },
}

# Met Éireann point forecasts spread over each catchment (lat, lon).
FORECAST_POINTS = {
    "dodder_glenasmole": (53.235, -6.365),
    "dodder_firhouse": (53.283, -6.335),
    "dodder_ballsbridge": (53.328, -6.231),
    "suir_templemore": (52.796, -7.840),
    "suir_thurles": (52.679, -7.810),
    "suir_multeen": (52.555, -8.050),
    "suir_cahir": (52.358, -7.923),
    "suir_nire": (52.274, -7.756),
    "suir_anner": (52.420, -7.600),
    "suir_clonmel": (52.352, -7.695),
    "suir_carrick": (52.344, -7.410),
}

# Met Éireann synoptic stations nearest each catchment: slug -> expected name.
OBS_STATIONS = {
    "casement": "Casement",
    "dublinairport": "Dublin Airport",
    "phoenix-park": "Phoenix Park",
    "gurteen": "Gurteen",
    "oak-park": "Oak Park",
    "johnstown-castle": "Johnstown Castle",
    "moore-park": "Moore Park",
}


def sensors_for(station):
    return tuple(s for s in OPW_SENSORS if not (s == "OD" and station in OPW_NO_OD))


def all_stations():
    """[(catchment, station_number, name)] for every configured OPW station."""
    return [(c, num, name) for c, stations in CATCHMENTS.items() for num, name in stations.items()]
