import streamlit as st
from pdb import set_trace
from utils.import_secrets import import_secrets
import_secrets()

from utils.google_utils import get_google_credentials, get_events
from utils.twilio_utils import send_twilio_message

st.title("Invia un messaggio di reminder a tutti i pazienti di domani")

if st.button("Trova contatti a cui inviare il messaggio"):
    with st.spinner("Authenticating with Google..."):
        creds = get_google_credentials()

    with st.spinner("Loading Calendar & Contacts..."):

        appointments = get_events(creds)
        if not appointments:
            "Non ho trovato nessun paziente per domani"
        else:
            
            schedules = "\n".join(["- {} alle {}".format(name=each["name"], time = each["time"]) for each in appointments])

            st.write(f"""Ho trovato questi appuntamenti:
                     {schedules}""")
            
            if st.button("Invia un promemoria a questi contatti"):
                for appointment in appointments.items():
                    phone = appointment["phone"]
                    time = appointment["time"]
                    send_twilio_message(phone, time)
