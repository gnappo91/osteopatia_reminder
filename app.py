import streamlit as st
from pdb import set_trace
from utils.import_secrets import import_secrets
import_secrets()

from utils.google_utils import get_google_credentials, get_events, load_token
from utils.twilio_utils import send_twilio_message

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ITALIAN_MONTHS = [
    "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre",
]

def format_italian_datetime(iso_dt: str, tz: str = "Europe/Rome") -> str:
    if iso_dt.endswith("Z"):
        iso_dt = iso_dt[:-1] + "+00:00"
    dt = datetime.fromisoformat(iso_dt)
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

def extract_time_hhmm(iso_dt: str, tz: str = "Europe/Rome") -> str:
    if iso_dt.endswith("Z"):
        iso_dt = iso_dt[:-1] + "+00:00"
    dt = datetime.fromisoformat(iso_dt)
    local_dt = dt.astimezone(ZoneInfo(tz))
    return local_dt.strftime("%H:%M")

def create_appointment_summary(appointments):
    schedules = "\n".join(["- **{name}**: {time}".format(name=each["name"], time = format_italian_datetime(each["start"])) for each in appointments])
    return f"""Ho trovato questi appuntamenti:

{schedules}
"""

st.title("Invia un messaggio di reminder a tutti i pazienti di domani")

# === Initialize session_state keys only once (do NOT overwrite them on every run) ===
if "google_credentials" not in st.session_state:
    creds = load_token()
    st.write(creds)
    st.session_state["google_credentials"] = creds
if "appointments" not in st.session_state:
    st.session_state["appointments"] = None
if "last_summary" not in st.session_state:
    st.session_state["last_summary"] = None

# Button to fetch contacts
if st.button("Trova contatti a cui inviare il messaggio", key="find_contacts"):
    # if no creds in session, fetch them and store
    if not st.session_state["google_credentials"]:
        with st.spinner("Authenticating with Google..."):
            creds = get_google_credentials()
        st.session_state["google_credentials"] = creds
    else:
        creds = st.session_state["google_credentials"]

    # fetch events and store them in session_state
    appointments = get_events(creds)
    st.session_state["appointments"] = appointments

    if not appointments:
        st.write("Non ho trovato nessun paziente per domani")
        st.session_state["last_summary"] = None
    else:
        summary = create_appointment_summary(appointments)
        st.session_state["last_summary"] = summary
        st.write(summary)

# If we already have a summary/appointments in session_state, show them
if st.session_state["appointments"]:

    # Button to send reminders (exists only when appointments are present)
    if st.button("Invia un promemoria a questi contatti", key="send_reminders"):
        # Use the appointments stored in session_state (persisted across reruns)
        appointments_to_send = st.session_state["appointments"]
        if not appointments_to_send:
            st.warning("Nessun appuntamento da inviare.")
        else:
            # iterate and send messages
            for appointment in appointments_to_send:
                phone = appointment.get("phone")
                time = extract_time_hhmm(appointment["start"])
                with st.spinner(f"Sto inviando il messaggio a {phone}..."):
                    send_twilio_message(phone, time)
            st.success("Messaggi inviati!")
            # Optional: clear appointments after sending to avoid duplicate sends
            st.session_state["appointments"] = None
            st.session_state["last_summary"] = None
