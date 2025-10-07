import os
import json
import re
from datetime import date, datetime

import pandas as pd
import matplotlib.pyplot as plt

from math import log10, floor

from openpyxl.utils import column_index_from_string, get_column_letter
# za proveru boje redova - zuti redovi su vazniji u fin listu
from openpyxl import load_workbook
from openpyxl.styles import Color
from unidecode import unidecode


from openai import OpenAI

def get_cell_value(df, cell_address):
    col_letter = ''.join(filter(str.isalpha, cell_address))
    row_number = int(''.join(filter(str.isdigit, cell_address))) - 1 # excel krece numeraciju od 1

    col_index = column_index_from_string(col_letter) - 1 # excel krece numeraciju od 1
    return df.iloc[row_number, col_index]


def find_header(file_path, sheet_name, column, range, keyword=""):
  try:
    df = pd.read_excel(file_path, sheet_name=sheet_name, usecols=f"{column}:{column}", skiprows=range[0]-1, nrows=range[1]-range[0], engine='openpyxl', header=None)
  except Exception as e:
    print(e)
    print("KOLONA")
    return -1
  #print(df)
  if not df.empty:
    if keyword != "":
      for i, value in enumerate(df.iloc[:, 0]):
        if str(value).strip() == str(keyword).strip(): 
          return range[0] + i
    else:
        for i, value in enumerate(df.iloc[:, 0]):
          if str(value).strip().lower() != "nan":
            return range[0] + i
      
  return -1 

def find_yellow_rows(file_path):
  wb = load_workbook(file_path)
  ws = wb['Fin']
  zuta_boja = "FFFFFF00"

  zuti_redovi_excel = []

  for row in ws.iter_rows(min_row=2):  # preskačemo header red
      for cell in row:
          fill = cell.fill
          # Proveravamo da li boja postoji i da li je tip boje "rgb"
          if fill.start_color.type == "rgb" and fill.start_color.rgb == zuta_boja:
              zuti_redovi_excel.append(cell.row)
              break  # ne moramo proveravati ostale ćelije u tom redu

  print("Žuti redovi su:", zuti_redovi_excel)
  return zuti_redovi_excel

def parse_date(d):
    if pd.isna(d):
        return None
    try:
        return pd.to_datetime(d).strftime('%Y-%m-%d')
    except:
        return str(d)
    

def to_JSON(file_path):
  final_json = {}

  # saradnja potencijalno prazna
  # 8. sheet je imenovan kao naziv firme
  sheet_names = ['OsnP', 'Blok', 'ImEx', 'Zabel', 'Fin', 'Saradnja']

  # osnovne informacije
  print("Obrada osnovnih informacija...")
  df_osnovne_informacije = pd.read_excel(file_path, sheet_name=sheet_names[0], usecols="A:A", skiprows=1, nrows=10, engine='openpyxl', header=None)
  df_osnovne_informacije = df_osnovne_informacije.dropna()
  #print(df_osnovne_informacije)
  osnovne_informacije_json= {}
  if len(df_osnovne_informacije.iloc[0, 0]) > 3:
    osnovne_informacije_json['naziv_komitenta'] = df_osnovne_informacije.iloc[0, 0]
    osnovne_informacije_json['status_pravnog_lica'] = df_osnovne_informacije.iloc[1, 0].replace('\xa0', '')
    osnovne_informacije_json['delatnost'] = df_osnovne_informacije.iloc[3, 0].split(':')[1]
  else:
     osnovne_informacije_json['scoring'] = df_osnovne_informacije.iloc[0, 0]
     osnovne_informacije_json['iznosEUR'] = df_osnovne_informacije.iloc[1, 0]
     osnovne_informacije_json['naziv_komitenta'] = df_osnovne_informacije.iloc[2, 0]
     osnovne_informacije_json['status_pravnog_lica'] = df_osnovne_informacije.iloc[3, 0].replace('\xa0', '')
     osnovne_informacije_json['delatnost'] = df_osnovne_informacije.iloc[5, 0].split(':')[1]

  
