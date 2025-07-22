import os
import sys
import shutil
from datetime import datetime
import pandas as pd
import json
import hashlib

import streamlit as st
import logging

from openai import OpenAI
from openai import OpenAIError

from excel_processor import to_JSON, generate_AIcomment
from google_drive_utils import upload_drive, google_drive_auth



LOCAL_OUTPUT_BASE_DIR = "output"
LOG_PATH = os.path.join(LOCAL_OUTPUT_BASE_DIR, "app.log")
os.makedirs(LOCAL_OUTPUT_BASE_DIR, exist_ok=True)
API_KEY = st.secrets["api_keys"]["openai"]

def hesh_pass(lozinka: str) -> str:
    # Pretvaramo lozinku u bajtove
    lozinka_bytes = lozinka.encode('utf-8')
    # Pravimo SHA-256 heš objekat
    sha256 = hashlib.sha256()
    # Dodajemo bajtove lozinke u heš objekat
    sha256.update(lozinka_bytes)
    # Vraćamo heš u heksadecimalnom obliku (string)
    return sha256.hexdigest()

# TODO: enkriptuj nove sifre i ubaci u .toml
def check_password(): 
    """ True ako je šifra tačna."""
    st.title("Prijava")
    password = st.text_input("Unesite pristupnu šifru:", type="password")

    if st.button("Potvrdi"):
        if password:
            try:
                # u hex
                entered_password_hex = hesh_pass(password)
                # Preuzmi tačnu hex šifru iz st.secrets
                correct_password_hex1 = st.secrets["auth1"]["password_hex"]
                correct_password_hex2 = st.secrets["auth2"]["password_hex"]
                correct_password_hex3 = st.secrets["auth3"]["password_hex"]

                if entered_password_hex == correct_password_hex1:
                    st.session_state["authenticated"] = True
                    st.session_state['user'] = 'Emilija'
                    # st.rerun() - stranica se odmah osvezi i prikaze app
                    st.rerun()
                elif entered_password_hex == correct_password_hex2:
                    st.session_state["authenticated"] = True
                    st.session_state['user'] = 'Stefan'
                    # st.rerun() - stranica se odmah osvezi i prikaze app
                    st.rerun()
                elif entered_password_hex == correct_password_hex3:
                    st.session_state["authenticated"] = True
                    st.session_state['user'] = 'Ana'
                    # st.rerun() - stranica se odmah osvezi i prikaze app
                    st.rerun()
                else:
                    st.error("Pristupna šifra nije tačna.")
            except KeyError:
                st.error("Greška u konfiguraciji: 'auth.password_hex' nije pronađen u secrets.toml.")
                return False
        else:
            st.warning("Molimo unesite šifru.")
    return False

# --- GLAVNI DEO APLIKACIJE ---

if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
    st.session_state['user'] = ''

# Ako korisnik nije autorizovan, prikaži ekran za prijavu
if not st.session_state["authenticated"]:
    check_password()
