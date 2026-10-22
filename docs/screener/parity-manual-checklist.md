# Parity — manual checklist (screens 6–12)

robots.txt disallows `/screener?*` (OBSERVED 2026-10-21), so these filter screens were **not** loaded by
the engine or by the agent (G2). Open each URL in a browser **outside market hours**, when finviz shows
the same last session. Compare the tickers with the engine's list below, then fill in the counts.
Status for each screen stays **PENDING_MANUAL** until that is done.

Engine run: session CLOSED, last completed session 2026-10-21, universe 11544 symbols (5880 without ETFs), generated 2026-10-22 02:09 UTC.

## Screen 6: `cap_small,sh_float_u50,sh_short_o20`

- finviz URL: https://finviz.com/screener.ashx?v=111&f=cap_small,sh_float_u50,sh_short_o20
- Engine matches: **69** with the float follow-up (PR #28: **15**, float = shares outstanding for all of them)
- Float methods of the 69: insiders 54, insiders+5pct 14, outstanding_proxy 1 (13D/13G coverage was complete only
  for the F1 targets when this was generated; the nightly backlog adds the rest)
- Approximated metrics: float, market_cap, short_float
- Engine tickers (2026-10-22 regeneration, same point in time: CLOSED after 2026-10-21, FINRA settlement
  2026-09-30): AEVA, AKTS, ALKT, ANAB, ANDG, AVBP, AVTX, BCAX, BRVE, CBRL, CLYM, COLL, CRI, CTRN, CYPH, DFH, DMAC, ELMT, EROC, EVER, FDMT, GALT, GIII, GLOB, GOLD, GRPN, GSHD, HLLY, HPK, HROW, HYMC, IIIV, INBX, KARD, KLRA, LBRX, MNRO, MPLT, MQ, NGNE, NKTR, NMAX, NNE, NUTX, OXM, PAR, PCRX, PMTS, PVLA, PZZA, QMCO, SCTX, SGMT, SHAZ, SHOE, SKYH, SPRY, SPTX, STAA, STOK, TRVI, UTZ, VITL, WKC, WLFC, WOLF, WYFI, ZBIO, ZURA
- PR #28's tickers: CBRL, CRI, GLOB, GRPN, HROW, INBX, MNRO, NGNE, NKTR, OXM, PAR, PCRX, PZZA, SHOE, VITL

| finviz matches | in both | Jaccard | precision | recall | checked on |
|---|---|---|---|---|---|
| | | | | | |

## Screen 7: `sh_float_u50,sh_short_o20,sh_avgvol_o500,sh_price_o2,sh_relvol_o2,sh_opt_option`

- finviz URL: https://finviz.com/screener.ashx?v=111&f=sh_avgvol_o500,sh_float_u50,sh_opt_option,sh_price_o2,sh_relvol_o2,sh_short_o20
- Engine matches: **6** with the float follow-up (PR #28: **0**)
- Float methods of the 6: insiders+5pct 4, insiders 2
- Approximated metrics: float, short_float
- Note: Squeeze-like. The squeeze's short ratio > 5 is not a finviz filter and is not applied here.
- Engine tickers (2026-10-22 regeneration, same point in time): ALKT, BETR, CYPH, FDMT, IRON, STOK

| finviz matches | in both | Jaccard | precision | recall | checked on |
|---|---|---|---|---|---|
| | | | | | |

## Screen 8: `ta_sma50_pa,ta_sma200_pa,ta_highlow52w_b0to10h`

- finviz URL: https://finviz.com/screener.ashx?v=111&f=ta_highlow52w_b0to10h,ta_sma200_pa,ta_sma50_pa
- Engine matches: **657** (ignored codes: none)
- Approximated metrics: none
- Engine tickers: AAMI, AAPL, ABBV, ABEV, ACCO, ACGL, ADI, ADUS, ADX, AER, AFG, AFL, AFYA, AIG, AII, AIT, AIZ, AJG, AKO.A, ALDF, ALF, ALL, AME, AMG, AMGN, AMRX, AMT, AMTB, AMWL, AMZN, ANF, AOUT, APA, APD, ARHS, ARQT, ARX, ASC, ASH, ASIC, ASO, ATLO, ATRC, AUC, AUDC, AVBC, AWK, AXIN, AXTA, BALL, BAP, BBDO, BBY, BCAL, BDCI, BDL, BDX, BF.A, BFH, BFST, BGC, BGR, BIIB, BIO.B, BJ, BKH, BLCO, BLK, BLUW, BMBL, BME, BMEZ, BNED, BNS, BOTJ, BOW, BP, BPRN, BRK.B, BSET, BSVN, BTE, BUD, BVFL, BWIN, BWLP, BWMN, BZUN, CAH, CAI, CAKE, CAPN, CART, CB, CBFV, CBLL, CBNA, CBNK, CBRL, CBZ, CCEL, CCEP, CCK, CCNE, CCZ, CDNA, CDRO, CDW, CENT, CENTA, CEPO, CET, CEV, CHAR, CHD, CHH, CHPG, CHRD, CHT, CI, CIB, CL, CLAR, CLDT, CLH, CLMB, CM, CNC, CNK, CNO, CNQ, CNXN, COKE, CON, COP, COPL, COR, CORT, COSO, COST, CPAC, CPAY, CPBI, CPHC, CRAQ, CRBG, CRCT, CRON, CRSR, CRT, CRVL, CUB, CVE, CVI, CVLT, CVX, CZFS, CZNC, DAAQ, DAC, DAO, DBX, DCOM, DDI, DDOG, DDS, DEO, DFIN, DGX, DHR, DHX, DINO, DIS, DJP, DK, DLO, DMAA, DNOW, DRDB, DRI, DSGR, DSGX, DSP, DT, DV, DVN, DXC, E, EBAY, EBF, EC, ECL, ECO, ECPG, ED, EDU, EFSI, EG, EGBN, EGHA, EGY, EIG, ELPC, ELV, EMR, ENIC, EOG, EPSN, EQH, EQNR, ESTC, ETSY, EVAC, EVRG, EVTC, EW, EWBC, EXFY, EXLS, EXPD, FACT, FANG, FAST, FBIZ, FBLA, FCBC, FCNCA, FDBC, FDS, FERA, FET, FFIV, FIGS, FIGX, FIZZ, FLXS, FLYW, FMAO, FMX, FNKO, FNLC, FORR, FOSL, FRHC, FROG, FRSH, FSBW, FTS, FTV, GABC, GCT, GEF.B, GFL, GFR, GGB, GHC, GILD, GKOS, GLBE, GMAB, GME, GNE, GPAT, GRMN, GRN, GRNT, GRX, GSAT, GSHR, GTERA, GTLB, GWW, HAE, HAFN, HALO, HBIO, HBNC, HCI, HG, HGTY, HIG, HIPO, HLP, HMC, HNNA, HOG, HPK, HQY, HRTG, HST, HSTM, HTB, HTFL, HUM, HURN, HVMC, HVT, HZO, IBEX, IBN, IBTA, ICFI, ICLR, IDT, IEX, IFF, IHG, IHS, III, ILMN, IMAX, IMO, INDB, INFU, INN, INTA, IOSP, IOT, IOVA, IQV, ITGR, ITUB, ITW, JACS, JAKK, JCE, JCI, JILL, JOE, JOYY, JRSH, JVA, KCHV, KDP, KELYA, KFII, KGEI, KINS, KMI, KMTS, KNSA, KNTK, KO, KOF, KOYN, KRT, KVUE, L, LAMR, LAUR, LCCC, LGND, LH, LIN, LIND, LLY, LNG, LPAA, LQDT, LSBK, LTC, LWAC, LXP, MA, MAR, MATX, MBVI, MCGA, MCK, MCY, MD, MDLZ, MEDP, MEOH, MET, MFC, MG, MGLD, MGRC, MGYR, MKLY, MMM, MMSI, MNST, MO, MORN, MPC, MRK, MRSH, MSCI, MSFT, MSGS, MSI, MSM, MTCH, MTLS, MTN, MTUS, MTW, MUR, NATH, NATL, NBTB, NDAQ, NDSN, NECB, NHC, NIQ, NKSH, NMP, NOMD, NPAC, NPK, NRIX, NSC, NSIT, NTAP, NTB, NTIP, NTNX, NTRA, NUE, NVS, NWE, NWS, NWSA, OABI, OBA, OGE, OGN, OKE, ONCH, OPK, OPRT, ORIC, ORIQ, ORRF, OSCR, OTTR, OVV, OXY, OYSE, PACH, PACS, PAG, PAGP, PAGS, PAII, PARR, PAYC, PAYO, PAYS, PBA, PBFS, PBI, PBR, PBR.A, PCAP, PCB, PCRX, PCTY, PD, PEO, PERF, PFE, PG, PGAC, PGR, PKG, PLBC, PLGO, PLMR, PLTR, PLUS, PM, PMTR, PNTG, PNW, PR, PRAA, PRGS, PRSU, PRTH, PSO, PSX, PTC, QLYS, QQQX, QSEA, QUMS, RAC, RAL, RAMP, RANG, RBRK, RDVT, RDWR, REGN, RELY, REPX, RGA, RGEN, RGR, RGS, RHP, RIGL, RL, RLI, RLJ, RNR, ROKU, ROST, RPRX, RS, RSG, RUSHB, RVSB, RVTY, RXO, SAFT, SAIC, SBC, SBFG, SBH, SCL, SCSC, SDHI, SEIC, SFNC, SG, SGU, SHEL, SHOP, SHOT, SIBN, SIG, SIGI, SJM, SKWD, SLAB, SLDE, SLF, SM, SMWB, SN, SOUL, SPEG, SPNT, SPT, SPXX, SSEA, SSL, SSNC, STEW, STGW, STK, STLN, STNG, SU, SVCC, SWK, SWX, SXC, SXT, SZZL, TACH, TACO, TAK, TALO, TAVI, TBBB, TBCH, TD, TECH, TEN, TENB, THC, THG, TMO, TMP, TNET, TONT, TREX, TRGP, TRMD, TS, TT, TTC, TTE, TVA, TVAI, TW, TX, TXNM, UCIB, UEIC, UGI, UGP, UNM, UNP, URBN, USA, USFD, USPH, UTMD, UTZ, UVE, V, VABK, VAI, VBNK, VEEV, VEON, VET, VLO, VLOS, VLTO, VNME, VRA, VRSN, VRTX, VTMX, VTRS, W, WAB, WABC, WCN, WDS, WEAV, WELL, WENN, WEX, WEYS, WHF, WLY, WLYB, WM, WMB, WMK, WOR, WPP, WRB, WSBK, WSM, WTBA, WTG, WTM, WTRG, WTS, WTW, XERS, XOM, XP, YHNA, ZD, ZETA, ZIM

| finviz matches | in both | Jaccard | precision | recall | checked on |
|---|---|---|---|---|---|
| | | | | | |

## Screen 9: `ta_rsi_os30,cap_midover`

- finviz URL: https://finviz.com/screener.ashx?v=111&f=cap_midover,ta_rsi_os30
- Engine matches: **61** (ignored codes: none)
- Approximated metrics: market_cap
- Engine tickers: ACHR, ALB, ALK, ALM, ARBK, AVAV, AXSM, BAC, BDCZ, BHF, BNL, BYND, CALM, CIFR, CLSK, CMG, CRS, CTVA, CW, CZR, DB, DPC, DRS, DXF, DY, FLEX, FLG, FSK, GPCR, HDL, HESM, HTO, HWM, ICL, IESC, IMVT, INSM, IREN, JOBY, KTOS, LUMN, MARA, MGM, MHO, MOS, MTZ, NOAH, NTST, OKLO, OTF, RADX, RCUS, RIO, RIOT, SCNI, SHG, STKH, STWD, UCTT, UUUU, WAFD

| finviz matches | in both | Jaccard | precision | recall | checked on |
|---|---|---|---|---|---|
| | | | | | |

## Screen 10: `fa_pe_u15,fa_roe_o15,cap_largeover`

- finviz URL: https://finviz.com/screener.ashx?v=111&f=cap_largeover,fa_pe_u15,fa_roe_o15
- Engine matches: **55** (ignored codes: none)
- Approximated metrics: market_cap, pe, roe
- Engine tickers: AA, ACGL, ADBE, AES, AFG, AFL, AGNC, AIZ, ALL, AMG, AMP, APA, BMY, BPOP, CCL, CF, CI, CINF, CTSH, DECK, DINO, EIX, EOG, EWBC, FIS, FSLR, GEN, GL, GS, HIG, HON, INCY, JAZZ, JPM, LDOS, LULU, LVS, MLI, MLM, NEM, NLY, OXY, PGR, PYPL, RJF, RNR, RRC, SOLV, SYF, THC, TROW, TRV, UBER, VZ, ZTS

| finviz matches | in both | Jaccard | precision | recall | checked on |
|---|---|---|---|---|---|
| | | | | | |

## Screen 11: `ta_gap_u-5,sh_avgvol_o1000`

- finviz URL: https://finviz.com/screener.ashx?v=111&f=sh_avgvol_o1000,ta_gap_d5
- Engine matches: **28** (ignored codes: none)
- Approximated metrics: none
- Note: The prompt writes ta_gap_u-5 (under -5%); finviz's own code for a gap down over 5% is ta_gap_d5 (OBSERVED).
- Engine tickers: AIXI, AVEX, CHOW, CJMB, CWD, EDBL, FRTT, GCDT, HCAI, HKPD, HUDI, INLF, ISRG, KNRX, MASK, MDXH, NFLX, NIVF, NWGL, PM, PRSO, RCT, RDHL, RUBI, SAIQ, T, VIVS, XLAB

| finviz matches | in both | Jaccard | precision | recall | checked on |
|---|---|---|---|---|---|
| | | | | | |

## Screen 12: `ta_volatility_wo5,sh_price_5to50`

- finviz URL: https://finviz.com/screener.ashx?v=111&f=sh_price_5to50,ta_volatility_wo5
- Engine matches: **726** (ignored codes: none)
- Approximated metrics: none
- Engine tickers: AADX, AAL, ABCL, ABSI, ABTC, ACCV, ACHV, ACOG, ADIG, ADPT, ADRX, ADSE, ADUR, ADV, AEHL, AEMD, AERO, AERT, AESI, AEVA, AEYE, AGBK, AGCC, AGEN, AGIO, AGMB, AGPU, AI, AIFU, AIIR, AIOS, AIP, AIRO, AIRT, AKTS, AKTX, ALGM, ALIS, ALIT, ALK, ALM, ALMR, ALMS, ALMU, ALOY, AMKR, AMN, AMPL, AMPX, AMRC, AMSC, AMTD, AMWL, ANAB, ANGI, ANL, ANRO, AOSL, AP, APLD, APMD, APPN, APPS, ARCT, ARHS, ARQQ, ARQT, ARTV, ARVN, ASAN, ASBH, ASBP, ASM, ASPN, ASPS, ASST, ASTC, ASTS, ASYS, ATGL, ATII, ATRA, ATTO, AUC, AUR, AURA, AVBP, AVEX, AVLN, AVNW, AVPT, AVR, AVTX, AXIL, AZ, AZTA, BAFN, BAND, BB, BBGI, BBNX, BCAX, BDSX, BEAM, BETA, BETR, BFLY, BHVN, BIOA, BKKT, BKSY, BLMN, BLSH, BLSM, BLZE, BMNR, BNAI, BNC, BNR, BNTC, BOBS, BOT, BRBR, BRCB, BRCC, BRID, BRUN, BRVE, BSP, BTDR, BTGO, BULL, BUUU, BVC, BYND, CABO, CADL, CAI, CAPR, CARL, CBIO, CBLL, CCG, CCOI, CD, CDNL, CENX, CERT, CEVA, CGEM, CHPT, CHRN, CHWM, CIFR, CIX, CLDX, CLF, CLPT, CLSK, CLYM, CMND, CMPS, CNL, COAG, CODI, COE, COHN, COMP, COOK, CORZ, CPIX, CPS, CRD.A, CRML, CRNC, CRT, CRVS, CSIQ, CSQR, CSTL, CTEV, CTNM, CTRN, CTSO, CUE, CURX, CXM, CXW, CYD, DAO, DBI, DCBO, DFH, DHT, DIN, DJT, DMAC, DMRA, DMRC, DNA, DNLI, DOCS, DPC, DRTS, DSGN, DTIL, DUOT, DXC, DXYZ, DYN, EAF, EBS, ECOR, EDRY, EGG, EIKN, ELMT, ELOX, ELVA, ELVN, ELWT, ENPH, ENR, ENTA, EOLS, EPRX, EQPT, ERAS, ERO, EROC, EROK, ETON, ETRA, EUDA, EVC, EVMN, EXOD, FATN, FCEL, FDMT, FENC, FGC, FGI, FIG, FIGR, FIGS, FINW, FIRY, FIVN, FLNC, FLY, FMC, FNGD, FNKO, FOIL, FOXF, FPS, FRNM, FRSH, FRVO, FSLY, FTH, FTRE, FUBO, FUN, FVN, FWDI, FWRD, FWRG, FXHO, GDS, GENB, GENI, GFS, GFUZ, GHRS, GLBE, GLOB, GLSI, GLUE, GLXY, GMTL, GOSS, GPCR, GPGI, GRML, GRND, GRNQ, GRPN, GRRR, GTBP, GTN.A, HAWK, HELP, HIMS, HLIT, HNRG, HPK, HPP, HQ, HQI, HSAI, HSLV, HY, HYMC, IAM, IART, IBRX, IBTA, ICHR, IDR, IE, IMC, IMCR, IMMX, IMNM, IMTX, IMUX, IMXI, INFQ, INIO, INKT, INTT, IONQ, IOT, IOVA, IPX, IREN, IRLA, ISOU, ITG, JACK, JBIO, JG, JMIA, JXG, KARD, KD, KGEI, KLRA, KMTS, KPLT, KRMN, KRRO, KRSA, KSS, KURA, KVYO, KYNB, KYTX, KZIA, LASR, LBRT, LBRX, LCUT, LEE, LFTO, LGIH, LGN, LIFE, LIME, LION, LMND, LNZA, LONA, LPTH, LQDA, LTBR, LTGO, LTRX, LUNR, LVLU, LYEL, LZ, MAAS, MACI, MAN, MARA, MASS, MAZE, MBLY, MBRX, MEI, METC, MF, MGTX, MHH, MITK, MLTX, MLYS, MMED, MMYT, MNRO, MNTN, MOBI, MOVE, MP, MPLT, MRAM, MRVI, MSB, MSGM, MSLE, MTC, MTEX, MYGN, NAKA, NAMS, NAT, NBTX, NCLH, NCNO, NDLS, NEGG, NESR, NEWP, NGNE, NHP, NINE, NIPG, NKTR, NMAX, NN, NNE, NPCE, NRDY, NRXS, NSLR, NTLA, NTRB, NTSK, NTWK, NVAX, NVCT, NVNO, NVTS, NXG, NXGL, NXPL, NXTC, NXTT, OBE, OBIO, OBX, OCC, OCS, OCUL, ODD, ODTX, OEC, OESX, OFRM, OIG, OKLO, OLMA, OMDA, OMER, ONDS, ONEW, ONIT, ONT, OPI, OPRX, OPTX, ORIC, OSS, OTLY, OUST, PAMT, PAR, PARK, PATH, PAYS, PBLS, PBYI, PCLA, PDFS, PED, PEGA, PESI, PGEN, PGY, PHAT, PHOS, PHVS, PICS, PL, PLAY, PLBL, PLRZ, PMI, PMN, POET, PONY, POWI, PPTA, PRCH, PRE, PRHI, PROF, PS, PSQL, PTLE, PTN, PUMP, PURR, PWP, PXS, PYPD, QBTS, QDEL, QMCO, QNT, QTTB, QUBT, QURE, QVCG, QXO, RACD, RAPP, RCEL, RCUS, RDNW, RDW, RDWR, REA, REAL, REAX, REF, RELL, REPL, RFAI, RGC, RGNX, RGTI, RHI, RIBB, RIOT, RLAY, RMIX, RMTI, RNAC, ROCK, ROLR, RPD, RPGL, RRGB, RSI, RSKD, RTB, RTSN, RUM, RUN, RVI, RVLV, RYM, RZAI, S, SAIH, SAIL, SAIQ, SBET, SBMT, SCTX, SCZM, SDGR, SDOT, SECZ, SEDG, SENS, SEPN, SG, SGHT, SGML, SGMT, SGP, SHAZ, SHLS, SI, SILC, SIND, SKIL, SKIN, SKYD, SLDB, SLE, SLS, SLSR, SMCI, SMMT, SMR, SMWB, SNAP, SNWV, SOFI, SOGP, SOPH, SOTK, SPCB, SPIR, SPMC, SPRB, SPT, SPTX, SRPT, SRZN, SSMR, STDN, STEP, STLN, STOK, STRO, STRZ, STTK, SUJA, SUPN, SUPX, SVC, SVCO, SVRN, SWMR, SYM, TACT, TANH, TBCH, TBI, TBN, TCGX, TCX, TDOC, TENB, TEO, TH, TIL, TISI, TITN, TKNO, TLIH, TNGX, TNXP, TOP, TP, TRAX, TRIB, TRLV, TRT, TRVI, TRXB, TSAT, TSSI, TTD, TTRX, TWIN, UEC, UEIC, UFI, ULCC, ULH, UMAC, UPST, URGN, USAR, USAU, USDE, USNA, UUUU, VATE, VCX, VCYT, VECO, VEEE, VELO, VERA, VERX, VG, VHC, VIA, VIAV, VIR, VITL, VKTX, VMAR, VNCE, VOGX, VOR, VOYG, VRA, VRDN, VRM, VSH, VSTM, VTVT, VXX, VYX, WATT, WAVE, WBTN, WEST, WFCF, WGO, WHR, WLTH, WNC, WOLF, WQEY, WRBY, WULF, WW, WYFI, WYY, XE, XERS, XGN, XLO, XNCR, XRPN, XXI, XZO, YARW, YDES, YSS, YSWY, Z, ZBIO, ZETA, ZLAB

| finviz matches | in both | Jaccard | precision | recall | checked on |
|---|---|---|---|---|---|
| | | | | | |

