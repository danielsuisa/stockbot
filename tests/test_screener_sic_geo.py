"""SIC -> finviz sector/industry crosswalk and EDGAR location -> finviz country slug (offline)."""
import unittest

from bot.screener import geo, sic

INDUSTRIES = set("""shellcompanies advertisingagencies aerospacedefense agriculturalinputs airlines airportsairservices
aluminum apparelmanufacturing apparelretail assetmanagement automanufacturers autoparts autotruckdealerships
banksdiversified banksregional beveragesbrewers beveragesnonalcoholic beverageswineriesdistilleries biotechnology
broadcasting buildingmaterials buildingproductsequipment businessequipmentsupplies capitalmarkets chemicals cokingcoal
communicationequipment computerhardware confectioners conglomerates consultingservices consumerelectronics copper
creditservices departmentstores diagnosticsresearch discountstores drugmanufacturersgeneral
drugmanufacturersspecialtygeneric educationtrainingservices electricalequipmentparts electroniccomponents
electronicgamingmultimedia electronicscomputerdistribution engineeringconstruction entertainment
farmheavyconstructionmachinery farmproducts financialconglomerates financialdatastockexchanges fooddistribution
footwearaccessories furnishingsfixturesappliances gambling gold grocerystores healthcareplans healthinformationservices
homeimprovementretail householdpersonalproducts industrialdistribution informationtechnologyservices
infrastructureoperations insurancebrokers insurancediversified insurancelife insurancepropertycasualty
insurancereinsurance insurancespecialty integratedfreightlogistics internetcontentinformation internetretail leisure
lodging lumberwoodproduction luxurygoods marineshipping medicalcarefacilities medicaldevices medicaldistribution
medicalinstrumentssupplies metalfabrication mortgagefinance oilgasdrilling oilgasep oilgasequipmentservices
oilgasintegrated oilgasmidstream oilgasrefiningmarketing otherindustrialmetalsmining otherpreciousmetalsmining
packagedfoods packagingcontainers paperpaperproducts personalservices pharmaceuticalretailers
pollutiontreatmentcontrols publishing railroads realestatedevelopment realestatediversified realestateservices
recreationalvehicles reitdiversified reithealthcarefacilities reithotelmotel reitindustrial reitmortgage reitoffice
reitresidential reitretail reitspecialty rentalleasingservices residentialconstruction resortscasinos restaurants
scientifictechnicalinstruments securityprotectionservices semiconductorequipmentmaterials semiconductors silver
softwareapplication softwareinfrastructure solar specialtybusinessservices specialtychemicals
specialtyindustrialmachinery specialtyretail staffingemploymentservices steel telecomservices textilemanufacturing
thermalcoal tobacco toolsaccessories travelservices trucking uranium utilitiesdiversified
utilitiesindependentpowerproducers utilitiesregulatedelectric utilitiesregulatedgas utilitiesregulatedwater
utilitiesrenewable wastemanagement""".split())


class Taxonomy(unittest.TestCase):
    def test_sectors(self):
        self.assertEqual(len(sic.SECTORS), 11)
        self.assertTrue(set(sic.INDUSTRY_SECTOR.values()) <= set(sic.SECTORS))
        self.assertEqual(set(sic.INDUSTRY_SECTOR.values()), set(sic.SECTORS))

    def test_industries_exact(self):
        self.assertEqual(set(sic.INDUSTRY_SECTOR), INDUSTRIES)

    def test_sic_table(self):
        self.assertGreaterEqual(len(sic.SIC_INDUSTRY), 400)
        self.assertTrue(set(sic.SIC_INDUSTRY.values()) <= INDUSTRIES)
        self.assertTrue(all(100 <= c <= 9999 for c in sic.SIC_INDUSTRY))

    def test_sector_conventions(self):
        for ind, sec in [("semiconductors", "technology"), ("banksregional", "financial"),
                         ("reitretail", "realestate"), ("biotechnology", "healthcare"), ("oilgasep", "energy"),
                         ("gold", "basicmaterials"), ("shellcompanies", "financial"), ("conglomerates", "industrials"),
                         ("telecomservices", "communicationservices"),
                         ("internetcontentinformation", "communicationservices"),
                         ("internetretail", "consumercyclical"), ("packagedfoods", "consumerdefensive"),
                         ("utilitiesregulatedelectric", "utilities"), ("solar", "technology"),
                         ("thermalcoal", "energy"), ("uranium", "energy")]:
            self.assertEqual(sic.INDUSTRY_SECTOR[ind], sec, ind)


