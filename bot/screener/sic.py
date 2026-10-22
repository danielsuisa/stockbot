"""SEC SIC code -> finviz-style (sector, industry) slugs, for the sec_<slug> / ind_<slug> filters.

A static DECISION crosswalk: finviz's taxonomy (Yahoo/Morningstar style) is proprietary, so SIC-mapped values are
approximate and must be labelled "SIC-mapped". SIC codes are SEC's EDGAR list (sec.gov SIC code list, fetched
2026-10-22) plus 2084/2085 (standard SIC, so the wineries/distilleries industry is reachable). One SIC code covers
companies finviz splits across industries; each code takes the industry of its most common / largest filers."""
import re

SECTORS = ("basicmaterials", "communicationservices", "consumercyclical", "consumerdefensive", "energy", "financial",
           "healthcare", "industrials", "realestate", "technology", "utilities")

_BY_SECTOR = {
    "basicmaterials": "agriculturalinputs aluminum buildingmaterials chemicals cokingcoal copper gold lumberwoodproduction "
                      "otherindustrialmetalsmining otherpreciousmetalsmining paperpaperproducts silver specialtychemicals "
                      "steel",
    "communicationservices": "advertisingagencies broadcasting electronicgamingmultimedia entertainment "
                             "internetcontentinformation publishing telecomservices",
    "consumercyclical": "apparelmanufacturing apparelretail autotruckdealerships automanufacturers autoparts "
                        "departmentstores footwearaccessories furnishingsfixturesappliances gambling homeimprovementretail "
                        "internetretail leisure lodging luxurygoods packagingcontainers personalservices "
                        "recreationalvehicles residentialconstruction resortscasinos restaurants specialtyretail "
                        "textilemanufacturing travelservices",
    "consumerdefensive": "beveragesbrewers beveragesnonalcoholic beverageswineriesdistilleries confectioners discountstores "
                         "educationtrainingservices farmproducts fooddistribution grocerystores householdpersonalproducts "
                         "packagedfoods tobacco",
    "energy": "oilgasdrilling oilgasep oilgasequipmentservices oilgasintegrated oilgasmidstream oilgasrefiningmarketing "
              "thermalcoal uranium",
    "financial": "assetmanagement banksdiversified banksregional capitalmarkets creditservices financialconglomerates "
                 "financialdatastockexchanges insurancebrokers insurancediversified insurancelife "
                 "insurancepropertycasualty insurancereinsurance insurancespecialty mortgagefinance shellcompanies",
    "healthcare": "biotechnology diagnosticsresearch drugmanufacturersgeneral drugmanufacturersspecialtygeneric "
                  "healthcareplans healthinformationservices medicalcarefacilities medicaldevices medicaldistribution "
                  "medicalinstrumentssupplies pharmaceuticalretailers",
    "industrials": "aerospacedefense airlines airportsairservices buildingproductsequipment businessequipmentsupplies "
                   "conglomerates consultingservices electricalequipmentparts engineeringconstruction "
                   "farmheavyconstructionmachinery industrialdistribution infrastructureoperations "
                   "integratedfreightlogistics marineshipping metalfabrication pollutiontreatmentcontrols railroads "
                   "rentalleasingservices securityprotectionservices specialtybusinessservices "
                   "specialtyindustrialmachinery staffingemploymentservices toolsaccessories trucking wastemanagement",
    "realestate": "realestatedevelopment realestatediversified realestateservices reitdiversified "
                  "reithealthcarefacilities reithotelmotel reitindustrial reitmortgage reitoffice reitresidential "
                  "reitretail reitspecialty",
    "technology": "communicationequipment computerhardware consumerelectronics electroniccomponents "
                  "electronicscomputerdistribution informationtechnologyservices scientifictechnicalinstruments "
                  "semiconductorequipmentmaterials semiconductors softwareapplication softwareinfrastructure solar",
    "utilities": "utilitiesdiversified utilitiesindependentpowerproducers utilitiesregulatedelectric "
                 "utilitiesregulatedgas utilitiesregulatedwater utilitiesrenewable",
}
INDUSTRY_SECTOR = {ind: sec for sec, inds in _BY_SECTOR.items() for ind in inds.split()}

