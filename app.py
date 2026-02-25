import os
import sys
import shutil
from datetime import datetime
import json
import hashlib

from openai import OpenAIError

import streamlit as st
import logging

from excel_processor import to_JSON, propose_credit_limit, generate_AIcomment
from google_drive_utils import upload_drive, google_drive_auth
from prompt_processor import render_prompt

LOCAL_OUTPUT_BASE_DIR = "output"
LOG_PATH = os.path.join(LOCAL_OUTPUT_BASE_DIR, "app.log")
os.makedirs(LOCAL_OUTPUT_BASE_DIR, exist_ok=True)
API_KEY = st.secrets["api_keys"]["openai"]

PROMPT_PATH = os.path.join("prompts", "template_v1.txt")


def hesh_pass(lozinka: str) -> str:
    # Pretvaramo lozinku u bajtove
    lozinka_bytes = lozinka.encode('utf-8')
    # Pravimo SHA-256 heš objekat
    sha256 = hashlib.sha256()
    # Dodajemo bajtove lozinke u heš objekat
    sha256.update(lozinka_bytes)
    # Vraćamo heš u heksadecimalnom obliku (string)
    return sha256.hexdigest()
def initialize_logger():
        logger = logging.getLogger("FinAiApp")

        if not logger.handlers:
            logger.setLevel(logging.INFO)
            logger.propagate = False

            log_formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
                )

            # Stream handler
            stream_handler = logging.StreamHandler(sys.stdout)
            stream_handler.setFormatter(log_formatter)
            logger.addHandler(stream_handler)

            # File handler
            file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
            file_handler.setFormatter(log_formatter)
            logger.addHandler(file_handler)

            logger.info("--- Aplikacija pokrenuta, loger konfigurisan ---")

        return logger

def login_form():
    """
    Prikazuje login formu i proverava kredencijale.
    """
    st.title("Prijava")
    password = st.text_input("Unesite pristupnu šifru:", type="password")

    if st.button("Potvrdi"):
        if not password:
            st.warning("Molimo unesite šifru.")
            return   # Prekida se izvršavanje ako nema sifre

        try:
            # Ucitaj sve korisnike i njihove hesirane sifre
            users_db = st.secrets["users"]
            # Heširaj unetu šifru samo jednom
            entered_password_hex = hesh_pass(password)
            # Prolazi kroz sve korisnike u bazi
            for username, correct_password_hex in users_db.items():
                if entered_password_hex == correct_password_hex:
                    # Ako se sifra poklopi, postavi stanje sesije i prekini
                    st.session_state["authenticated"] = True
                    st.session_state['user'] = username
                    st.rerun()

            # Ako petlja prodje sve korisnike i ne nadje poklapanje
            st.error("Pristupna šifra nije tačna.")

        except KeyError:
            st.error("Greska u konfiguraciji: Sekcija [users] nije pronadjena")
        except Exception as e:
            st.error(f"Došlo je do neočekivane greške: {e}")
# ********************
# Glavni deo aplikacije
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False

if not st.session_state["authenticated"]:
    login_form()
else:
    # --- PRIVREMENA BLOKADA ZA PUSH ---
    st.title('Analiza kreditnog rizika')
    st.error("### 🛠️ Izmene u toku...")
    st.info("Sistem je trenutno u fazi ažuriranja. Molimo vas za strpljenje, funkcionalnost će biti ponovo uspostavljena uskoro.")
    
    if st.button("Odjavi se"):
        st.session_state["authenticated"] = False
        st.rerun()
        
    st.stop() # Sve ispod ove linije se ignoriše na serveru
    # ----------------------------------
# ****************
# Glavni deo aplikacije
#if "authenticated" not in st.session_state:
#    st.session_state["authenticated"] = False

