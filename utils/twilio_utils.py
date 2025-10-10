import os
from twilio.rest import Client

TWILIO_ACCOUNT_SID_NEW = os.getenv("TWILIO_ACCOUNT_SID_NEW")
TWILIO_AUTH_TOKEN_NEW = os.getenv("TWILIO_AUTH_TOKEN_NEW")
WHATSAPP_PHONE_NUMBER = os.getenv("WHATSAPP_PHONE_NUMBER")
TEMPLATE_ID = os.getenv("TEMPLATE_ID")

def send_twilio_message(to, time):
    
    client = Client(TWILIO_ACCOUNT_SID_NEW, TWILIO_AUTH_TOKEN_NEW)
    try:
        message = client.messages.create(
        from_=f'whatsapp:{WHATSAPP_PHONE_NUMBER}',
        content_sid=TEMPLATE_ID,
        content_variables=f'{{"1":"{time}"}}',
        to=f'whatsapp:{to}'
        )
    except Exception as _:
        print(f"Number {to} is invalid")