# industry slug -> SIC codes. Not mapped: 8880 ADRs, 8888 foreign governments, 9721 international affairs.
_BY_INDUSTRY = {
    # 01-09 agriculture, forestry, fishing
    "farmproducts": "100 200 700 900 2015 2070 5150",
    "lumberwoodproduction": "800 2400 2421 2430",
    # 10-14 mining
    "otherindustrialmetalsmining": "1000 1090 3330 3341",
    "gold": "1040 6795",  # 6795 mineral royalty traders: Royal Gold & co.
    "thermalcoal": "1220 1221",
    "oilgasep": "1311 6792",
    "oilgasdrilling": "1381",
    "oilgasequipmentservices": "1382 1389 3533",
    "buildingmaterials": "1400 3241 3250 3270 3272 3281 3290",  # 1400: aggregates (Vulcan, Martin Marietta)
    # 15-17 construction
    "residentialconstruction": "1520 1531 2451 2452",
    "engineeringconstruction": "1540 1600 1623 1700 1731 8711",
    # 20-39 manufacturing
    "packagedfoods": "2000 2011 2013 2020 2024 2030 2033 2040 2050 2052 2090 2092",
    "confectioners": "2060",
    "beveragesnonalcoholic": "2080 2086",
    "beveragesbrewers": "2082",
    "beverageswineriesdistilleries": "2084 2085 5180",
    "tobacco": "2100 2111",
    "textilemanufacturing": "2200 2211 2221 2250 2390",
    "apparelmanufacturing": "2253 2300 2320 2330 2340 5130",
    "furnishingsfixturesappliances": "2273 2510 2511 2531 2540 2590 3220 3260 3630 3634 5020",
    "businessequipmentsupplies": "2520 2522 2761 2780 3579 3950",
    "paperpaperproducts": "2600 2611 2621",
    "packagingcontainers": "2631 2650 2670 2673 3080 3081 3086 3089 3221 3411 3412",
    "publishing": "2711 2721 2731 2732 2741 2771",
    "specialtybusinessservices": "2750 2790 3990 6794 7330 7340 7380 7389 8111 8744 8900",  # 7389 also Visa/PayPal
    "chemicals": "2800 2820 2821",
    "specialtychemicals": "2810 2851 2860 2890 2891 3060",
    "drugmanufacturersspecialtygeneric": "2833 2834",  # 2834 cannot tell big pharma from specialty: the commoner one
    "diagnosticsresearch": "2835 3826 8071 8734",
    "biotechnology": "2836 8731",  # 8731 commercial research: mostly small biotechs (CROs land here too)
    "householdpersonalproducts": "2840 2842 2844",
    "agriculturalinputs": "2870",
    "oilgasrefiningmarketing": "2911 2990 5171 5172",  # 2911 also holds XOM/CVX (finviz: Oil & Gas Integrated)
    "buildingproductsequipment": "2950 3211 3231 3430 3433 3442 3448 3585 3822",
    "autoparts": "3011 3714 5010 5013",
    "footwearaccessories": "3021 3140 3960",
    "specialtyindustrialmachinery": "3050 3510 3540 3541 3550 3555 3560 3561 3562 3564 3567 3569 3580 3590",
    "luxurygoods": "3100 3873 3910 3911 5094 5944",
    "steel": "3310 3312 3317 3320 5050 5051",
    "aluminum": "3334 3350",
    "electricalequipmentparts": "3357 3600 3612 3613 3620 3621 3640 3690",
    "metalfabrication": "3360 3390 3440 3443 3444 3451 3452 3460 3470 3490",
    "toolsaccessories": "3420 3524",
    "aerospacedefense": "3480 3720 3721 3724 3728 3730 3760 3812",
    "farmheavyconstructionmachinery": "3523 3530 3531 3532 3537 3713 3715",
    "semiconductorequipmentmaterials": "3559",  # 3559 special industry machinery NEC: Lam, Axcelis, Veeco
    "computerhardware": "3570 3571 3572 3575 3577 3578",
    "communicationequipment": "3576 3661 3663 3669",
    "consumerelectronics": "3651 3861",
    "entertainment": "3652 7812 7819 7822 7829 7830 7841 7900",
    "electroniccomponents": "3670 3672 3677 3678 3679 3695",
    "semiconductors": "3674",
    "automanufacturers": "3711",
    "recreationalvehicles": "3716 3751 3790",
    "railroads": "3743 4011 4013",
    "scientifictechnicalinstruments": "3821 3823 3824 3825 3827 3829",
    "medicaldevices": "3841 3842 3844 3845",
    "medicalinstrumentssupplies": "3843 3851",
    "leisure": "3931 3942 3944 3949 7997",
    # 40-49 transportation, communications, utilities
    "travelservices": "4100 4700",
    "trucking": "4210 4213 4231",  # 4210 also holds UPS (finviz: Integrated Freight & Logistics)
    "integratedfreightlogistics": "4220 4513 4731",
    "marineshipping": "4400 4412",
    "airlines": "4512 4522",
    "airportsairservices": "4581",
    "oilgasmidstream": "4610 4922",
    "telecomservices": "4812 4813 4822 4841 4899 7385",
    "broadcasting": "4832 4833",
    "utilitiesdiversified": "4900 4931 4932 4961",
    "utilitiesregulatedelectric": "4911",
    "utilitiesregulatedgas": "4923 4924",
    "utilitiesregulatedwater": "4941",
    "wastemanagement": "4950 4953 4955",
    "utilitiesindependentpowerproducers": "4991",
    # 50-59 trade
    "industrialdistribution": "5000 5030 5031 5040 5063 5070 5072 5080 5082 5084 5090 5099 5110 5160 5190",
    "electronicscomputerdistribution": "5045 5064 5065",
    "medicaldistribution": "5047 5122",
    "fooddistribution": "5140 5141",
    "homeimprovementretail": "5200 5211",
    "specialtyretail": "5271 5412 5531 5700 5712 5731 5734 5735 5900 5940 5945 5990",
    "departmentstores": "5311",
    "discountstores": "5331 5399",
    "grocerystores": "5400 5411",
    "autotruckdealerships": "5500",
    "apparelretail": "5600 5621 5651 5661",
    "restaurants": "5810 5812",
    "pharmaceuticalretailers": "5912",
    "internetretail": "5960 5961",
    # 60-67 finance, insurance, real estate
    "banksregional": "6021 6022 6029 6035 6036",  # 6021 also holds JPM/BAC/C/WFC (finviz: Banks - Diversified)
    "creditservices": "6099 6141 6153 6159 6172 6199",  # 6199 finance services: AmEx & co.
    "mortgagefinance": "6111 6162 6163",
    "assetmanagement": "6189 6282 6799",
    "financialdatastockexchanges": "6200",  # 6200: CME, ICE, Nasdaq, Cboe
    "capitalmarkets": "6211 6221",
    "insurancelife": "6311 6321",
    "healthcareplans": "6324",
    "insurancepropertycasualty": "6331",
    "insurancespecialty": "6351 6361",
    "insurancediversified": "6399",
    "insurancebrokers": "6411",
    "realestatediversified": "6500 6510 6512 6513 6519",
    "realestateservices": "6531",
    "realestatedevelopment": "6532 6552",
    "shellcompanies": "6770 9995",
    "reitdiversified": "6798",
    # 70-89 services
    "lodging": "7000 7011",
    "personalservices": "7200 7384 7500 7600 8300 8351 8600",
    "advertisingagencies": "7310 7311 7331",
    "consultingservices": "7320 8700 8741 8742",  # 7320 credit reporting: Equifax, TransUnion
    "rentalleasingservices": "7350 7359 7377 7510",
    "staffingemploymentservices": "7361 7363",
    "internetcontentinformation": "7370",  # 7370 holds Alphabet, Meta, Snap, Pinterest
    "informationtechnologyservices": "7371 7373 7374",
    "softwareapplication": "7372",
    "securityprotectionservices": "7381",
    "gambling": "7948 7990",
    "medicalcarefacilities": "8000 8011 8050 8051 8060 8062 8082 8090 8093",
    "educationtrainingservices": "8200",
}
SIC_INDUSTRY = {int(c): ind for ind, codes in _BY_INDUSTRY.items() for c in codes.split()}

