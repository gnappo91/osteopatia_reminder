# app.py
import streamlit as st
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo  # pro-level: uses stdlib zoneinfo (Python 3.9+)
import os, re, json
import phonenumbers  # optional but recommended to normalize numbers
from pdb import set_trace
from typing import Optional, Dict, Any

# Google libs
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import Flow

# Config / scopes
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/contacts.readonly"
]
CREDENTIALS_FILE = os.path.join("tmp","credentials_web.json")   # downloaded from Google Cloud Console
TOKEN_FILE = os.path.join("tmp", "token.json")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")

BASE_URL = os.getenv("BASE_URL")  # <- set to your deployed domain
REDIRECT_PATH = "/oauth2callback"         # or "/" if you prefer
REDIRECT_URI = f"{BASE_URL}{REDIRECT_PATH}"

# ---- Helpers ----

def _ensure_token_dir_exists(path: str):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)

def save_token(creds: Credentials, path: str = TOKEN_FILE):
    """
    Persist credentials to disk as JSON and set restrictive permissions (owner read/write).
    Use creds.to_json() to preserve refresh token, expiry, etc.
    """
    _ensure_token_dir_exists(path)
    try:
        # Prefer storing the full JSON from google Creds (keeps format compatible with google libs)
        with open(path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        # Restrict permissions (rw-------)
        try:
            os.chmod(path, 0o600)
        except Exception:
            # Windows or hosted environments may ignore chmod; not fatal
            pass
    except Exception as e:
        # non-fatal: log/ignore but keep running
        print(f"Warning: failed to save token to {path}: {e}")

def load_token(path: str = TOKEN_FILE) -> Optional[Credentials]:
    """
    Load credentials from disk. Returns a google.oauth2.credentials.Credentials or None.
    Handles parsing expiry via your dict_to_creds fallback if needed.
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        # try direct from_authorized_user_info (works for google format)
        try:
            info = json.loads(raw)
        except Exception:
            return None

        # Build Credentials robustly: prefer dict_to_creds (handles expiry parsing)
        creds = dict_to_creds(info) if isinstance(info, dict) else None
        # if creds is None, try constructor fallback
        if creds is None:
            creds = Credentials.from_authorized_user_info(info, scopes=SCOPES)
        return creds
    except Exception as e:
        # token corrupt — remove to avoid repeated failures
        try:
            os.remove(path)
        except Exception:
            pass
        print(f"Warning: could not load token file: {e}")
        return None
    
def load_client_config() -> Dict[str, Any]:

    # Fallback: use a credentials.json file included in repo
    if os.path.exists(CREDENTIALS_FILE):
        with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    raise RuntimeError(
        "Google client config not found. Add it to st.secrets['google'] "
        "(e.g. SERVICE_ACCOUNT_JSON or CLIENT_CONFIG) or provide credentials.json in the repo."
    )


def creds_to_dict(creds: Credentials) -> Dict[str, Any]:
    """Serialize Credentials to a JSON-serializable dict for storage."""
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else None,
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }


def _to_naive_utc(dt: datetime) -> datetime:
    """Return a naive datetime in UTC equivalent to dt."""
    if dt.tzinfo is None:
        return dt  # already naive — assume it's UTC
    # convert to UTC then drop tzinfo
    return dt.astimezone(timezone.utc).replace(tzinfo=None)

def dict_to_creds(d: Dict[str, Any]) -> Credentials:
    """
    Construct a google.oauth2.credentials.Credentials from a stored dict.
    Ensure creds.expiry is naive UTC (no tzinfo) because google-auth compares
    against a naive utcnow(). This avoids naive/aware comparison errors.
    """
    # Try to recreate via library helper first (may or may not set expiry as naive)
    try:
        creds = Credentials.from_authorized_user_info(d, scopes=d.get("scopes"))
    except Exception:
        creds = Credentials(
            token=d.get("token"),
            refresh_token=d.get("refresh_token"),
            token_uri=d.get("token_uri"),
            client_id=d.get("client_id"),
            client_secret=d.get("client_secret"),
            scopes=d.get("scopes"),
        )

    expiry = d.get("expiry")
    if expiry:
        try:
            # Normalize ISO forms: replace trailing Z with +00:00 so fromisoformat can parse it
            if isinstance(expiry, str) and expiry.endswith("Z"):
                expiry_str = expiry[:-1] + "+00:00"
                parsed = datetime.fromisoformat(expiry_str)
            elif isinstance(expiry, str):
                parsed = datetime.fromisoformat(expiry)
            elif isinstance(expiry, datetime):
                parsed = expiry
            else:
                parsed = None

            if parsed:
                # convert to naive UTC to match google-auth expectations
                creds.expiry = _to_naive_utc(parsed)
        except Exception:
            creds.expiry = None

    return creds

# ---- Main flow function ----
def get_google_credentials() -> Optional[Credentials]:
    """
    Return valid Credentials. Strategy:
      1) Try in-memory session cache (st.session_state) — keep for fast reuse during run.
      2) Try token file on disk (load_token) and refresh if needed.
      3) Proceed with OAuth Flow (produces token and saves it).
    """
    # 0) prefer session cache (fast during a single Streamlit run)
    cached = st.session_state.get("_google_creds")
    if isinstance(cached, Credentials):
        if cached.valid or (cached.refresh_token and cached.expired):
            if cached.expired and cached.refresh_token:
                try:
                    cached.refresh(Request())
                except Exception:
                    st.session_state.pop("_google_creds", None)
                else:
                    # persist refreshed token
                    save_token(cached)
                    return cached
            else:
                return cached

    # 1) try disk token
    creds = load_token()
    if creds:
        # if expired and refresh token available, refresh and persist
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                # corrupted/stale refresh token — remove and fall through to interactive auth
                try:
                    os.remove(TOKEN_FILE)
                except Exception:
                    pass
                creds = None
            else:
                save_token(creds)
                st.session_state["_google_creds"] = creds
                return creds
        elif creds.valid:
            st.session_state["_google_creds"] = creds
            return creds

    # 2) interactive OAuth flow (same as your original logic)
    client_config = load_client_config()
    if not any(k in client_config for k in ("web", "installed")):
        client_config = {"web": client_config}
    if REDIRECT_URI is None:
        raise RuntimeError("REDIRECT_URI not configured. Set st.secrets['BASE_URL'] and st.secrets['REDIRECT_PATH'].")

    flow = Flow.from_client_config(client_config=client_config, scopes=SCOPES, redirect_uri=REDIRECT_URI)

    params = st.query_params
    if "error" in params:
        st.experimental_set_query_params()
        st.warning("Google sign-in failed or was cancelled.")
        return None

    if "code" in params:
        code = params["code"][0]
        flow.fetch_token(code=code)
        creds = flow.credentials
        # persist and set restrictive perms
        save_token(creds)
        st.session_state["_google_creds"] = creds
        st.experimental_set_query_params()
        return creds

    # no code -> produce auth url and stop (same UI behavior)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    st.session_state["_oauth_state"] = state
    st.markdown("### Sign in with Google")
    st.write("Click the link below to sign in with Google:")
    st.markdown(f"[Sign in]({auth_url})")
    st.stop()
    return None

def get_tomorrow_bounds(tz_name="Europe/Rome"):
    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()
    tomorrow = today + timedelta(days=1)
    tmin = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0, tzinfo=tz).isoformat()
    tmax = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 23, 59, 59, tzinfo=tz).isoformat()
    return tmin, tmax

def sanitize_phone(raw: str, default_region="IT"):
    # try phonenumbers first
    try:
        p = phonenumbers.parse(raw, default_region)
        if phonenumbers.is_possible_number(p) and phonenumbers.is_valid_number(p):
            return phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164)
    except Exception:
        pass
    # fallback: remove non-digits and ensure leading +
    digits = re.sub(r"[^\d+]", "", raw)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if digits and not digits.startswith("+"):
        # WARNING: naive; better to ensure contacts have country codes
        digits = "+" + digits
    return digits

def search_contacts_by_name(people_service, name):
    # warmup (People API suggests a warmup empty request to improve cache)
    try:
        people_service.people().searchContacts(query="", pageSize=1, readMask="names,phoneNumbers").execute()
    except Exception:
        pass
    people_service.people().connections().list(resourceName="people/me",pageSize=200,pageToken=None,personFields="names,emailAddresses,phoneNumbers,metadata").execute()
    resp = people_service.people().searchContacts(query=name, pageSize=10, readMask="names,phoneNumbers").execute()
    if not resp.get("results", []):
        return
    person = resp.get("results", [])[0]["person"] # Get the first matching contact
    name = person["names"][0].get("displayName")
    phone = person.get("phoneNumbers", [])[0]["value"]
    out = {
            "name": name,
            "phone": phone
        }
    return out

def get_events(creds):
    cal_service = build("calendar", "v3", credentials=creds)

    tmin, tmax = get_tomorrow_bounds("Europe/Rome")
    events_result = cal_service.events().list(
        calendarId=GOOGLE_CALENDAR_ID,
        timeMin=tmin,
        timeMax=tmax,
        singleEvents=True,
        orderBy="startTime"
    ).execute()
    events = events_result.get("items", [])

    if len(events)==0:
        print("Non ho trovato nessun appuntamento per domani")
        return
    else:
        failures = []

        people_service = build("people", "v1", credentials=creds)
        appointments = []
        for ev in events:
            event_start = ev['start'].get('dateTime', ev['start'].get('date'))
            summary = ev.get("summary", "")
            print(f"Event: **{summary}**")
            found_contact = search_contacts_by_name(people_service, summary)
            if not found_contact:
                print(f"Nessun telefono associato all'evento '{summary}'")
                continue
            phone_raw = found_contact["phone"]
            phone_e164 = sanitize_phone(phone_raw)
            if not phone_e164 or len(phone_e164) < 6:
                failures.append((summary, phone_raw, "bad_format"))
                print(f"   ✖ bad phone format: {phone_raw}")
                continue
            appointments.append({"name":found_contact["name"],"start":event_start,"phone":phone_e164})
        return appointments

def fetch_events():
    # DEBUG: list calendars and events per calendar
    st.header("Debug: Calendars & events (raw)")

    # build cal_service as you already do
    creds = get_google_credentials()
    cal_service = build("calendar", "v3", credentials=creds)

    # list calendars visible to the user
    st.subheader("User's calendars")
    cal_list = cal_service.calendarList().list().execute()
    calendars = cal_list.get("items", [])
    st.write(f"Found {len(calendars)} calendars in calendarList.")
    for cal in calendars:
        st.markdown(f"**Calendar summary:** {cal.get('summary')} — id: `{cal.get('id')}`")
        # show access role for debugging
        st.write("AccessRole:", cal.get("accessRole"))
        st.write("Primary?:", cal.get("primary", False))

    # now inspect events on each calendar for the same bounds
    tmin, tmax = get_tomorrow_bounds("Europe/Rome")
    st.write("Query bounds:", tmin, "→", tmax)

    for cal in calendars:
        cid = cal.get("id")
        try:
            ev_resp = cal_service.events().list(
                calendarId=cid,
                timeMin=tmin,
                timeMax=tmax,
                singleEvents=True,
                orderBy="startTime",
                maxResults=50
            ).execute()
        except Exception as e:
            st.write(f"Error fetching events for calendar {cid}: {e}")
            continue

        items = ev_resp.get("items", [])
        st.write(f"Calendar `{cal.get('summary')}` ({cid}) → {len(items)} events")
        if items:
            # show full event objects so we can inspect start fields
            st.json(items)
        else:
            st.write("No events returned. (If you expect events here, check the Calendar UI and the calendar id above.)")