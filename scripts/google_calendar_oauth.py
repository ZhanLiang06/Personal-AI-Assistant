from pathlib import Path
import os

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
CLIENT_SECRET_PATH = os.environ["GOOGLE_OAUTH_CLIENT_SECRETS_PATH"]
TOKEN_PATH = Path(os.environ.get("GOOGLE_CALENDAR_TOKEN_PATH", "data/google_calendar_token.json"))

TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)

flow = InstalledAppFlow.from_client_secrets_file(
    CLIENT_SECRET_PATH,
    scopes=SCOPES,
)

creds = flow.run_local_server(port=0)

TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
print(f"Saved Google Calendar token to {TOKEN_PATH}")