# (SIC codes, name regex, industry): the few SIC codes a company name can split; first match wins
_REFINE = [
    ({6798}, r"MORTGAGE", "reitmortgage"),
    ({6798}, r"HEALTH ?CARE|MEDICAL", "reithealthcarefacilities"),
    ({6798}, r"\bOFFICE", "reitoffice"),
    ({6798}, r"\bINDUSTRIAL|LOGISTICS", "reitindustrial"),
    ({6798}, r"RESIDENTIAL|APARTMENT|\bHOMES\b", "reitresidential"),
    ({6798}, r"\bRETAIL|SHOPPING|\bMALLS?\b", "reitretail"),
    ({6798}, r"HOTEL|HOSPITALITY|LODGING", "reithotelmotel"),
    ({1040}, r"^(?!.*GOLD).*SILVER", "silver"),
    ({1000, 1090}, r"URANIUM", "uranium"),
    ({1000, 1090}, r"COPPER", "copper"),
    ({4911, 4931, 4991}, r"SOLAR|RENEWABLE|\bWIND\b", "utilitiesrenewable"),
    ({2834}, r"BIO|THERAPEUTICS", "biotechnology"),  # finviz classes most such small caps as Biotechnology
    ({7011}, r"CASINO|GAMING|RESORTS", "resortscasinos"),
]


def _code(sic):
    try:
        return int(str(sic).strip())
    except (TypeError, ValueError):
        return None


def classify(sic, name=""):
    """SIC code (int or str) [+ company name] -> (sector, industry), or (None, None) when unknown."""
    code = _code(sic)
    if code is None or code <= 0:
        return None, None
    ind = SIC_INDUSTRY.get(code)
    if ind is None:  # nearest lower code in the same 2-digit major group
        lower = [c for c in SIC_INDUSTRY if c // 100 == code // 100 and c < code]
        if not lower:
            return None, None
        code = max(lower)
        ind = SIC_INDUSTRY[code]
    for codes, pat, refined in _REFINE:
        if code in codes and re.search(pat, (name or "").upper()):
            ind = refined
            break
    return INDUSTRY_SECTOR[ind], ind
