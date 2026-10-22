"""SEC EDGAR location codes / country names -> finviz geo_<slug> country slugs.

An INFERRED mapping: finviz's "country" is the company's location (headquarters), so the business address wins over
the state of incorporation. Countries finviz doesn't list get the lowercase letters-only slug of their name.
EDGAR codes are SEC's "EDGAR state and country codes" table (sec.gov, fetched 2026-10-22)."""
import re

_US = ("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK "
       "OR PA RI SC SD TN TX UT VT VA WA WV WI WY X1 PR VI GU B5 1V 2J")  # states, DC, US, territories
_CANADA = "A0 A1 A2 A3 A4 A5 A6 A7 A8 A9 B0 Z4"  # provinces / territories, Z4 federal level

_FOREIGN = {
    "B2": "AFGHANISTAN", "Y6": "ALAND ISLANDS", "B3": "ALBANIA", "B4": "ALGERIA", "B6": "ANDORRA", "B7": "ANGOLA",
    "1A": "ANGUILLA", "B8": "ANTARCTICA", "B9": "ANTIGUA AND BARBUDA", "C1": "ARGENTINA", "1B": "ARMENIA",
    "1C": "ARUBA", "C3": "AUSTRALIA", "C4": "AUSTRIA", "1D": "AZERBAIJAN", "C5": "BAHAMAS", "C6": "BAHRAIN",
    "C7": "BANGLADESH", "C8": "BARBADOS", "1F": "BELARUS", "C9": "BELGIUM", "D1": "BELIZE", "G6": "BENIN",
    "D0": "BERMUDA", "D2": "BHUTAN", "D3": "BOLIVIA", "1E": "BOSNIA AND HERZEGOVINA", "B1": "BOTSWANA",
    "D4": "BOUVET ISLAND", "D5": "BRAZIL", "D6": "BRITISH INDIAN OCEAN TERRITORY", "D9": "BRUNEI DARUSSALAM",
    "E0": "BULGARIA", "X2": "BURKINA FASO", "E2": "BURUNDI", "E3": "CAMBODIA", "E4": "CAMEROON", "E8": "CAPE VERDE",
    "E9": "CAYMAN ISLANDS", "F0": "CENTRAL AFRICAN REPUBLIC", "F2": "CHAD", "F3": "CHILE", "F4": "CHINA",
    "F6": "CHRISTMAS ISLAND", "F7": "COCOS (KEELING) ISLANDS", "F8": "COLOMBIA", "F9": "COMOROS", "G0": "CONGO",
    "Y3": "CONGO, THE DEMOCRATIC REPUBLIC OF THE", "G1": "COOK ISLANDS", "G2": "COSTA RICA", "L7": "COTE D'IVOIRE",
    "1M": "CROATIA", "G3": "CUBA", "G4": "CYPRUS", "2N": "CZECH REPUBLIC", "G7": "DENMARK", "1G": "DJIBOUTI",
    "G9": "DOMINICA", "G8": "DOMINICAN REPUBLIC", "H1": "ECUADOR", "H2": "EGYPT", "H3": "EL SALVADOR",
    "H4": "EQUATORIAL GUINEA", "1J": "ERITREA", "1H": "ESTONIA", "H5": "ETHIOPIA", "H7": "FALKLAND ISLANDS (MALVINAS)",
    "H6": "FAROE ISLANDS", "H8": "FIJI", "H9": "FINLAND", "I0": "FRANCE", "I3": "FRENCH GUIANA",
    "I4": "FRENCH POLYNESIA", "2C": "FRENCH SOUTHERN TERRITORIES", "I5": "GABON", "I6": "GAMBIA", "2Q": "GEORGIA",
    "2M": "GERMANY", "J0": "GHANA", "J1": "GIBRALTAR", "J3": "GREECE", "J4": "GREENLAND", "J5": "GRENADA",
    "J6": "GUADELOUPE", "J8": "GUATEMALA", "Y7": "GUERNSEY", "J9": "GUINEA", "S0": "GUINEA-BISSAU", "K0": "GUYANA",
    "K1": "HAITI", "K4": "HEARD ISLAND AND MCDONALD ISLANDS", "X4": "HOLY SEE (VATICAN CITY STATE)", "K2": "HONDURAS",
    "K3": "HONG KONG", "K5": "HUNGARY", "K6": "ICELAND", "K7": "INDIA", "K8": "INDONESIA",
    "K9": "IRAN, ISLAMIC REPUBLIC OF", "L0": "IRAQ", "L2": "IRELAND", "Y8": "ISLE OF MAN", "L3": "ISRAEL",
    "L6": "ITALY", "L8": "JAMAICA", "M0": "JAPAN", "Y9": "JERSEY", "M2": "JORDAN", "1P": "KAZAKSTAN", "M3": "KENYA",
    "J2": "KIRIBATI", "M4": "KOREA, DEMOCRATIC PEOPLE'S REPUBLIC OF", "M5": "KOREA, REPUBLIC OF", "M6": "KUWAIT",
    "1N": "KYRGYZSTAN", "M7": "LAO PEOPLE'S DEMOCRATIC REPUBLIC", "1R": "LATVIA", "M8": "LEBANON", "M9": "LESOTHO",
    "N0": "LIBERIA", "N1": "LIBYAN ARAB JAMAHIRIYA", "N2": "LIECHTENSTEIN", "1Q": "LITHUANIA", "N4": "LUXEMBOURG",
    "N5": "MACAU", "1U": "MACEDONIA, THE FORMER YUGOSLAV REPUBLIC OF", "N6": "MADAGASCAR", "N7": "MALAWI",
    "N8": "MALAYSIA", "N9": "MALDIVES", "O0": "MALI", "O1": "MALTA", "1T": "MARSHALL ISLANDS", "O2": "MARTINIQUE",
    "O3": "MAURITANIA", "O4": "MAURITIUS", "2P": "MAYOTTE", "O5": "MEXICO", "1K": "MICRONESIA, FEDERATED STATES OF",
    "1S": "MOLDOVA, REPUBLIC OF", "O9": "MONACO", "P0": "MONGOLIA", "Z5": "MONTENEGRO", "P1": "MONTSERRAT",
    "P2": "MOROCCO", "P3": "MOZAMBIQUE", "E1": "MYANMAR", "T6": "NAMIBIA", "P5": "NAURU", "P6": "NEPAL",
    "P7": "NETHERLANDS", "P8": "NETHERLANDS ANTILLES", "1W": "NEW CALEDONIA", "Q2": "NEW ZEALAND", "Q3": "NICARAGUA",
    "Q4": "NIGER", "Q5": "NIGERIA", "Q6": "NIUE", "Q7": "NORFOLK ISLAND", "Q8": "NORWAY", "P4": "OMAN",
    "R0": "PAKISTAN", "1Y": "PALAU", "1X": "PALESTINIAN TERRITORY, OCCUPIED", "R1": "PANAMA", "R2": "PAPUA NEW GUINEA",
    "R4": "PARAGUAY", "R5": "PERU", "R6": "PHILIPPINES", "R8": "PITCAIRN", "R9": "POLAND", "S1": "PORTUGAL",
    "S3": "QATAR", "S4": "REUNION", "S5": "ROMANIA", "1Z": "RUSSIAN FEDERATION", "S6": "RWANDA",
    "Z0": "SAINT BARTHELEMY", "U8": "SAINT HELENA", "U7": "SAINT KITTS AND NEVIS", "U9": "SAINT LUCIA",
    "Z1": "SAINT MARTIN", "V0": "SAINT PIERRE AND MIQUELON", "V1": "SAINT VINCENT AND THE GRENADINES", "Y0": "SAMOA",
    "S8": "SAN MARINO", "S9": "SAO TOME AND PRINCIPE", "T0": "SAUDI ARABIA", "T1": "SENEGAL", "Z2": "SERBIA",
    "T2": "SEYCHELLES", "T8": "SIERRA LEONE", "U0": "SINGAPORE", "2B": "SLOVAKIA", "2A": "SLOVENIA",
    "D7": "SOLOMON ISLANDS", "U1": "SOMALIA", "T3": "SOUTH AFRICA", "1L": "SOUTH GEORGIA AND THE SOUTH SANDWICH ISLANDS",
    "U3": "SPAIN", "F1": "SRI LANKA", "V2": "SUDAN", "V3": "SURINAME", "L9": "SVALBARD AND JAN MAYEN",
    "V6": "SWAZILAND", "V7": "SWEDEN", "V8": "SWITZERLAND", "V9": "SYRIAN ARAB REPUBLIC", "F5": "TAIWAN",
    "2D": "TAJIKISTAN", "W0": "TANZANIA, UNITED REPUBLIC OF", "W1": "THAILAND", "Z3": "TIMOR-LESTE", "W2": "TOGO",
    "W3": "TOKELAU", "W4": "TONGA", "W5": "TRINIDAD AND TOBAGO", "W6": "TUNISIA", "W8": "TURKEY",
    "2E": "TURKMENISTAN", "W7": "TURKS AND CAICOS ISLANDS", "2G": "TUVALU", "W9": "UGANDA", "2H": "UKRAINE",
    "C0": "UNITED ARAB EMIRATES", "X0": "UNITED KINGDOM", "X3": "URUGUAY", "2K": "UZBEKISTAN", "2L": "VANUATU",
    "X5": "VENEZUELA", "Q1": "VIET NAM", "D8": "VIRGIN ISLANDS, BRITISH", "X8": "WALLIS AND FUTUNA",
    "U5": "WESTERN SAHARA", "T7": "YEMEN", "Y4": "ZAMBIA", "Y5": "ZIMBABWE",
}

