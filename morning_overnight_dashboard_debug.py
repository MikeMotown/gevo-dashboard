
import requests
import pandas as pd
import streamlit as st
from datetime import datetime, date, time as dtime, timedelta
from zoneinfo import ZoneInfo
from dateutil import parser as dateparser

st.set_page_config(page_title="GEVO – Morning & Overnight Signal (Diag)", page_icon="📈", layout="wide")

st.sidebar.title("🔑 Indstillinger")
API_KEY = st.sidebar.text_input("EODHD API key", type="password", help="Indsæt din EODHD nøgle her")
if not API_KEY and "EODHD_API_KEY" in st.secrets:
    API_KEY = st.secrets["EODHD_API_KEY"]

SYMBOL = st.sidebar.text_input("Ticker (EODHD format)", value="GEVO.US")
INTERVAL = st.sidebar.selectbox("Intradag interval", ["5m", "1m", "1h"], index=0)

dk_tz = ZoneInfo("Europe/Copenhagen")
et_tz = ZoneInfo("America/New_York")
now_dk = datetime.now(dk_tz)
now_et = now_dk.astimezone(et_tz)

st.sidebar.markdown(f"**Nu (DK):** {now_dk:%Y-%m-%d %H:%M}  \n**Nu (ET):** {now_et:%Y-%m-%d %H:%M}")
st.sidebar.markdown("### 📏 Regler og filtre")
GAP_LIMIT = st.sidebar.slider("Gap-grænse (Morning): > -x %", min_value=-5.0, max_value=0.0, value=-1.0, step=0.1)
VOLA_LIMIT = st.sidebar.slider("Vola-grænse (Overnight): < x %", min_value=1.0, max_value=10.0, value=4.0, step=0.5)
NEWS_WINDOW_H = st.sidebar.slider("Nyhedsvindue (timer)", min_value=6, max_value=24, value=12, step=1)

MORNING_INFO = "Morning Pop: 15:30 → 16:10 (DK tid)"
OVERNIGHT_INFO = "Overnight: 21:50 → (næste dag) 16:00 (DK tid) – kun mandag & fredag"

def to_ts(dt_obj):
    return int(dt_obj.timestamp())

