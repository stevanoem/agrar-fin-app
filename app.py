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
def login_form():
    """Prikazuje login formu i proverava kredencijale."""
    
    st.title("Prijava")
    password = st.text_input("Unesite pristupnu šifru:", type="password")

    if st.button("Potvrdi"):
        if not password:
            st.warning("Molimo unesite šifru.")
            return # Prekida se izvršavanje ako nema sifre

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
            st.error("Greška u konfiguraciji: Sekcija [users] nije pronađena u secrets.toml.")
        except Exception as e:
            st.error(f"Došlo je do neočekivane greške: {e}")

# Glavni deo aplikacije
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False

if not st.session_state["authenticated"]:
    login_form()
else:
    # --- LOGGING SETTINGS ---

    def initialize_logger():
        logger = logging.getLogger("FinAiApp")

        if not logger.handlers:
            logger.setLevel(logging.INFO)
            logger.propagate = False

            log_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

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

                        *   saradnja_sektori_rsd: Data on historical cooperation. This is the **KEY** to determine the analysis path. **If this field is an empty object ([]), the analysis will follow PATH B.**
                        *   poslovanje_po_godinama_osnovno: yearly business metrics; all amounts in **000 RSD**
                        *   finansije: Detailed financial statements. All values are expressed in **thousands of RSD (000 RSD)**. The zuti_pokazatelj key marks indicators of highest importance.
                        *   blokade_od_2010: account block info; indicates whether blocked, period if applicable, and amount
                        *   ugovori: contracts
                        *   uvoz: inport data; financial values in EUR
                        *   izvoz: export data; financial values in EUR
                        *   Other fields: apr_zabeleske etc.

                       **ANALYSIS GUIDELINES & HIERARCHICAL DECISION LOGIC (Based on expert instructions)**

                            **STEP 1: Check for Universal "No-Go" Conditions (Limit = 0 RSD)**
                            **(These are absolute rules. If any are true, propose a 0 RSD limit and state the reason clearly. Do not proceed to other steps.)**
                            *   The client has a **Poslovni gubitak (Operating Loss)** in the most recent year.
                            *   There were any account **blockades (blokade)** in the last 3 years.
                            *   **Neto obrtna sredstva (Net Working Capital)** are negative for two consecutive years.
                            *   There is no valid collateral mentioned in the `ugovori` section (e.g., no general contract or promissory notes/menice).

                            **STEP 2: Determine Client Type and Analysis Path**
                            *   If `saradnja_sektori_rsd` is empty or has minimal data → Follow **PATH B: NEW CLIENT**.
                            *   If significant cooperation history exists → Follow **PATH A: KNOWN CLIENT**.

                            ---

                            **PATH A: KNOWN CLIENT ANALYSIS**
                            *(This logic remains the same as before)*
                            *   Principle: Strong historical cooperation is the primary factor.
                            *   Analyze business volume and payment discipline, citing specific numbers.
                            *   Credit limit can go up to 5% of annual realized revenue.

                            ---

                            **PATH B: NEW CLIENT ANALYSIS (Strict Financial Approach)**
                            **Principle:** The credit limit for a new client is based on a conservative percentage of their most recent annual revenue, adjusted based on a holistic financial risk assessment and standard starting amounts.

                            **B1: Risk Factor Assessment**
                            *   Analyze the overall financial picture, paying special attention to these key indicators:
                                *   **Red Flags:** Is the number of employees zero? Are revenues in a significant decline? Is the Z-test indicating "bankrotstvo"? Are liquidity ratios far below the recommended levels (Current Ratio < 2, Quick Ratio < 1)?
                                *   **Positive Signs:** Is the company large and well-known? Are all key financial indicators stable or growing?

                            **B2: Credit Limit Calculation - Decision Tree**
                            *   **Guiding Principle:** The calculation is a balance between a percentage of revenue and standard limits. The standard starting limit for an average new client is **1,000,000 RSD**. The absolute minimum for a client not rejected is **600,000 RSD**.

                            *   **Apply the Rules in This Order:**
                                1.  **High Risk Client:** If the Risk Assessment (B1) revealed **multiple significant Red Flags** (e.g., declining revenue AND a "bankrotstvo" Z-test), the limit **MUST BE 600,000 RSD**.
                                2.  **Exceptional Client:** If the client is **exceptionally large, stable, and financially sound** (e.g., well-known, high revenue, all indicators positive), you can propose a higher limit, calculated as **2-3% of revenue**.
                                3.  **Standard New Client (All other cases):** For a standard new client who does not fit the high-risk or exceptional categories, calculate **1-2% of revenue**. The final proposed limit should be **anchored around the 1,000,000 RSD mark** as a safe and standard starting point. If the calculation results in a slightly different figure (e.g., 950,000 or 1,100,000), it is acceptable to round it to the nearest logical amount, often 1,000,000 RSD.

                            **B3: Final Justification**
                            *   In your explanation, you must state the percentage of revenue used for the calculation and justify why that specific percentage and final amount were chosen, linking it directly to the risks and strengths you identified in step B1.
                            *   Example: "Predloženi limit od 1.000.000 RSD predstavlja oko 2% godišnjih prihoda (58.000.000 RSD). Ovaj standardni iznos za početak saradnje je adekvatan jer, iako kompanija nema gubitke, primećen je pad prihoda i broj zaposlenih je nula, što zahteva konzervativan pristup."

                            ---

                            **IMPORTANT INSTRUCTIONS FOR THE AI:**
                            *   Treat the Z-test result of "bankrotstvo" as a serious risk factor that lowers the limit, but **NOT** as an automatic "No-Go" rule on its own.

                    
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
                st.error("TXT fajl nije pronađen. Molimo pokrenite analizu ponovo.")
                logger.error(f"TXT fajl nije pronađen na putanji: {st.session_state.get('ai_comment_path')}")

        #st.write(f"Klijent: {st.session_state['client_name']}")
        # st.write(f"Komentar AI: {st.session_state['ai_comment']}")

    
        if st.button("Pokreni novu analizu"):

            #Resetovanje stanja
            st.session_state['current_stage'] = 'waiting_for_file'
            st.session_state['log_uploaded'] = False
            st.session_state['upload_in_progress'] = False 
            logger.info("Pokretanje nove analize.")
            st.rerun()