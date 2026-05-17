from __future__ import annotations

from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

CLIENT_FILE = Path('/Users/ilgam/Jurist/credentials/google_oauth_client.json')
TOKEN_FILE = Path('/Users/ilgam/Jurist/credentials/google_token.json')
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/documents',
]

flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
creds = flow.run_local_server(port=0, prompt='consent')
TOKEN_FILE.write_text(creds.to_json(), encoding='utf-8')
TOKEN_FILE.chmod(0o600)
print(f'Token saved: {TOKEN_FILE}')
