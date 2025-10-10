import streamlit as st
from pdb import set_trace
from utils.import_secrets import import_secrets
import_secrets()

from utils.google_utils import get_google_credentials, get_events
from utils.twilio_utils import send_twilio_message

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ITALIAN_MONTHS = [
    "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre",
]

def format_italian_datetime(iso_dt: str, tz: str = "Europe/Rome") -> str:
    """
    Convert an ISO datetime string with offset (e.g. "2025-10-11T15:00:00+02:00" or "...Z")
    to either:
      - "Domani <D> <Mese> alle HH:MM" if the datetime (in the given tz) is tomorrow, or
      - "<D> <Mese> alle HH:MM" otherwise.

    Returns a simple string (no localization library required).
    """
    # Accept trailing 'Z' for UTC
    if iso_dt.endswith("Z"):
        iso_dt = iso_dt[:-1] + "+00:00"

    # parse ISO — fromisoformat handles offsets like "+02:00"
    dt = datetime.fromisoformat(iso_dt)
    # convert to target timezone (makes comparison reliable)
    local_dt = dt.astimezone(ZoneInfo(tz))

    today = datetime.now(ZoneInfo(tz)).date()
    tomorrow = today + timedelta(days=1)
    local_date = local_dt.date()

    day = local_dt.day
    month_name = ITALIAN_MONTHS[local_dt.month - 1]
    time_str = local_dt.strftime("%H:%M")

    if local_date == tomorrow:
        return f"Domani {day} {month_name} alle {time_str}"
    else:
        return f"{day} {month_name} alle {time_str}"

from datetime import datetime
from zoneinfo import ZoneInfo

def extract_time_hhmm(iso_dt: str, tz: str = "Europe/Rome") -> str:
    """
    Extract the local time (HH:MM) from an ISO datetime string with timezone offset.
    Example: "2025-10-11T15:00:00+02:00" → "15:00"
    """
    # Handle UTC 'Z' suffix if present
    if iso_dt.endswith("Z"):
        iso_dt = iso_dt[:-1] + "+00:00"

    dt = datetime.fromisoformat(iso_dt)
    local_dt = dt.astimezone(ZoneInfo(tz))
    return local_dt.strftime("%H:%M")


st.title("Invia un messaggio di reminder a tutti i pazienti di domani")

if st.button("Trova contatti a cui inviare il messaggio"):
    with st.spinner("Authenticating with Google..."):
        creds = get_google_credentials()

    with st.spinner("Loading Calendar & Contacts..."):

        appointments = get_events(creds)
        if not appointments:
            st.write("Non ho trovato nessun paziente per domani")
        else:
            
            schedules = "\n".join(["- **{name}**: {time}".format(name=each["name"], time = format_italian_datetime(each["start"])) for each in appointments])

            st.write(f"""Ho trovato questi appuntamenti:

{schedules}
""")
            
            if st.button("Invia un promemoria a questi contatti"):
                for appointment in appointments.items():
                    phone = appointment["phone"]
                    time = extract_time_hhmm(appointment["start"])
                    send_twilio_message(phone, time)
