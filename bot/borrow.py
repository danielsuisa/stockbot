"""IBKR's public short-borrow file (free: ftp3.interactivebrokers.com, user "shortstock", file usa.txt): the annual
fee rate and the shares available to borrow for US stocks, refreshed during the day. The squeeze list shows it as
unvalidated - there is no free history to backtest it."""
import ftplib
import io

HOST, USER, FILE = "ftp3.interactivebrokers.com", "shortstock", "usa.txt"


def parse(text):
    """usa.txt -> {ticker (SEC spelling): {"fee": annual % or None, "avail": shares or None, "more": bool}}; "more"
    marks IBKR's ">10000000" (at least that many). Columns come from the #SYM header; USD lines only."""
    head, out = None, {}
    for line in text.splitlines():
        if line.startswith("#SYM"):
            head = line[1:].split("|")
            continue
        if head is None or not line.strip() or line.startswith("#"):
            continue
        f = dict(zip(head, line.split("|")))
        sym = (f.get("SYM") or "").strip().upper().replace(" ", "-")  # IBKR writes class shares as "BRK B"
        if not sym or f.get("CUR") != "USD":
            continue
        avail = (f.get("AVAILABLE") or "").strip()
        try:
            n = int(avail.lstrip(">"))
        except ValueError:
            n = None
        try:
            fee = float(f.get("FEERATE") or "")
        except ValueError:
            fee = None
        out[sym] = {"fee": fee, "avail": n, "more": avail.startswith(">")}
    return out


def fetch(timeout=30):
    """All of usa.txt, parsed; {} when the FTP server cannot be reached (the list then shows borrow as missing)."""
    buf = io.BytesIO()
    try:
        ftp = ftplib.FTP(HOST, timeout=timeout)
        ftp.login(USER, "")
        ftp.retrbinary(f"RETR {FILE}", buf.write)
        ftp.quit()
    except (OSError, EOFError, ftplib.Error) as e:
        print(f"ibkr borrow file: {type(e).__name__} {e}")
        return {}
    return parse(buf.getvalue().decode("utf-8", "replace"))
