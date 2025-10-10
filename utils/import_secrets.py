# app_secrets_usage.py
import os
import json
import stat
from pathlib import Path
import streamlit as st

def import_secrets():
    # 1) Load simple env-like keys into process env (no .env file necessary)
    # pro tip: keep a dedicated section in secrets (e.g. st.secrets["env"]) so you don't leak large JSON blobs
    for k, v in st.secrets.get("env", {}).items():
        # only set if not already set (avoids overriding in special cases)
        os.environ.setdefault(k, v)

    # 2) Create a credentials.json file from a JSON secret (for libs that expect a file path)
    # Pro: write in /tmp (ephemeral on cloud) and set file mode 0o600 to restrict access.
    cred_section = st.secrets.get("google", {})
    if "SERVICE_ACCOUNT_JSON" in cred_section:
        cred_text = cred_section["SERVICE_ACCOUNT_JSON"]
        # Ensure it's valid JSON (if you stored an actual dict in secrets it may be dict already)
        if isinstance(cred_text, dict):
            cred_payload = cred_text
        else:
            cred_payload = json.loads(cred_text)

        cred_path = Path(os.path.join("tmp","credentials_web.json"))
        cred_path.write_text(json.dumps(cred_payload, indent=None, separators=(",", ":")))

        # chmod 600 — owner read/write only
        cred_path.chmod(0o600)

        # Many libraries (google SDKs) look for GOOGLE_APPLICATION_CREDENTIALS
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(cred_path)