#   osnovne_informacije_json['maticni_broj'] = re.search(r"Matični broj:\s*(\d+)", df_osnovne_informacije.iloc[4, 0]).group(1)
#   osnovne_informacije_json['pib'] = re.search(r"PIB:\s*(\d+)", df_osnovne_informacije.iloc[4, 0]).group(1)
#   osnovne_informacije_json['pdv_status'] = re.search(r"PDV status:\s*([A-Z]+)", df_osnovne_informacije.iloc[4, 0]).group(1)
#   ulica = re.search(r"Adresa:\s*([^\s].*?)\s{2,}", df_osnovne_informacije.iloc[6, 0]).group(1)
#   mesto = re.search(r"Mesto:\s*(.+)", df_osnovne_informacije.iloc[6, 0]).group(1).replace('\xa0', '')
#   osnovne_informacije_json['adresa'] = {
#       "ulica": ulica,
#       "mesto": mesto
#   }
#   osnovne_informacije_json['delatnost'] = re.search(r"Delatnost:\s*(.+?)\s{2,}", df_osnovne_informacije.iloc[5, 0]).group(1)
#   osnovne_informacije_json['telefon'] = re.search(r"Telefon:\s*(.+)", df_osnovne_informacije.iloc[7, 0]).group(1).replace('\xa0', '')
#   match = re.search(r"Blokade:\s*(.+)", df_osnovne_informacije.iloc[8, 0])
#   osnovne_informacije_json['blokade'] = match.group(1).replace('\xa0', '') if match else None
#   match = re.search(r"Izvršenja:\s*(.+)", df_osnovne_informacije.iloc[8, 0])
#   osnovne_informacije_json['izvrsenja'] = match.group(1).replace('\xa0', '') if match else None
  
  final_json['osnovne_informacije'] = osnovne_informacije_json

  print(f"Komitent {final_json['osnovne_informacije']['naziv_komitenta']}")
  

  # osnovno poslovanje po godinama
  print("Obrada poslovanja po godinama...")
  h = find_header(file_path, sheet_names[0], "A", (11, 19), "Godina")
  #print(h)
  df_poslovanje = pd.read_excel(file_path, sheet_name=sheet_names[0], skiprows=h-1, nrows=7, engine='openpyxl', header=0)
  df_poslovanje = df_poslovanje.astype(object).where(pd.notnull(df_poslovanje), None)
  df_poslovanje = df_poslovanje.dropna(axis=1, how='all')
  print(df_poslovanje)
  poslovanje_json = df_poslovanje.to_dict(orient='records')
  # lista metrika po godinama ->  struktura po godinama
  poslovanje_po_godinama = {}

  for red in poslovanje_json:
      metrika = red['Godina'].lower().replace(' ', '_')  # npr. "Ukupna aktiva" -> "ukupna_aktiva"
      for godina in df_poslovanje.columns[1:]:
          if godina not in poslovanje_po_godinama:
              poslovanje_po_godinama[godina] = {}
          poslovanje_po_godinama[godina][metrika] = red.get(godina)
  final_json['poslovanje_po_godinama_osnovno'] = poslovanje_po_godinama


  # osnivanje
  print("Obrada detalja osnivanja...")
  df_osnivanje = pd.read_excel(file_path, sheet_name=sheet_names[0], usecols="A:B", skiprows=h+7+1, nrows=4, engine='openpyxl', header=None)
  df_osnivanje = df_osnivanje.astype(object).where(pd.notnull(df_osnivanje), None)
  df_osnivanje.columns = ['Atribut', 'Vrednost']
  osnivanje_json = df_osnivanje.to_dict(orient='records')
  osnivanje_firme = {}
  for red in osnivanje_json:
      key = red['Atribut'].lower().replace(' ', '_')
      value = red['Vrednost']
      #  datum konvertovanje u string
      if isinstance(value, datetime):
          value = value.strftime('%Y-%m-%d')
      osnivanje_firme[key] = value
  final_json['osnivanje_firme'] = osnivanje_firme

  # blokada
  print("Obrada blokada...")
  h = find_header(file_path, sheet_names[1], "A", (13, 18), "Blokade računa")
  if h == -1:
    h = find_header(file_path, sheet_names[1], "A", (14, 18))
    blokada_od2010 = pd.read_excel(file_path, sheet_name=sheet_names[1], usecols="A:A", skiprows=h-1, nrows=1, engine='openpyxl', header=None).iloc[0,0]
    final_json['blokade_od_2010'] = blokada_od2010
  else:
    df_raw = pd.read_excel(file_path, sheet_name=sheet_names[1], skiprows=h,usecols="A:D", engine='openpyxl', header=0)
    stop_index = df_raw[df_raw.iloc[:, 0].str.contains("Računi", na=False)].index[0]
    df_blokada = df_raw.iloc[:stop_index].copy()

    df_blokada.reset_index(drop=True, inplace=True)
    df_blokada.dropna(how='all', inplace=True)
    
    df_blokada = df_blokada.astype(str)
    final_json['blokade_od_2010'] = df_blokada.to_dict(orient='records')
  

  # uvoz i izvoz
  print("Obrada uvoza i izvoza...")
  h = find_header(file_path, sheet_names[2], "A", (13, 18), "Godina")
  if h == -1:
    final_json['uvoz'] = None
  else:
    uvoz = pd.read_excel(file_path, sheet_name=sheet_names[2], usecols="A:F", skiprows=h-1, engine='openpyxl', header=0).dropna(how='all')
    uvoz.columns = uvoz.columns.str.replace('\xa0', '', regex=False).str.strip()
    # PROVERA I FILTRIRANJE UVOZA
    if ( 'Godina' in uvoz.columns) and any(uvoz['Godina'].apply(lambda x: isinstance(x, (int, float)))):
        uvoz_filtered = uvoz[uvoz['Godina'].apply(lambda x: isinstance(x, (int, float)))].copy()
        uvoz_filtered.reset_index(drop=True, inplace=True)
        uvoz_filtered = uvoz_filtered.astype(object).where(pd.notnull(uvoz_filtered), None)
        uvoz_json = uvoz_filtered.to_dict(orient='records')
        final_json['uvoz'] = uvoz_json
    else:
        final_json['uvoz'] = None  # ili [] ili "Nema podataka"

  h = find_header(file_path, sheet_names[2], "H", (13, 18), "Godina")
  if h == -1:
    final_json['izvoz'] = None
  else:
    izvoz = pd.read_excel(file_path, sheet_name=sheet_names[2], usecols="H:L", skiprows=h-1, engine='openpyxl', header=0).dropna(how='all')
    izvoz.columns = izvoz.columns.str.replace('\xa0', '', regex=False).str.strip()
    # PROVERA I FILTRIRANJE IZVOZA
    # Napomena: kolona sa godinom je 'Godina.1' kod izvoza
    if  ('Godina.1' in izvoz.columns)and any(izvoz['Godina.1'].apply(lambda x: isinstance(x, (int, float)))):
        izvoz_filtered = izvoz[izvoz['Godina.1'].apply(lambda x: isinstance(x, (int, float)))].copy()
        izvoz_filtered.reset_index(drop=True, inplace=True)
        izvoz_filtered = izvoz_filtered.astype(object).where(pd.notnull(izvoz_filtered), None)
        izvoz_json = izvoz_filtered.to_dict(orient='records')
        final_json['izvoz'] = izvoz_json
    else:
        final_json['izvoz'] = None  # ili [] ili "Nema podataka"


  #  APR zabeleske
  print("Obrada apr...")
  h = find_header(file_path, sheet_names[3], "A", (15, 20), "Tip")
  #print(h)
  if h != -1:
    apr_zabeleske = pd.read_excel(file_path, sheet_name=sheet_names[3], usecols="A:C", skiprows=h-1, engine='openpyxl', header=0)
    apr_zabeleske_clean = apr_zabeleske.dropna(how='all').copy()
    print(apr_zabeleske_clean.columns)
    if 'Tip' in apr_zabeleske_clean.columns:
            apr_zabeleske_clean = apr_zabeleske_clean[apr_zabeleske_clean['Tip'].notna()]
            apr_zabeleske_clean = apr_zabeleske_clean[apr_zabeleske_clean['Tip'] != '2.1.4.0']
            apr_zabeleske_clean.reset_index(drop=True, inplace=True)

            # Izbaci nepoželjne poruke
            nezeljene_poruke = [
                "Nije pronadjena ni jedna ostala zabeležba.",
                "Nije pronadjena ni jedna obrisana APR zabeležba."
            ]
            apr_zabeleske_filtered = apr_zabeleske_clean[~apr_zabeleske_clean['Tip'].isin(nezeljene_poruke)]

            if not apr_zabeleske_filtered.empty and 'Datum' in apr_zabeleske_filtered.columns:
                apr_zabeleske_filtered['Datum'] = pd.to_datetime(
                      apr_zabeleske_filtered['Datum'], dayfirst=True,
                      format="%d.%m.%Y",  # odgovara formatu 24.7.2024
                      errors='coerce'
                  ).dt.strftime('%Y-%m-%d')

                final_json['apr_zabeleske'] = apr_zabeleske_filtered.to_dict(orient='records')
            else:
                final_json['apr_zabeleske'] = None
    else:
            print('proba')
            final_json['apr_zabeleske'] = None
  else:
    final_json['apr_zabeleske'] = None

  # saradnja
  # print("Obrada saradnja...")
  # seme = pd.read_excel(file_path, sheet_name=sheet_names[5], usecols="B:E", skiprows=3, nrows=6, engine='openpyxl', header=0)
  # pesticidi = pd.read_excel(file_path, sheet_name=sheet_names[5], usecols="B:E", skiprows= 11, nrows=6, engine='openpyxl', header=0)
  # mehanizacija = pd.read_excel(file_path, sheet_name=sheet_names[5], usecols="B:E", skiprows= 19, nrows=6, engine='openpyxl', header=0)
  # stocarstvo = pd.read_excel(file_path, sheet_name=sheet_names[5], usecols="B:E", skiprows= 27, nrows=6, engine='openpyxl', header=0)
  # berza = pd.read_excel(file_path, sheet_name=sheet_names[5], usecols="B:E", skiprows= 35, nrows=6, engine='openpyxl', header=0)
  # voce_povrce = pd.read_excel(file_path, sheet_name=sheet_names[5], usecols="B:E", skiprows= 43, nrows=6, engine='openpyxl', header=0)
  # ratarstvo = pd.read_excel(file_path, sheet_name=sheet_names[5], usecols="B:E", skiprows= 52, nrows=6, engine='openpyxl', header=0)

  # seme = seme.astype(object).where(pd.notnull(seme), None)
  # pesticidi = pesticidi.astype(object).where(pd.notnull(pesticidi), None)
  # mehanizacija = mehanizacija.astype(object).where(pd.notnull(mehanizacija), None)
  # stocarstvo = stocarstvo.astype(object).where(pd.notnull(stocarstvo), None)
  # berza = berza.astype(object).where(pd.notnull(berza), None)
  # voce_povrce = voce_povrce.astype(object).where(pd.notnull(voce_povrce), None)
  # ratarstvo = ratarstvo.astype(object).where(pd.notnull(ratarstvo), None)

  # seme_json = seme.to_dict(orient='records')
  # pesticidi_json = pesticidi.to_dict(orient='records')
  # mehanizacija_json = mehanizacija.to_dict(orient='records')
  # stocarstvo_json = stocarstvo.to_dict(orient='records')
  # berza_json = berza.to_dict(orient='records')
  # voce_povrce_json =voce_povrce.to_dict(orient='records')
  # ratarstvo_json = ratarstvo.to_dict(orient='records')

  # final_json['saradnja_sektori_rsd'] = {
  #   'seme': seme_json,
  #   'pesticidi': pesticidi_json,
  #   'mehanizacija': mehanizacija_json,
  #   'stocarstvo': stocarstvo_json,
  #   'berza': berza_json,
  #   'voce_povrce': voce_povrce_json,
  #   'ratarstvo': ratarstvo_json
  # }

  # stanje otvorenih stavki na dan analize - sheet Saradnja
  # print("Obrada otvorene stavke...")
  # otvorene_stavke = pd.read_excel(file_path, sheet_name=sheet_names[5], usecols="G:M", skiprows=3, nrows=8, engine='openpyxl', header=0)
  # otvorene_stavke = otvorene_stavke.astype(object).where(pd.notnull(otvorene_stavke), None)
  # otvorene_stavke.columns = ["Sektor"] + list(otvorene_stavke.columns[1:])
  # otvorene_stavke_json = otvorene_stavke.to_dict(orient='records')
  # otvorene_stavke_filtered = [row for row in otvorene_stavke_json if row['Sektor'].lower() != 'grand total']
  # final_json['otvorene_stavke'] = otvorene_stavke_filtered
  final_json['saradnja_sektori_rsd'] = {}

  # finansije
  print("Obrada finansije...")
  fin_blocks = {
    'bilans_stanja': (9, 128),
    'bilans_uspeha': (129, 193),
    'bilans_novcanih_troskova': (194, 252),
    'racio_analiza_likvidnosti': (254, 264),
    'racio_analiza_rentabilnost': (265, 271),
    'racio_analiza_aktivnosti': (272, 288)

  }

  zuti_redovi_excel = find_yellow_rows(file_path)
  df_fin = pd.read_excel(file_path, sheet_name='Fin', engine='openpyxl', header=None)
  df_fin = df_fin.astype(object).where(pd.notnull(df_fin), None)
  #print(df_fin.shape[1])
  if df_fin.empty:
    error_message = f'Obavezni list "Fin" je prazan.'
    print(error_message)
    raise ValueError(error_message)
  else:
    n_years = min(3, df_fin.shape[1] - 1)
    fin_json = {}
    years_row_idx = 3
    years = df_fin.iloc[years_row_idx].dropna().tolist()
    zuti_redovi_df = [r - 1 for r in zuti_redovi_excel]
    df_fin = df_fin.iloc[:, :(len(years)+1)]

    for blok, (start_row, end_row) in fin_blocks.items():
      df_block = df_fin.iloc[start_row:end_row+1].copy()

      #  redovi "žuti"
      df_block['zuti_pokazatelj'] = df_block.index.isin(zuti_redovi_df)


      # poslednjih n godina
      selected_years = years[-n_years:]

      n_cols = df_block.shape[1]
      df_final = df_block.iloc[:, [0] + list(range(n_cols - (n_years + 1), n_cols))]

      # Postavi nazive kolona
      header = ['naziv'] + selected_years + ['zuti_pokazatelj']
      df_final.columns = header

      # Pretvori u listu rečnika
      fin_json[blok] = df_final.to_dict(orient='records')

    # proveriti da li je u 000 rsd
    final_json['finansije'] = fin_json

  # ugovori
  print("Obrada ugovori...")
  df_ugovori = pd.read_excel(file_path, sheet_name=7, usecols="W:AA", skiprows=14, nrows=10, engine='openpyxl', header=0)
  df_ugovori = df_ugovori.astype(object).where(pd.notnull(df_ugovori), None)
  df_ugovori = df_ugovori.dropna(how='all',axis=0)
  df_ugovori['Zaključen'] = df_ugovori['Zaključen'].apply(parse_date)
  df_ugovori['Ispunjenje'] = df_ugovori['Ispunjenje'].apply(parse_date)
  ugovori_json = df_ugovori.to_dict(orient='records')
  final_json['ugovori'] = ugovori_json

  # pokazatelji
  # print("Obrada pokazatelji...")
  # df_pokazatelji = pd.read_excel(file_path, sheet_name=7, usecols="S:Y", skiprows=31, nrows=6, engine='openpyxl', header=0)
  # df_pokazatelji = df_pokazatelji.astype(object).where(pd.notnull(df_pokazatelji), None)
  # # Prevedi kolone
  # df_pokazatelji.columns = [unidecode(str(col)) for col in df_pokazatelji.columns]

  # # Prevedi tekst u kolonama ako ima stringova
  # for col in df_pokazatelji.select_dtypes(include=['object']).columns:
  #     df_pokazatelji[col] = df_pokazatelji[col].apply(lambda x: unidecode(str(x)) if pd.notna(x) else x)
  # pokazatelji_json = df_pokazatelji.to_dict(orient='records')
  # final_json['osnovni_pokazatelji'] = pokazatelji_json


  return json.loads(json.dumps(final_json, ensure_ascii=False, default=str))