class Classify(unittest.TestCase):
    def test_spot(self):
        for code, want in [(3674, ("technology", "semiconductors")), (2836, ("healthcare", "biotechnology")),
                           (6022, ("financial", "banksregional")), (6021, ("financial", "banksregional")),
                           (1311, ("energy", "oilgasep")), (6770, ("financial", "shellcompanies")),
                           (7372, ("technology", "softwareapplication")),
                           (4911, ("utilities", "utilitiesregulatedelectric")), (1040, ("basicmaterials", "gold")),
                           (3711, ("consumercyclical", "automanufacturers")), (6311, ("financial", "insurancelife")),
                           (6331, ("financial", "insurancepropertycasualty")), (6211, ("financial", "capitalmarkets")),
                           (6282, ("financial", "assetmanagement")), (5961, ("consumercyclical", "internetretail")),
                           (7011, ("consumercyclical", "lodging")), (5812, ("consumercyclical", "restaurants")),
                           (2834, ("healthcare", "drugmanufacturersspecialtygeneric")),
                           (6798, ("realestate", "reitdiversified"))]:
            self.assertEqual(sic.classify(code), want, code)

    def test_input_forms(self):
        want = ("technology", "semiconductors")
        self.assertEqual(sic.classify("3674"), want)
        self.assertEqual(sic.classify(" 3674 "), want)
        self.assertEqual(sic.classify("0100"), ("consumerdefensive", "farmproducts"))
        for bad in (None, "", "abc", "  ", 0, "-5", 8880, 9721):
            self.assertEqual(sic.classify(bad), (None, None), bad)

    def test_fallback_nearest_lower_same_group(self):
        self.assertEqual(sic.classify(3675), sic.classify(3674))  # not in the list -> 3674
        self.assertEqual(sic.classify(2899), sic.classify(2891))
        self.assertEqual(sic.classify(7379), sic.classify(7377))
        self.assertEqual(sic.classify(3500), (None, None))  # nothing lower in major group 35
        self.assertEqual(sic.classify(9999), sic.classify(9995))

    def test_name_refinement(self):
        self.assertEqual(sic.classify(6798, "REALTY INCOME CORP"), ("realestate", "reitdiversified"))
        self.assertEqual(sic.classify(6798, "XYZ Mortgage Trust")[1], "reitmortgage")
        self.assertEqual(sic.classify(6798, "Boston Properties Office REIT")[1], "reitoffice")
        self.assertEqual(sic.classify(6798, "Welltower Healthcare Inc")[1], "reithealthcarefacilities")
        self.assertEqual(sic.classify(6798, "Industrial Logistics Properties Trust")[1], "reitindustrial")
        self.assertEqual(sic.classify(6798, "American Homes 4 Rent")[1], "reitresidential")
        self.assertEqual(sic.classify(6798, "Simon Property Group Malls")[1], "reitretail")
        self.assertEqual(sic.classify(6798, "Small Cap Realty")[1], "reitdiversified")  # MALL needs a word boundary
        self.assertEqual(sic.classify(6798, "Host Hotels & Resorts")[1], "reithotelmotel")
        self.assertEqual(sic.classify(1040, "Pan American Silver Corp")[1], "silver")
        self.assertEqual(sic.classify(1040, "Gold and Silver Mining Co")[1], "gold")
        self.assertEqual(sic.classify(4911, "NextEra Renewable Energy"), ("utilities", "utilitiesrenewable"))
        self.assertEqual(sic.classify(2834, "Acme Therapeutics Inc"), ("healthcare", "biotechnology"))
        self.assertEqual(sic.classify(2834, "Pfizer Inc")[1], "drugmanufacturersspecialtygeneric")
        self.assertEqual(sic.classify(7011, "Wynn Resorts Ltd")[1], "resortscasinos")
        self.assertEqual(sic.classify(1090, "Energy Fuels Uranium"), ("energy", "uranium"))
        self.assertEqual(sic.classify(6798, None)[1], "reitdiversified")


