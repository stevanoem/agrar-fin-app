import os
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request

import streamlit as st

SCOPES = ['https://www.googleapis.com/auth/drive.file']  # dozvola za upload fajlova


def google_drive_auth(logger):
    creds = None

    
    try:
        token_info = st.secrets.get("google_drive", {}).get("token", None)
        credentials_info = st.secrets.get("google_drive", {}).get("credentials", None)

        if token_info and credentials_info:
            
            creds = Credentials(
                token=token_info.get("token"),
                refresh_token=token_info.get("refresh_token"),
                token_uri=token_info.get("token_uri", "https://oauth2.googleapis.com/token"),
                client_id=credentials_info.get("client_id"),
                client_secret=credentials_info.get("client_secret"),
                scopes=SCOPES,
            )
        else:
            logger.warning("Nisu pronađene Google Drive tajne u st.secrets.")
            creds = None
    except Exception as e:
        logger.error(f"Greška pri učitavanju tajni: {e}")
        creds = None

    # Proveri da li su kredencijali validni ili treba osveženje
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                logger.info("Token osvežen.")
                # Ovde možeš snimiti osveženi token nazad u neki persistent storage ako želiš
            except Exception as e:
                logger.error(f"Greška pri osvežavanju tokena: {e}")
                creds = None
        else:
            # posto nema fajla sa credentials, napraviti flow iz client_id i client_secret
            try:
                flow = InstalledAppFlow.from_client_config({
                    "installed": {
                        "client_id": credentials_info.get("client_id"),
                        "client_secret": credentials_info.get("client_secret"),
                        "redirect_uris": credentials_info.get("redirect_uris"),
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token"
                    }
                }, SCOPES)
                creds = flow.run_local_server(port=0)
                logger.info("Kreiran novi OAuth token.")
                # Ovde možeš snimiti novi token u st.session_state ili negde
            except Exception as e:
                logger.error(f"Greška pri kreiranju OAuth toka: {e}")
                creds = None

    return creds


def google_drive_auth2(logger):
    creds = None
    token_path = 'token.json'
    creds_path = 'credentials.json'  # fajl koji preuzmeš sa Google Cloud Console

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    # Ako nema validnog tokena, pokreni OAuth flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                logger.info("Token osvežen.")
            except Exception as e:
                logger.error(f"Greška pri osvežavanju tokena: {e}")
                creds = None
        else:
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)
            logger.info("Kreiran novi OAuth token.")

        # Sačuvaj token za naredne pozive
        with open(token_path, 'w') as token_file:
            token_file.write(creds.to_json())
            logger.info("Token sačuvan u token.json")

    return creds

def upload_drive(file_path, creds, folder_id, logger):
    try:
        service = build('drive', 'v3', credentials=creds)
        file_metadata = {
            'name': os.path.basename(file_path),
            'parents': [folder_id]
        }
        media = MediaFileUpload(file_path, resumable=True)
        file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        logger.info(f"Fajl '{file_path}' uspešno uploadovan sa ID: {file.get('id')}")
        return file.get('id')
    except Exception as e:
        logger.error(f"Greška pri uploadu fajla '{file_path}': {e}")
        return None