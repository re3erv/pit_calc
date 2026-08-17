# data/pipe_catalog.py

PIPE_CATALOG = {
    "Сталь электросварная": {
        "options": [
            {"d": 0.108, "t": 0.004, "rough": 0.00005, "r_min": 0.2},
            {"d": 0.159, "t": 0.005, "rough": 0.00005, "r_min": 0.3},
            {"d": 0.219, "t": 0.005, "rough": 0.00005, "r_min": 0.4},
            {"d": 0.273, "t": 0.005, "rough": 0.00005, "r_min": 0.5},
            {"d": 0.325, "t": 0.005, "rough": 0.00005, "r_min": 0.5},
            {"d": 0.426, "t": 0.006, "rough": 0.00005, "r_min": 0.6},
            {"d": 0.530, "t": 0.007, "rough": 0.00005, "r_min": 0.8},
        ],
        "sdr": None,
        "rough": 0.00005,
        "r_min_coeff": None,
    },
}

for sdr in [7.4, 9, 11, 13.6, 17, 17.6, 21, 26, 33, 41]:
    PIPE_CATALOG[f"ПЭ-100 SDR {sdr}"] = {
        "sdr": sdr,
        "diameters": [
            0.110, 0.125, 0.140, 0.160, 0.180, 0.200, 0.225, 0.250,
            0.280, 0.315, 0.355, 0.400, 0.450, 0.500, 0.560, 0.630,
            0.710, 0.800, 0.900, 1.000, 1.200
        ],
        "rough": 0.00001,
        "r_min_coeff": 25,
    }

def get_pipe_type_names():
    return sorted(PIPE_CATALOG.keys())

def get_pe_params(sdr, diameter):
    t = diameter / sdr
    rough = PIPE_CATALOG[f"ПЭ-100 SDR {sdr}"]["rough"]
    r_min = PIPE_CATALOG[f"ПЭ-100 SDR {sdr}"]["r_min_coeff"] * diameter
    return t, rough, r_min

def get_steel_options_for_diameter(type_name, diameter):
    catalog = PIPE_CATALOG[type_name]
    options = catalog.get("options", [])
    return [o for o in options if abs(o["d"] - diameter) < 1e-9]

def get_default_params(type_name, diameter, wall=None):
    catalog = PIPE_CATALOG[type_name]
    if catalog["sdr"] is not None:
        t, rough, r_min = get_pe_params(catalog["sdr"], diameter)
        return {"outer_d": diameter, "wall": t, "rough": rough, "r_min": r_min}
    else:
        filtered = get_steel_options_for_diameter(type_name, diameter)
        if not filtered:
            return None
        if wall is not None:
            for o in filtered:
                if abs(o["t"] - wall) < 1e-9:
                    return {"outer_d": o["d"], "wall": o["t"], "rough": o["rough"], "r_min": o["r_min"]}
        o = filtered[0]
        return {"outer_d": o["d"], "wall": o["t"], "rough": o["rough"], "r_min": o["r_min"]}