def propose_credit_limit(prihod_rsd: float,
                             a: float = 26.54,
                             b: float = -2.46,
                             lo: float = 0.1,
                             hi: float = 3.0) -> dict:
    """
    Računa procenat i predlog kreditnog limita na osnovu godišnjeg prihoda.
    """
    if prihod_rsd <= 0:
        raise ValueError("Prihod mora biti pozitivan broj")

    pct = a + b * log10(prihod_rsd)
    pct = max(lo, min(hi, pct))  # clipping

    limit = prihod_rsd * pct / 100.0
    return {"proc_limita": pct, "predlog_limit": int(limit)}


def generate_AIcomment1(prompt, key):
  # Inicijalizuj klijenta
  client = OpenAI(api_key=key)  # Preporuka: koristi os.environ

  system_content = """
    You are an expert AI Credit Risk Analyst.
    Your task is to carefully analyze the provided client JSON data and generate a professional, ready-to-use "AI Comment" in business Serbian for a human credit risk analyst.
  """
  # Poziv modela (GPT-4.1)
  response = client.chat.completions.create(
      model="gpt-4.1",
      messages=[
          {"role": "system", "content": system_content},
          {"role": "user", "content": prompt}

      ],
      temperature=0.0
  )

  # Prikaz odgovora
  return response.choices[0].message.content

def generate_AIcomment(prompt, key):
  # Inicijalizuj klijenta
  client = OpenAI(api_key=key)  # Preporuka: koristi os.environ

  response = client.responses.create(
      model = "gpt-5-2025-08-07",
      input=prompt,
      reasoning={
          "effort": "high"
      },
      text={
          "verbosity": "high"
      },
      max_output_tokens= 15000
  )
  
  return response.output_text