def fetch_intraday(symbol, api_key, interval, day_et: date):
    start = datetime.combine(day_et, dtime(9, 30), tzinfo=et_tz)
    end = datetime.combine(day_et, dtime(16, 0), tzinfo=et_tz)
    if day_et == now_et.date():
        end = now_et

    url = (f"https://eodhd.com/api/intraday/{symbol}?api_token={api_key}"
           f"&interval={interval}&from={int(start.timestamp())}&to={int(end.timestamp())}&fmt=json")
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        st.error(f"Intradag API-fejl: {e}")
        try:
            st.caption(f"Rå svar (første 200 tegn): {r.text[:200]}")
        except Exception:
            pass
        return None

    if not isinstance(data, list) or len(data) == 0:
        st.warning("Intradag-kald returnerede 0 rækker.")
        return None

    df = pd.DataFrame(data)
    if "t" in df:
        idx = pd.to_datetime(df["t"], unit="s", utc=True).tz_convert(et_tz)
        df.index = idx
        colmap = {"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}
        df = df.rename(columns={k: v for k, v in colmap.items() if k in df.columns})
    elif "datetime" in df:
        idx = pd.to_datetime(df["datetime"], utc=True).tz_convert(et_tz)
        df.index = idx
    else:
        st.error("Ukendt intradag-format (mangler 't' eller 'datetime').")
        return None

    need = {"Open", "High", "Low", "Close"}
    if not need.issubset(df.columns):
        st.error(f"Mangler kolonner i intradag: {need - set(df.columns)}")
        return None

    df = df[(df.index.weekday < 5) & (df.index.time >= dtime(9, 30)) & (df.index.time <= dtime(16, 0))]
    df = df.sort_index()

    if df.empty:
        st.warning("Intradag efter filtrering er tom (måske før åbning eller interval ikke dækket af din plan).")
        return None

    return df

def fetch_eod(symbol, api_key, from_date: str, to_date: str):
    url = f"https://eodhd.com/api/eod/{symbol}?api_token={api_key}&from={from_date}&to={to_date}&fmt=json"
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        st.error(f"EOD API-fejl: {e}")
        try:
            st.caption(f"Rå EOD-svar (første 200 tegn): {r.text[:200]}")
        except Exception:
            pass
        return None
    if not isinstance(data, list) or len(data) == 0:
        st.warning("EOD-kald returnerede 0 rækker.")
        return None
    df = pd.DataFrame(data)
    if "date" not in df.columns:
        st.error("EOD-data mangler 'date'-kolonne.")
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date")

def fetch_fundamentals(symbol, api_key):
    url = f"https://eodhd.com/api/fundamentals/{symbol}?api_token={api_key}"
    try:
        r = requests.get(url, timeout=60)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None

def next_earnings_within_days(fund_json, now_et_date: date, days: int = 2):
    if not isinstance(fund_json, dict):
        return None
    upcoming = None
    try:
        if "Earnings" in fund_json and isinstance(fund_json["Earnings"], dict) and "Date" in fund_json["Earnings"]:
            upcoming = fund_json["Earnings"]["Date"]
        elif "General" in fund_json and isinstance(fund_json["General"], dict) and "EarningsDate" in fund_json["General"]:
            upcoming = fund_json["General"]["EarningsDate"]
    except Exception:
        pass
    if isinstance(upcoming, str):
        try:
            upcoming = dateparser.parse(upcoming).date()
        except Exception:
            upcoming = None
    if isinstance(upcoming, date):
        return (upcoming - now_et_date).days <= days
    return None

def fetch_recent_news(symbol, api_key, end_et_dt, hours=12):
    start_et = end_et_dt - timedelta(hours=hours)
    url = f"https://eodhd.com/api/news?s={symbol}&api_token={api_key}&from={start_et.date()}&to={end_et_dt.date()}"
    try:
        r = requests.get(url, timeout=60)
        if r.status_code != 200:
            return []
        items = r.json()
    except Exception:
        return []
    out = []
    if isinstance(items, list):
        for it in items:
            ts = it.get("date") or it.get("publishedAt") or it.get("time")
            if not ts:
                continue
            try:
                t = dateparser.parse(ts)
            except Exception:
                continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=ZoneInfo("UTC")).astimezone(et_tz)
            else:
                t = t.astimezone(et_tz)
            if start_et <= t <= end_et_dt:
                out.append({"time": t, "title": it.get("title", "(no title)")})
    return out

def compute_gap_ok(today_first_close, prev_close, gap_limit=-1.0):
    if prev_close is None or prev_close <= 0 or today_first_close is None:
        return None, None
    gap_pct = (today_first_close / prev_close - 1.0) * 100.0
    return gap_pct, (gap_pct > gap_limit)

if not API_KEY:
    st.info("Indsæt din EODHD API‑nøgle i venstre side eller tilføj den som secret 'EODHD_API_KEY' i Streamlit Cloud.")
    st.stop()

today_et = now_et.date()
intra_today = fetch_intraday(SYMBOL, API_KEY, INTERVAL, today_et)
eod_recent = fetch_eod(SYMBOL, API_KEY, (today_et - timedelta(days=10)).isoformat(), today_et.isoformat())
fundamentals = fetch_fundamentals(SYMBOL, API_KEY)
news_list = fetch_recent_news(SYMBOL, API_KEY, now_et, hours=NEWS_WINDOW_H)