# letters-only lowercase name -> slug (finviz's spelling where finviz lists the country)
_ALIAS = {
    "us": "usa", "unitedstates": "usa", "unitedstatesofamerica": "usa", "virginislandsus": "usa", "puertorico": "usa",
    "uk": "unitedkingdom", "england": "unitedkingdom", "greatbritain": "unitedkingdom", "scotland": "unitedkingdom",
    "wales": "unitedkingdom", "northernireland": "unitedkingdom",
    "korearepublicof": "southkorea", "republicofkorea": "southkorea", "korea": "southkorea",
    "koreademocraticpeoplesrepublicof": "northkorea",
    "peoplesrepublicofchina": "china", "chinapeoplesrepublicof": "china", "prc": "china",
    "taiwanprovinceofchina": "taiwan", "republicofchina": "taiwan",
    "hongkongsar": "hongkong", "hongkongchina": "hongkong",
    "uae": "unitedarabemirates", "virginislandsbritish": "britishvirginislands", "bvi": "britishvirginislands",
    "russianfederation": "russia", "kazakstan": "kazakhstan", "vietnam": "vietnam", "vietnamsocialistrepublicof": "vietnam",
    "thebahamas": "bahamas", "bahamasthe": "bahamas", "macao": "macau",
    "iranislamicrepublicof": "iran", "tanzaniaunitedrepublicof": "tanzania", "moldovarepublicof": "moldova",
    "micronesiafederatedstatesof": "micronesia", "macedoniatheformeryugoslavrepublicof": "northmacedonia",
    "congothedemocraticrepublicofthe": "drcongo", "laopeoplesdemocraticrepublic": "laos", "bruneidarussalam": "brunei",
    "libyanarabjamahiriya": "libya", "syrianarabrepublic": "syria", "palestinianterritoryoccupied": "palestine",
    "holyseevaticancitystate": "vatican", "falklandislandsmalvinas": "falklandislands",
    "cocoskeelingislands": "cocosislands", "canadafederallevel": "canada",
}


def slug(name):
    """Country name -> slug (lowercase letters only, aliased to finviz's spelling)."""
    s = re.sub(r"[^a-z]", "", (name or "").lower())
    return _ALIAS.get(s, s) or None


EDGAR = {**{c: "usa" for c in _US.split()}, **{c: "canada" for c in _CANADA.split()},
         **{c: slug(n) for c, n in _FOREIGN.items()}}


def _from_address(a):
    if not isinstance(a, dict):
        return None
    code = str(a.get("stateOrCountry") or "").strip().upper()
    if code in EDGAR:
        return EDGAR[code]
    for key in ("stateOrCountryDescription", "country"):
        v = str(a.get(key) or "").strip()
        if v.upper() in EDGAR:  # US descriptions are just the state code
            return EDGAR[v.upper()]
        if v and v.upper() != "UNKNOWN" and slug(v):
            return slug(v)
    return None


def country(addresses, state_of_inc=None):
    """SEC submissions `addresses` (+ stateOfIncorporation) -> country slug, or None."""
    addresses = addresses if isinstance(addresses, dict) else {}
    for kind in ("business", "mailing"):
        c = _from_address(addresses.get(kind))
        if c:
            return c
    return EDGAR.get(str(state_of_inc or "").strip().upper())
