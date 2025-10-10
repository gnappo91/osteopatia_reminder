# app.py
import streamlit as st
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  # pro-level: uses stdlib zoneinfo (Python 3.9+)
import os, re, json
import phonenumbers  # optional but recommended to normalize numbers
from pdb import set_trace
from typing import Optional, Dict, Any

# Google libs
from google_auth_oauthlib.flow import InstalledAppFlow
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
TOKEN_FILE = "token.json"
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")

BASE_URL = os.getenv("BASE_URL")  # <- set to your deployed domain
REDIRECT_PATH = "/oauth2callback"         # or "/" if you prefer
REDIRECT_URI = f"{BASE_URL}{REDIRECT_PATH}"

# ---- Helpers ----
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


def dict_to_creds(d: Dict[str, Any]) -> Credentials:
    """
    Construct a google.oauth2.credentials.Credentials from a stored dict.
    If expiry present, parse it and set on the credentials object.
    """
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
            creds.expiry = datetime.fromisoformat(expiry)
        except Exception:
            # fallback: ignore expiry if parsing fails
            creds.expiry = None
    return creds

# ---- Main flow function ----
def get_google_credentials() -> Optional[Credentials]:
    """
    Returns a valid google.oauth2.credentials.Credentials or stops the Streamlit run
    after showing the authorization URL.

    Flow:
      - Try to load token.json and return refreshed credentials if needed.
      - Create a Flow and, if code present in query params, exchange code for tokens.
      - Otherwise generate auth url, show link and call st.stop() to prevent downstream code.
    """
    # 0) quick return if already in session_state and valid
    cached = st.session_state.get("_google_creds")
    if isinstance(cached, Credentials):
        if cached.valid or (cached.refresh_token and cached.expired):
            # refresh if expired but refresh token available
            if not cached.valid:
                try:
                    cached.refresh(Request())
                except Exception:
                    # if refresh fails, drop cached and continue to full flow
                    st.session_state.pop("_google_creds", None)
                else:
                    return cached
            else:
                return cached

    # 0b) try token file on disk
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            creds = dict_to_creds(saved) if isinstance(saved, dict) else Credentials.from_authorized_user_info(saved)
            # refresh if expired and refresh_token present
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception:
                    # stale token: remove file and fall through to re-auth
                    try:
                        os.remove(TOKEN_FILE)
                    except Exception:
                        pass
                else:
                    # persist refreshed token
                    try:
                        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                            f.write(creds.to_json())
                    except Exception:
                        pass
                    st.session_state["_google_creds"] = creds
                    return creds
            elif creds.valid:
                st.session_state["_google_creds"] = creds
                return creds
        except Exception:
            # if token file corrupt, remove and continue to new flow
            try:
                os.remove(TOKEN_FILE)
            except Exception:
                pass

    # 1) create flow (web client)
    client_config = load_client_config()

    # Flow.from_client_config expects client_config shaped like {"web": {...}} or {"installed": {...}}
    # If the top-level is one of those keys, pass as-is; otherwise wrap it under "web".
    if not any(k in client_config for k in ("web", "installed")):
        client_config = {"web": client_config}

    if REDIRECT_URI is None:
        raise RuntimeError("REDIRECT_URI not configured. Set st.secrets['BASE_URL'] and st.secrets['REDIRECT_PATH'].")

    flow = Flow.from_client_config(client_config=client_config, scopes=SCOPES, redirect_uri=REDIRECT_URI)

    # 2) handle redirect back from Google
    params = st.experimental_get_query_params()
    if "error" in params:
        # user denied consent or error from provider; clear params and show message
        st.experimental_set_query_params()
        st.warning("Google sign-in failed or was cancelled.")
        return None

    if "code" in params:
        code = params["code"][0]
        # Exchange the code
        flow.fetch_token(code=code)
        creds = flow.credentials

        # persist credentials to disk (best-effort)
        try:
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        except Exception:
            # non-fatal: continue without disk persistence
            pass

        # store in session for quick reuse during this app run
        st.session_state["_google_creds"] = creds

        # clear query params to avoid re-exchange on rerun
        st.experimental_set_query_params()
        return creds

    # 3) no code -> generate auth url and show it, then STOP execution so caller doesn't continue with creds None
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    st.session_state["_oauth_state"] = state

    st.markdown("### Sign in with Google")
    st.write("Click the link below to sign in with Google:")
    st.write(auth_url)  # optionally show link; you may want to use st.markdown(f"[Sign in]({auth_url})") for clickable link

    # Important: stop here — prevents downstream code from running with creds == None
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