else:
    # --- LOGGING SETTINGS ---

    def initialize_logger():
        logger = logging.getLogger("FinAiApp")

        if not logger.handlers:
            logger.setLevel(logging.INFO)
            logger.propagate = False

            log_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

            # Stream handler za prikaz u Streamlit Cloud logovima
            stream_handler = logging.StreamHandler(sys.stdout)
            stream_handler.setFormatter(log_formatter)
            logger.addHandler(stream_handler)

            # File handler za lokalni log koji ćeš slati na Google Drive
            file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
            file_handler.setFormatter(log_formatter)
            logger.addHandler(file_handler)

            logger.info("--- Aplikacija pokrenuta, loger konfigurisan ---")

        return logger

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
        st.session_state['file_error'] =''
        st.session_state['openai_error']=''
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
            temp_file_path = os.path.join(temp_dir, st.session_state['timestamp'] +'_'+ st.session_state['user'] + '_' + uploaded_file.name)

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
                #st.write("Prikaz JSON sadržaja:")
                #st.json(json_content_for_ai)

                
                client_name_from_json = json_content_for_ai.get("osnovne_informacije", {}).get("naziv_komitenta", "")
                client_name = client_name_from_json
                    

                prompt_text = f"""
               You are an expert Credit Risk Analyst AI. Your primary function is to analyze the provided JSON data for a client and generate a comprehensive "AI Comment" in Serbian. Your output must be a professional, ready-to-use report for a human credit risk analyst.

                        **CRITICAL OUTPUT RULES:**

                        **1. NO TECHNICAL JARGON:**
                        *   Your final "AI Comment" must be written in professional business Serbian.
                        *   **DO NOT** mention JSON field names (e.g., saradnja_sektori_rsd, zuti_pokazatelj).
                        *   Instead, describe the meaning of the data in business terms (e.g., "Klijent je bio u blokadi..." instead of "The blokade_od_2010 field shows...").

                        **2. PROVIDE SUPPORTING DATA:**
                        *   Whenever you make an analytical statement about a specific metric (e.g., growth, decline, stability), you **must** immediately follow it with the key data points in parentheses to make the analysis transparent and verifiable.
                            *    Correct Example: "Likvidnost je u blagom padu (opšti racio je 1.5, pad sa 2.1 u prethodnoj godini)."
                            *    Correct Example: "Realizacija u sektoru 'seme' pokazuje snažan rast (sa 610.909 hiljada RSD u 2022. na 2.338.507 hiljada RSD u 2024)."
                            *    Incorrect Example: "Likvidnost je u padu." (Missing data.)

                        **INPUT DATA DESCRIPTION (For your internal processing only):**

                        *   saradnja_sektori_rsd: Data on historical cooperation. This is the **KEY** to determine the analysis path. **If this field is missing, the analysis will follow PATH B.**
                        *   finansije: Detailed financial statements. The zuti_pokazatelj key marks indicators of highest importance.
                        *   Other fields: blokade_od_2010, ugovori etc.

                        **ANALYSIS GUIDELINES & HIERARCHICAL DECISION LOGIC:**

                        **STEP 1: Check for Universal "No-Go" Conditions**
                        *(The following lead to an immediate limit of 0 RSD, unless strongly justified otherwise.)*
                        *   No valid collateral (contract, promissory note, or insurance).
                        *   Blocked account(s) in the past 2–3 years.
                        *   Negative net working capital for two consecutive years.

                        **STEP 2: Determine Client Type and Analysis Path**
                        *(Based on saradnja_sektori_rsd data.)*
                        *   If **strong cooperation history** exists → Follow **PATH A: KNOWN CLIENT**.
                        *   If this field is **missing** → Follow **PATH B: NEW / LAPSED CLIENT**.

                        ---

                        **PATH A: KNOWN CLIENT ANALYSIS**
                        *   **Principle:** Strong historical cooperation is the primary factor.
                        *   Analyze business volume and payment discipline, citing specific numbers in parentheses.
                        *   Financials should be interpreted in the context of the existing relationship.
                        *   Credit limit can go up to **5% of annual realized revenue**.

                        ---

                        **PATH B: NEW / LAPSED CLIENT ANALYSIS**
                        *   **Principle:** Use conservative and strict financial analysis.
                        *   Focus on high-impact indicators. Every claim must be backed by values in parentheses.
                        *   **Carefully check liquidity:**
                            *   Current ratio (Opšta likvidnost) > 2
                            *   Quick ratio (Brza likvidnost) > 1
                        *   **Typical limit:** **1–2% of latest annual revenue**, or a starter limit of **600,000–1,000,000 RSD** for new firms without reports.

                        ---

                        **STEP 3: FORMATTING THE FINAL "AI COMMENT"**
                        Structure your output using the format below. All findings must use business language and include supporting values in parentheses.

                        **Tip klijenta i osnova za analizu**
                        (Indicate the client’s name and type – “Poznat” or “Novi”. Specify what the analysis is based on.)

                        **Ukupna procena**
                        (Provide a high-level summary of the client’s financial and risk profile. Key numbers should appear in parentheses.)

                        ---

                        **Ključni faktori rizika**
                        (List format. Each point must state a risk and include data in parentheses.)
                        *   Example: Likvidnost: Opšti racio likvidnosti je pao ispod preporučenog nivoa (trenutno 1.5, pad sa 2.14 u 2022), a brza likvidnost je na samoj granici (1.01).

                        ---

                        **Pozitivni pokazatelji**
                        (List format. Each point must state a strength and include data in parentheses.)

                        ---

                        **Predlog limita i obrazloženje**
                        **Predlaže se limit od [Iznos] RSD.**

                        **Obrazloženje:**
                        (Provide detailed justification with supporting data.)
                        *   Example: "Limit od 5 miliona RSD predstavlja manje od 0.25% godišnje realizacije (ukupna realizacija ~2.3 milijarde RSD u 2024), što je konzervativno u odnosu na preporučeni raspon za poznate klijente."

                    

                        --- START OF CLIENT JSON DATA ---
                        {json_content_for_ai}
                        --- END OF CLIENT JSON DATA ---
                """

                ai_comment = generate_AIcomment(prompt_text, API_KEY)
                logger.info("AI komentar uspešno generisan.")

                ai_comment_output_base_dir = os.path.join(LOCAL_OUTPUT_BASE_DIR, "komentari")
                ai_comment_firm_specific_dir = os.path.join(ai_comment_output_base_dir, client_name)
                os.makedirs(ai_comment_firm_specific_dir, exist_ok=True)
                ai_comment_local_file = os.path.join(ai_comment_firm_specific_dir, f'{st.session_state['timestamp'] +'_'+ st.session_state['user'] + '_' +  client_name}_ai_comment.txt')

                with open(ai_comment_local_file, 'w', encoding='utf-8') as f_comment:
                    f_comment.write(ai_comment)

                os.makedirs(os.path.join(LOCAL_OUTPUT_BASE_DIR, 'json'), exist_ok=True)
                json_output_path = os.path.join(LOCAL_OUTPUT_BASE_DIR, 'json', f'{st.session_state['timestamp'] +'_'+ st.session_state['user'] + '_' + client_name}_data_for_ai.json')
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
                            logger.info(f"JSON uspešno uploadovan na Google Drive. ID: {drive_folder_id }")
                        else:
                            st.error("Upload fajla nije uspeo.")
                        
                        # Upload AI komentara (.txt)
                        drive_folder_id = st.secrets["google_drive_folder"]["folder_id"]
                        file_id = upload_drive(ai_comment_local_file, creds, drive_folder_id, logger)
                        if file_id:
                            st.success(f"Fajl ai kom uspešno uploadovan! ID: {file_id}")
                            logger.info(f"AI komentar uspešno uploadovan na Google Drive. ID: {drive_folder_id }")
                        else:
                            st.error("Upload fajla nije uspeo.")

                            
                else:
                    st.error("Autentifikacija za Google Drive nije uspela. Fajlovi nisu uploadovani.")
                    logger.error("Google Drive autentifikacija nije uspela.")
                    

                #short_ai_text_for_pdf = shorter_text(ai_comment)
                #print(short_ai_text_for_pdf)
                
                #pdf_output_dir = os.path.join('output', 'pdf')
                #os.makedirs(pdf_output_dir, exist_ok=True)
                #pdf_file_path = os.path.join(pdf_output_dir, f'{st.session_state["client_name"]}_kreditna_analiza.pdf')

                #generate_PDF(pdf_file_path, excel_file_path, short_ai_text_for_pdf)
                logger.info(f"TXT uspešno generisan: {ai_comment_local_file}")

                # Saving result
                st.session_state['ai_comment'] = ai_comment
                #st.session_state['pdf_path'] = pdf_file_path
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
                st.error("Fajl nije u ispravnom formatu. Molimo izaberite ispravan fajl.")
                st.session_state['current_stage'] = 'waiting_for_file'
                st.session_state['file_error'] = "Fajl nije u ispravnom formatu. Molimo izaberite ispravan fajl."
                st.rerun()

            except Exception as e:
                logger.error(f"Neočekivana greška tokom analize: {e}")
                st.error("Došlo je do neočekivane greške tokom analize. Pokušajte ponovo.")
                st.session_state['current_stage'] = 'file_uploaded'
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
                        log_temp_path = f"{st.session_state['timestamp'] +'_'+ st.session_state['user']}_app.log"

                    
                        shutil.copy(LOG_PATH, log_temp_path)

                        log_drive_id = upload_drive(log_temp_path, creds, DRIVE_FOLDER_ID, logger)
                        if log_drive_id:
                            logger.info(f"Log fajl uspešno uploadovan na Google Drive. ID: {log_drive_id}")
                        else:
                            st.error("Došlo je do greške prilikom upload-a log fajla na Google Drive.")

                        os.remove(log_temp_path)
                        st.session_state['log_uploaded'] = True

            #Ciscenje log fajla
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
                st.error("PDF fajl nije pronađen. Molimo pokrenite analizu ponovo.")
                logger.error(f"TXT fajl nije pronađen na putanji: {st.session_state.get('ai_comment_path')}")

        st.write(f"Klijent: {st.session_state['client_name']}")
        # st.write(f"Komentar AI: {st.session_state['ai_comment']}")

    
        if st.button("Pokreni novu analizu"):

            #Resetovanje stanja
            st.session_state['current_stage'] = 'waiting_for_file'
            st.session_state['log_uploaded'] = False
            st.session_state['upload_in_progress'] = False 
            logger.info("Pokretanje nove analize.")
            st.rerun()