class Geo(unittest.TestCase):
    def test_edgar_table(self):
        self.assertEqual(geo.EDGAR["CA"], "usa")
        self.assertEqual(geo.EDGAR["PR"], "usa")
        self.assertEqual(geo.EDGAR["A0"], "canada")
        for code, want in [("K3", "hongkong"), ("L3", "israel"), ("E9", "caymanislands"), ("D0", "bermuda"),
                           ("F4", "china"), ("F5", "taiwan"), ("M5", "southkorea"), ("X0", "unitedkingdom"),
                           ("C0", "unitedarabemirates"), ("1Z", "russia"), ("Q1", "vietnam"), ("1P", "kazakhstan"),
                           ("D8", "britishvirginislands"), ("L2", "ireland"), ("C9", "belgium"), ("D5", "brazil"),
                           ("C3", "australia"), ("T3", "southafrica"), ("Q2", "newzealand")]:
            self.assertEqual(geo.EDGAR[code], want, code)
        self.assertNotIn("unitedstates", geo.EDGAR.values())
        self.assertTrue(all(v and v.isalpha() and v.islower() for v in geo.EDGAR.values()))

    def test_slug(self):
        for name, want in [("UNITED KINGDOM", "unitedkingdom"), ("England", "unitedkingdom"),
                           ("GREAT BRITAIN", "unitedkingdom"), ("KOREA, REPUBLIC OF", "southkorea"),
                           ("South Korea", "southkorea"), ("CAYMAN ISLANDS", "caymanislands"),
                           ("HONG KONG", "hongkong"), ("PEOPLE'S REPUBLIC OF CHINA", "china"),
                           ("TAIWAN, PROVINCE OF CHINA", "taiwan"), ("UNITED ARAB EMIRATES", "unitedarabemirates"),
                           ("VIRGIN ISLANDS, BRITISH", "britishvirginislands"), ("RUSSIAN FEDERATION", "russia")]:
            self.assertEqual(geo.slug(name), want, name)
        self.assertIsNone(geo.slug(""))
        self.assertIsNone(geo.slug(None))

    def test_country(self):
        self.assertEqual(geo.country({"business": {"stateOrCountry": "CA"}}), "usa")
        self.assertEqual(geo.country({"business": {"stateOrCountry": "CA", "stateOrCountryDescription": "CA",
                                                   "isForeignLocation": 0}}), "usa")
        self.assertEqual(geo.country({"business": {"stateOrCountry": "K3",
                                                   "stateOrCountryDescription": "HONG KONG"}}), "hongkong")
        self.assertEqual(geo.country({"business": {"stateOrCountry": "L3"}}, "DE"), "israel")
        self.assertEqual(geo.country({}, "E9"), "caymanislands")
        self.assertEqual(geo.country(None, " e9 "), "caymanislands")
        self.assertEqual(geo.country({"business": {"stateOrCountry": None, "stateOrCountryDescription": "Singapore",
                                                   "isForeignLocation": 1}}), "singapore")
        self.assertEqual(geo.country({"business": {"country": "Greece"}}), "greece")
        self.assertEqual(geo.country({"business": {"stateOrCountry": None}, "mailing": {"stateOrCountry": "X0"}}),
                         "unitedkingdom")
        self.assertEqual(geo.country({"business": {"stateOrCountry": "XX"}}, "D0"), "bermuda")

    def test_nothing(self):
        self.assertIsNone(geo.country({}))
        self.assertIsNone(geo.country(None, None))
        self.assertIsNone(geo.country({"business": {"stateOrCountry": None, "stateOrCountryDescription": None},
                                       "mailing": {}}, ""))
        self.assertIsNone(geo.country({"business": {"stateOrCountry": "XX",
                                                    "stateOrCountryDescription": "UNKNOWN"}}))


if __name__ == "__main__":
    unittest.main()