#if not st.session_state["authenticated"]:
#    login_form()
#else:
    # --- LOGGING SETTINGS ---
    # --- Initialization ---
    if 'logger' not in st.session_state:
        st.session_state['logger'] = initialize_logger()

    logger = st.session_state['logger']
    st.title('Analiza kreditnog rizika')

    # Initialization session state
    if 'current_stage' not in st.session_state:
        st.session_state['current_stage'] = 'waiting_for_file'
        st.session_state['ai_comment'] = ''
        st.session_state['ai_comment_path'] = ''
        st.session_state['client_name'] = ''
        st.session_state['uploaded_file_path'] = ''
        st.session_state['original_file_name'] = ''
        st.session_state['timestamp'] = ''
        st.session_state['log_uploaded'] = False
        st.session_state['file_error'] = ''
        st.session_state['openai_error'] = ''
        st.session_state['upload_in_progress'] = False
        logger.info("Session state inicijalizovan. Aplikacija čeka fajl.")

    # --- KONTROLA TOKA APLIKACIJE ---

    # --- FAZA 1: ČEKANJE FAJLA ---
    if st.session_state['current_stage'] == 'waiting_for_file':

        if st.session_state.get('file_error'):
            st.error(st.session_state['file_error'])
            st.session_state['file_error'] = ''
        uploaded_file = st.file_uploader(
            "Izaberi Excel fajl",
            type=["xls", "xlsx", "xlsm"]
        )
        if uploaded_file is not None:
            temp_dir = 'temp_uploaded_files'
            st.session_state['timestamp'] = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            os.makedirs(temp_dir, exist_ok=True)
            temp_file_path = os.path.join(
                temp_dir, st.session_state['timestamp'] +
                '_' + st.session_state['user'] +
                '_' + uploaded_file.name
                )

            with open(temp_file_path, 'wb') as f:
                f.write(uploaded_file.getbuffer())

            # Update session state
            st.session_state['uploaded_file_path'] = temp_file_path
            st.session_state['original_file_name'] = uploaded_file.name
            st.session_state['current_stage'] = 'file_uploaded'
            logger.info(f"Fajl uspešno sačuvan: {uploaded_file.name} na putanji {temp_file_path}")
            st.rerun()