with st.expander("🔍 Diagnose (klik for detaljer)"):
    st.write("Dato (ET):", now_et.strftime("%Y-%m-%d %H:%M"))
    st.write("API key sat:", bool(API_KEY))
    st.write("Ticker:", SYMBOL, "Interval:", INTERVAL)

    if intra_today is not None and not intra_today.empty:
        st.write("Intradag rækker:", len(intra_today))
        st.write("Første bar:", intra_today.index.min().strftime("%Y-%m-%d %H:%M ET"))
        st.write("Seneste bar:", intra_today.index.max().strftime("%Y-%m-%d %H:%M ET"))
        st.write(intra_today.tail(3))
    else:
        st.write("Intradag: TOM")

    if eod_recent is not None and not eod_recent.empty:
        st.write("EOD rækker (seneste 10 dage):", len(eod_recent))
        st.write("Seneste EOD-dato:", pd.to_datetime(eod_recent['date']).max().strftime("%Y-%m-%d"))
        st.write(eod_recent.tail(3))
    else:
        st.write("EOD: TOM")

    st.write("Nyheder i vindue:", len(news_list) if isinstance(news_list, list) else "ukendt")

st.subheader(f"🌅 {MORNING_INFO}")
if intra_today is None or intra_today.empty or eod_recent is None or eod_recent.empty:
    st.warning("Ingen intradag/EOD data nok til Morning-vurdering endnu.")
else:
    first_bar = intra_today.iloc[0]
    green_open = bool(first_bar["Close"] > first_bar["Open"])
    prev_day = (intra_today.index[0] - timedelta(days=1)).date()
    intra_prev = fetch_intraday(SYMBOL, API_KEY, INTERVAL, prev_day)
    prev_close = None
    if intra_prev is not None and not intra_prev.empty:
        prev_close = float(intra_prev.iloc[-1]["Close"])
    else:
        row = eod_recent[eod_recent["date"] == pd.to_datetime(prev_day)]
        if not row.empty:
            prev_close = float(row.iloc[-1]["close"])
    gap_pct, gap_ok = compute_gap_ok(float(first_bar["Close"]), prev_close, GAP_LIMIT)
    st.write("DEBUG – green_open:", green_open, "gap_pct:", gap_pct, "gap_ok:", gap_ok, "prev_close:", prev_close)
    if green_open and (gap_ok is True):
        st.success("KØB MORNING POP ✅")
    else:
        st.error("VENT – Morning-kriterier ikke opfyldt ❌")
    c1, c2, c3 = st.columns(3)
    c1.metric("Åbningsbar grøn?", "Ja" if green_open else "Nej")
    c2.metric("Gap vs. i går (15:30)", f"{gap_pct:.2f}%" if gap_pct is not None else "ukendt")
    c3.metric("Gap-grænse", f"> {GAP_LIMIT:.1f}%")

st.divider()

st.subheader(f"🌙 {OVERNIGHT_INFO}")
if intra_today is None or intra_today.empty:
    st.warning("Ingen intradag-data nok til Overnight-vurdering endnu.")
else:
    day_high = float(intra_today["High"].max())
    day_low = float(intra_today["Low"].min())
    vola_pct = (day_high / day_low - 1.0) * 100.0 if day_low > 0 else None
    earn_soon = next_earnings_within_days(fundamentals, now_et.date(), days=2)
    news_ok = len(news_list) == 0
    vola_ok = (vola_pct is not None) and (vola_pct < VOLA_LIMIT)
    weekday = now_et.strftime("%A")
    overnight_day = weekday in ["Monday", "Friday"]
    allow = overnight_day and vola_ok and (earn_soon is not True) and news_ok
    if overnight_day and allow:
        st.success("OK TIL OVERNIGHT ✅")
    elif overnight_day:
        st.error("IKKE OK TIL OVERNIGHT ❌")
    else:
        st.info("Overnight bruges kun Mandag & Fredag.")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Volatilitet i dag", f"{vola_pct:.2f}%" if vola_pct is not None else "ukendt")
    c2.metric("Vola-grænse", f"< {VOLA_LIMIT:.1f}%")
    c3.metric("Earnings ≤ 2 dage?", "Ja" if earn_soon else "Nej/Ukendt")
    c4.metric("Friske nyheder (seneste vindue)", str(len(news_list)))

st.divider()
st.caption("Alle tider i overskrifter er i dansk tid (CET/CEST). Beregninger sker i New York‑tid (ET) for præcision.")
