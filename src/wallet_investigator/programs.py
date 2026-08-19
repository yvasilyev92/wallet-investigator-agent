"""Plain-English OFAC program codes for local lookup (no web search)."""

OFAC_PROGRAMS: dict[str, str] = {
    "CYBER2": (
        "Malicious cyber-enabled activities under EOs 13694/13757: persons "
        "who engage in significant cyber attacks against critical infrastructure "
        "or for financial/commercial gain."
    ),
    "CYBER3": (
        "Additional malicious cyber designations in the CYBER series, typically "
        "tied to ransomware, theft of cryptocurrency, or related support networks."
    ),
    "CYBER4": (
        "Later cyber-related designations (including EO 14144 / EO 14306 authorities) "
        "covering significant malicious cyber actors and facilitators."
    ),
    "ILLICIT-DRUGS-EO14059": (
        "Executive Order 14059 (Imposing Sanctions on Foreign Persons Involved in "
        "the Global Illicit Drug Trade): traffickers, money launderers, and support "
        "networks for illicit drugs including fentanyl and precursors."
    ),
    "DPRK3": (
        "North Korea program under EO 13722: blocks the Government of North Korea "
        "and persons who operate in specified DPRK industries or support the regime, "
        "including designated cyber groups."
    ),
    "DPRK4": (
        "North Korea program under EO 13810: additional DPRK-related blocking, "
        "including persons who operate in construction, energy, financial services, "
        "or other specified DPRK industries."
    ),
    "SDGT": (
        "Specially Designated Global Terrorist under EO 13224: persons who commit, "
        "threaten, or support terrorism, or who are owned/controlled by such persons."
    ),
    "RUSSIA-EO14024": (
        "EO 14024 (Russia-related harmful foreign activities): the Government of "
        "the Russian Federation, its financial sector, and persons who operate in "
        "specified sectors of the Russian economy."
    ),
    "ELECTION-EO13848": (
        "EO 13848 (Imposing Certain Sanctions in the Event of Foreign Interference "
        "in a United States Election): persons who have engaged in or assisted "
        "foreign election interference."
    ),
    "NPWMD": (
        "Non-proliferation of weapons of mass destruction under EO 13382: persons "
        "who have engaged in activities or transactions that have materially "
        "contributed to WMD or missile proliferation."
    ),
    "IFSR": (
        "Iranian Financial Sanctions Regulations: restrictions on certain Iranian "
        "financial institutions and persons who facilitate Iran-related transactions."
    ),
    "IRGC": (
        "Islamic Revolutionary Guard Corps-related designation: the IRGC and persons "
        "owned, controlled, or acting for it, often overlapping IFSR/SDGT authorities."
    ),
}


def lookup_ofac_program(code: str) -> str:
    key = (code or "").strip().upper()
    if not key:
        return "No program code provided."
    if key in OFAC_PROGRAMS:
        return f"{key}: {OFAC_PROGRAMS[key]}"
    # allow already-pretty keys
    for known, text in OFAC_PROGRAMS.items():
        if known.replace("-", "") == key.replace("-", ""):
            return f"{known}: {text}"
    return (
        f"No local glossary entry for '{code}'. "
        "Use legal_authorities from the OFAC record instead of guessing."
    )