# TODO iskljuci dugme za pokretanje analize dok traje ubacivanje na drive
    # --- FAZA 2: FAJL UBAČEN, ČEKA SE ANALIZA ---
    elif st.session_state['current_stage'] == 'file_uploaded':

        if st.session_state.get('openai_error'):
            st.error(st.session_state['openai_error'])
            st.session_state['openai_error'] = ''

        st.success(f"Fajl '{st.session_state['original_file_name']}' je spreman za analizu.")

        if not st.session_state.get('upload_in_progress', False):
            if st.button('Pokreni analizu'):
                st.session_state['upload_in_progress'] = True
                st.rerun()
        else:
            #  upload_in_progress postavljen
            creds = google_drive_auth(logger)
            if creds:
                drive_folder_id = st.secrets["google_drive_folder"]["folder_id"]
                file_id = upload_drive(st.session_state['uploaded_file_path'], creds, drive_folder_id, logger)
                if file_id:
                    st.success(f"Fajl uspešno uploadovan! ID: {file_id}")
                    st.session_state['current_stage'] = 'analysis_in_progress'
                    st.rerun()
                else:
                    st.error("Upload fajla nije uspeo.")
                    st.session_state['upload_in_progress'] = False  # Resetuj
            else:
                st.error("Nije uspela autentifikacija za Google Drive.")
                st.session_state['upload_in_progress'] = False
    # --- FAZA 3: ANALIZA U TOKU ---
    elif st.session_state['current_stage'] == 'analysis_in_progress':
        with st.spinner("Analiziram podatke i generišem izveštaj..."):
            try:
                excel_file_path = st.session_state['uploaded_file_path']
                logger.info(f"Pokrenuta analiza, fajl: {excel_file_path}")
                # --- LOGIKA ZA ANALIZU ---
                json_content_for_ai = to_JSON(excel_file_path)
                logger.info("JSON sadržaj uspešno generisan.")
                # st.write("Prikaz JSON sadržaja:")
                # st.json(json_content_for_ai)
                year_data = json_content_for_ai["poslovanje_po_godinama_osnovno"]
                mostrecent_year = max(year_data.keys())
                revenue = year_data[mostrecent_year]["ukupni_prihodi"] * 1000
                print(revenue)
                limit = propose_credit_limit(revenue)
                percentage = limit.get("proc_limita")
                limit = limit.get("predlog_limit")
                logger.info(f"Predloženi limit: {limit}, procenat: {percentage}")
                client_name_from_json = json_content_for_ai.get("osnovne_informacije", {}).get("naziv_komitenta", "")
                client_name = client_name_from_json
                vars = {"limit": limit, "percentage": percentage, "client_name": client_name, "client_json_data": json_content_for_ai}
                prompt_text = render_prompt(PROMPT_PATH, vars)
                st.write(prompt_text)
                logger.info(f"Prompt uspesno renderovan \n\n: {prompt_text}")

                ai_comment, usage = generate_AIcomment(prompt_text, API_KEY)

                logger.info(f"Token usage: total={usage['total_tokens']}, input={usage['input_tokens']}, output={usage['output_tokens']}")

                
                #ai_comment = "PROBA"
                logger.info("AI komentar uspešno generisan.")

                ai_comment_output_base_dir = os.path.join(LOCAL_OUTPUT_BASE_DIR, "komentari")
                ai_comment_firm_specific_dir = os.path.join(ai_comment_output_base_dir, client_name)
                os.makedirs(ai_comment_firm_specific_dir, exist_ok=True)
                ai_comment_local_file = os.path.join(
                    ai_comment_firm_specific_dir,
                    f'{st.session_state['timestamp'] + '_' + st.session_state['user'] + '_' + client_name}_ai_comment.txt'
                )

                with open(ai_comment_local_file, 'w', encoding='utf-8') as f_comment:
                    f_comment.write(ai_comment)

                os.makedirs(os.path.join(LOCAL_OUTPUT_BASE_DIR, 'json'), exist_ok=True)
                json_output_path = os.path.join(
                    LOCAL_OUTPUT_BASE_DIR,
                    'json',
                    f'{st.session_state['timestamp'] + '_' + st.session_state['user'] + '_' + client_name}_data_for_ai.json'
                )
                with open(json_output_path, 'w', encoding='utf-8') as json_file:
                    json.dump(json_content_for_ai, json_file, ensure_ascii=False, indent=4)

                st.session_state['ai_comment_path'] = ai_comment_local_file
                # --- Upload JSON i AI komentar na Google Drive ---
                creds = google_drive_auth(logger)
                if creds:
                    # Upload JSON (.json)
                    drive_folder_id = st.secrets["google_drive_folder"]["folder_id"]
                    file_id = upload_drive(json_output_path, creds, drive_folder_id, logger)
                    if file_id:
                        st.success(f"Fajl uspešno uploadovan! ID: {file_id}")
                        logger.info(f"JSON uspešno uploadovan na Google Drive. ID: {drive_folder_id}")
                    else:
                        st.error("Upload fajla nije uspeo.")
                    # Upload AI komentara (.txt)
                    drive_folder_id = st.secrets["google_drive_folder"]["folder_id"]
                    file_id = upload_drive(ai_comment_local_file, creds, drive_folder_id, logger)
                    if file_id:
                        st.success(f"Fajl ai kom uspešno uploadovan! ID: {file_id}")
                        logger.info(f"AI komentar uspešno uploadovan na Google Drive. ID: {drive_folder_id}")
                    else:
                        st.error("Upload fajla nije uspeo.")
                else:
                    st.error("Autentifikacija za Google Drive nije uspela. Fajlovi nisu uploadovani.")
                    logger.error("Google Drive autentifikacija nije uspela.")
                # short_ai_text_for_pdf = shorter_text(ai_comment)
                # print(short_ai_text_for_pdf)
                # pdf_output_dir = os.path.join('output', 'pdf')
                # os.makedirs(pdf_output_dir, exist_ok=True)
                # pdf_file_path = os.path.join(pdf_output_dir, f'{st.session_state["client_name"]}_kreditna_analiza.pdf')
                # generate_PDF(pdf_file_path, excel_file_path, short_ai_text_for_pdf)
                logger.info(f"TXT uspešno generisan: {ai_comment_local_file}")
                # Saving result
                st.session_state['ai_comment'] = ai_comment
                st.session_state['current_stage'] = 'analysis_done'
                st.rerun()
            except OpenAIError as oe:
                logger.error(f"Greška prilikom poziva OpenAI API-ja: {oe}")
                st.error("Došlo je do problema sa AI servisom (OpenAI). Pokušajte ponovo kasnije.")
                st.session_state['current_stage'] = 'file_uploaded'
                st.session_state['openai_error'] = 'Došlo je do problema sa AI servisom (OpenAI). Pokušajte ponovo kasnije.'
                st.rerun()

            except (ValueError, KeyError, AttributeError, TypeError, IndexError) as ex:
                logger.error(f"Greška prilikom čitanja fajla: {ex}")

                error_str = str(ex).lower()
                if 'fin' in error_str and 'prazan' in error_str:
                        # Prikazujemo specifičnu, jasnu poruku korisniku
                        user_message = f"Greška: {ex} Molimo popunite ga podacima i pokušajte ponovo."
                        st.error(user_message)
                        st.session_state['file_error'] = user_message
                else:
                        # Ako nije ta greška, prikazujemo generičku poruku
                        user_message = "Fajl nije u ispravnom formatu. Molimo izaberite ispravan fajl."
                        st.error(user_message)
                        st.session_state['file_error'] = user_message
        
                st.session_state['current_stage'] = 'waiting_for_file'
                st.rerun()

            except Exception as e:
                logger.error(f"Neočekivana greška tokom analize: {e}")
                st.error("Došlo je do neočekivane greške tokom analize. Pokušajte ponovo.")
                st.session_state['current_stage'] = 'waiting_for_file'
                st.rerun()

    # --- FAZA 4: ANALIZA ZAVRŠENA, PRIKAZ REZULTATA ---
    elif st.session_state['current_stage'] == 'analysis_done':
        st.header("Rezultati analize")
        st.success("Analiza je uspešno završena!")
        # Ovde prikažite rezultate koje ste sačuvali u session_state
        st.subheader("AI Komentar:")
        st.text_area("Generisani AI Komentar:", st.session_state['ai_comment'], height=300, key="ai_comment_display")

        if not st.session_state.get('log_uploaded'):

            if st.session_state.get('ai_comment_path'):
                creds = google_drive_auth(logger)
                if creds:
                    try:
                        DRIVE_FOLDER_ID = st.secrets["google_drive_folder"]["folder_id"]
                    except KeyError:
                        st.error("Nije pronađen ID Google Drive foldera u secrets.toml!")
                        DRIVE_FOLDER_ID = None

                    if DRIVE_FOLDER_ID:
                        log_temp_path = f"{st.session_state['timestamp'] + '_' + st.session_state['user']}_app.log"
                        shutil.copy(LOG_PATH, log_temp_path)
                        log_drive_id = upload_drive(log_temp_path, creds, DRIVE_FOLDER_ID, logger)
                        if log_drive_id:
                            logger.info(f"Log fajl uspešno uploadovan na Google Drive. ID: {log_drive_id}")
                        else:
                            st.error("Došlo je do greške prilikom upload-a log fajla na Google Drive.")

                        os.remove(log_temp_path)
                        st.session_state['log_uploaded'] = True

            # Ciscenje log fajla
            open(LOG_PATH, 'w').close()
            logger.info("Log fajl uspešno ispražnjen.")
            try:
                # Open the generated TXT file and provide download button
                with open(st.session_state['ai_comment_path'], "rb") as file:
                    btn = st.download_button(
                        label="Preuzmi TXT",
                        data=file,
                        file_name=os.path.basename(st.session_state['ai_comment_path']),
                        mime="application/txt"
                    )
            except FileNotFoundError:
                st.error("TXT fajl nije pronađen. Molimo pokrenite analizu ponovo.")
                logger.error(f"TXT fajl nije pronađen na putanji: {st.session_state.get('ai_comment_path')}")

        if st.button("Pokreni novu analizu"):

            # Resetovanje stanja
            st.session_state['current_stage'] = 'waiting_for_file'
            st.session_state['log_uploaded'] = False
            st.session_state['upload_in_progress'] = False
            logger.info("Pokretanje nove analize.")
            st.rerun()
