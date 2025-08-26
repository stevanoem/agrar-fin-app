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
                            You are an expert Credit Risk Analyst AI. Your primary function is to analyze the provided JSON data for a client and generate a comprehensive "AI Comment" in Serbian. Your process must strictly emulate the decision-making logic of a human credit risk analyst as described in our internal guidelines.

                                **CRITICAL OUTPUT RULES:**
                                1.  **NO TECHNICAL JARGON:** Write in professional business Serbian. DO NOT mention JSON field names. Describe the meaning of the data in business terms.
                                2.  **PROVIDE SUPPORTING DATA:** Every analytical statement must be backed by key data points in parentheses.
                                3.  **SOURCE OF TRUTH FOR DATA:** For all financial calculations (revenue, profit, ratios, etc.), you MUST exclusively use the data from the detailed financial statements located under the "finansije" key. IGNORE the summary data in "poslovanje_po_godinama_osnovno" for calculations, as it may be inconsistent.
                                4.  **DO NOT ROUND:** Do not round the final credit limit. Provide the exact calculated number.

                                **INPUT DATA DESCRIPTION (For your internal processing only):**
                                *   The JSON contains multiple sections with client data.
                                *   **CRITICAL:** All financial values in the `poslovanje_po_godinama_osnovno` and `finansije` sections are in **THOUSANDS of RSD (000 RSD)**. When you present these numbers in your report, you must correctly state them (e.g., a value of `5719839` should be presented as "5.719.839 hiljada RSD" or "5.719.839.000 RSD").
                                *   `saradnja_sektori_rsd`: Data on historical cooperation. An empty object  means it's a new client.
                                *   `blokade_od_2010`: Information on account blockades.
                                *   `uvoz` / `izvoz`: Financial values are in EUR.

                                ---

                                **STEP 1: ANALYSIS GUIDELINES & HIERARCHICAL DECISION LOGIC**

                                **STEP 1.1: Universal "No-Go" Conditions (Limit = 0 RSD)**
                                *   First, check for these absolute deal-breakers. If any are true for the most recent year, assign a **0 RSD limit**, state the reason clearly, and STOP the analysis.
                                    1.  **Operating Loss (`Poslovni gubitak`)**: The company has an operating loss.
                                    2.  **Recent Account Blockades (`Blokade`)**: There are recorded blockades in the last 3 years.
                                    3.  **Negative Net Working Capital (`Neto obrtna sredstva`)**: Net Working Capital has been negative for two consecutive years.

                                **STEP 1.2: Determine Client Type and Analysis Path**
                                *   If the `saradnja_sektori_rsd` object is empty -> **PATH B: NEW CLIENT**.
                                *   If there is significant data in `saradnja_sektori_rsd` -> **PATH A: EXISTING CLIENT**.

                                ---

                                **PATH A: EXISTING CLIENT ANALYSIS**
                                *   (This path is for future development). The primary basis for the limit is the history of cooperation and payment discipline. The limit can be higher, potentially up to 5% of realized revenue with us, if payment is orderly.

                                ---

                                **PATH B: NEW CLIENT ANALYSIS (Strict Deterministic Approach)**

                                **Principle:** The analysis is based purely on financial statements. The credit limit is calculated using a penalty-based system starting from a conservative baseline.

                                **B1: Starting Point**
                                *   The starting point for the credit limit is **2% of the most recent year's Total Revenue (`Ukupni prihodi`)**.

                                **B2: Hierarchical Risk-Based Penalties**
                                *   Apply penalties sequentially according to the strict hierarchical order below.
                                *   Stop applying penalties once the final percentage reaches the **minimum floor of 1%**.
                                *   The final output must be fully deterministic. Always evaluate indicators in this exact order.
                                *   Even if a penalty is not applied because the floor is reached, you must still list all identified risk factors in the "Ključni faktori rizika" section to provide a complete profile.

                                **B2.1: Hierarchical List of Indicators and Penalties (Refined based on analyst feedback)**
                                1.  **Declining Business Revenue (`Poslovni prihodi`)**: If business revenues show a declining trend over the last 3 years. → **–0.5%**
                                2.  **Negative Net Result (`Neto rezultat`)**: If the net result is negative (but operating result is positive). → **–0.3%**
                                3.  **Imbalance of Receivables vs. Payables**: If Trade Payables (`Obaveze iz poslovanja`) are significantly higher than Trade Receivables (`Potraživanja po osnovu prodaje`) in the last year. → **–0.2%**
                                4.  **Low Current Ratio (`Opšti racio likvidnosti`)**: If the ratio is below 2.0 in the last year. → **–0.2%**
                                5.  **Low Quick Ratio (`Rigorozni racio likvidnosti`)**: If the ratio is below 1.0 in the last year. → **–0.2%**
                                6.  **High Days Sales Outstanding (DSO)**: If DSO (`Prosecan broj dana naplate potrazivanja`) is above 120 days OR shows a significant increasing trend. → **–0.2%**
                                7.  **High Leverage (`Koeficijent zaduženosti`)**: If the leverage ratio is above 0.7 OR shows a significant increasing trend. → **–0.2%**
                                8.  **Declining Profitability (Operating Margin)**: If the operating profit margin (`Poslovni dobitak` / `Poslovni prihodi`) is in a clear declining trend. → **–0.1%**
                                9.  **Other Red Flags (Informational, lower penalty)**: If Z-Test indicates "bankrotstvo" or "opasnost", or if the number of employees is zero for an established company. → **–0.1%**

                                **B3: Final Justification**
                                *   In the "Predlog limita i obrazloženje" section, **do not show the step-by-step arithmetic** (e.g., "2% - 0.5% = 1.5%"). Instead, state the final calculated percentage and the exact credit limit. Then, provide a concise narrative justification. This justification should holistically explain that the final percentage was determined by applying reductions from the starting point of 2% due to specific, identified risk factors, and then list those factors with their corresponding indicator numbers.
                                *   Mention if the limit is conditional on obtaining collateral (e.g., "Predlog limita je uslovljen potpisivanjem ugovora i dobijanjem menica kao sredstva obezbeđenja.").
                                ---

                                **STEP 2: FORMATTING THE FINAL "AI COMMENT"**
                                *   **Tip klijenta i osnova za analizu**
                                *   **Ukupna procena**
                                *   **Ključni faktori rizika**
                                *   **Pozitivni pokazatelji**
                                *   **Predlog limita i obrazloženje**

                                --- START OF CLIENT JSON DATA ---
                                {json_content_for_ai}
                                ---
                            """

                prompt_text1 = f"""
                                You are an expert Credit Risk Analyst AI. Your task is to analyze the provided JSON data for a client and generate a professional "AI Comment" in Serbian, suitable for a human credit risk analyst.

                                ==========================
                                STEP 0: INPUT DATA DESCRIPTION
                                ==========================
                                - saradnja_sektori_rsd: historical cooperation; if empty → PATH B (new client)
                                - poslovanje_po_godinama_osnovno: yearly business metrics (000 RSD)
                                - finansije: detailed financial statements (000 RSD)
                                - blokade_od_2010: account block info
                                - ugovori: contracts
                                - uvoz/izvoz: financial values in EUR
                                - Other fields: apr_zabeleske etc.

                                ==========================
                                STEP 1: DETERMINISTIC RED FLAG COUNT
                                ==========================
                                - Count red flags strictly based on the **22 indicators** below (include Z-test as one flag):
                                    1. Visina poslovnog dobitka/gubitka (negative EBIT/EBITDA)
                                    2. Visina poslovnih prihoda/rashoda (declining revenue)
                                    3. Vanbilansna aktiva/pasiva (high off-balance items)
                                    4. Kratkoročne finansijske obaveze (high current liabilities)
                                    5. Dugoročne obaveze (high long-term debt)
                                    6. Odnos potraživanja i obaveza iz poslovanja (unfavorable DSO/DPO)
                                    7. Gubitak iznad visine kapitala (loss > equity)
                                    8. Osnovni kapital (low base capital)
                                    9. Stalna imovina (low fixed assets)
                                    10. Dani vezivanja obaveza prema dobavljačima (high DPO)
                                    11. Dani vezivanja zaliha (high DSI)
                                    12. Dani vezivanja potraživanja od kupaca (high DSO)
                                    13. Ukupne finansijske obaveze (high total liabilities)
                                    14. Koeficijent finansijske stabilnosti (low financial stability ratio)
                                    15. Koeficijent zaduženosti (high leverage ratio)
                                    16. Neto obrtni fond (negative net working capital)
                                    17. Rigorozni racio likvidnosti (low stringent liquidity ratio)
                                    18. Opšti racio likvidnosti (low current ratio)
                                    19. Neto prinos na kapital (low return on equity)
                                    20. EBITDA (negative)
                                    21. EBITDA marža (low EBITDA margin)
                                    22. Bruto marža na prodaju (low gross margin)
                                - Apply hard limits based on total red flags:
                                    | Red Flags | Credit Limit (RSD) |
                                    |-----------|------------------|
                                    | 0–2       | 1,000,000        |
                                    | 3–4       | 950,000          |
                                    | 5–9       | 800,000          |
                                    | ≥10       | 600,000          |
                                - Output the exact number as `LIMIT_RSD`. **Do not round or give ranges.**
                                - Each of the 22 indicators must be counted exactly once. Do not double-count correlated metrics. For example, EBITDA marža and EBITDA are counted separately, but do not count the same negative EBITDA twice.
                                - No-Go conditions (Limit = 0) override table:
                                    * Operating loss in most recent year
                                    * Account block in last 3 years
                                    * Negative net working capital for 2 consecutive years

                                ==========================
                                STEP 2: GENERATE AI COMMENT
                                ==========================
                                - Use the red flag count from STEP 1 to justify the limit.
                                - Structure output using business Serbian and include supporting data in parentheses.
                                - Sections:
                                    1. Tip klijenta i osnova za analizu
                                    2. Ukupna procena (financial and risk overview, key numbers)
                                    3. Ključni faktori rizika (list red flags with supporting numbers)
                                    4. Pozitivni pokazatelji (list with supporting numbers)
                                    5. Predlog limita i obrazloženje
                                        - Must reference `LIMIT_RSD`
                                        - Clearly explain why that limit is applied based on identified red flags and positive indicators

                                ==========================
                                IMPORTANT:
                                ==========================
                                - The output must be **deterministic**: same input → same red flag count → same limit.
                                - Z-test result of "bankrotstvo" is counted as one red flag.
                                - The AI Comment must include both the **exact credit limit** and **a full, human-readable explanation** with supporting numbers.
                                - Do not create multiple versions or ranges for the limit.

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