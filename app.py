import streamlit as st
from seleniumbase import SB
import time
import os
import glob
import zipfile
import re
import pandas as pd
import shutil
import random

# --- KONFIGURACJA ŚRODOWISKA ---
os.environ["SELENIUMBASE_DRIVER_PATH"] = "/tmp/drivers"
os.environ["UC_DRIVER_PATH"] = "/tmp/uc_drivers"

def wyciagnij_liczbe(raw_html):
    if not raw_html: return 0.0
    # Usuwanie tagów i czyszczenie znaków
    clean_text = re.sub(r'<[^>]+>', '', raw_html)
    clean_text = "".join(clean_text.split()).replace(',', '.')
    clean_text = re.sub(r'[^\d\.\-]', '', clean_text)
    try: return float(clean_text)
    except: return 0.0

def wykonaj_analize_krs(krs, log_callback):
    results = []
    nazwa_firmy = None
    
    executable_path = shutil.which("chromium") or shutil.which("chromium-browser") or \
                      shutil.which("google-chrome") or shutil.which("brave-browser")
    
    katalog_pobrane = "/tmp/downloads"
    if os.path.exists(katalog_pobrane):
        shutil.rmtree(katalog_pobrane)
    os.makedirs(katalog_pobrane)

    # Używamy zautomatyzowanego menedżera SB - NIE wywołujemy sb.quit() ręcznie
    with SB(uc=False, browser="chrome", binary_location=executable_path, headless=True, xvfb=True,
            chromium_arg="--no-sandbox,--disable-dev-shm-usage") as sb:
        try:
            log_callback("🚀 Łączenie z bazą RDF...")
            sb.execute_cdp_cmd("Network.setUserAgentOverride", {
                "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            })
            
            sb.open("https://rdf-przegladarka.ms.gov.pl/")
            time.sleep(2)
            try: sb.click('button:contains("Akceptuj")', timeout=4)
            except: pass 

            sb.type("input[formcontrolname='numerKRS']", krs)
            sb.click("span.p-button-label:contains('Wyszukaj')")
            sb.wait_for_element("table", timeout=20)
            
            try:
                nazwa_firmy = sb.get_text("div:contains('Nazwa / firma podmiotu') + div")
                log_callback(f"🏢 Firma: {nazwa_firmy}")
            except: nazwa_firmy = f"Podmiot {krs}"

            # Filtrowanie rocznych sprawozdań
            sb.click("span.p-button-label:contains('Pokaż filtry')")
            time.sleep(1)
            sb.click("span#rodzajDokumentuNazwaInput")
            time.sleep(1)
            sb.click("li:contains('Roczne sprawozdanie finansowe')")
            sb.click("button:contains('Filtruj')")
            log_callback("⏳ Oczekiwanie na odświeżenie tabeli...")
            time.sleep(5) 

            wiersze = sb.find_elements("tbody tr")
            znalezione_pozycje = []
            for i, w in enumerate(wiersze):
                if "roczne sprawozdanie finansowe" in w.text.lower():
                    znalezione_pozycje.append(i + 1)
                if len(znalezione_pozycje) >= 5: break

            log_callback(f"📊 Znaleziono {len(znalezione_pozycje)} dokumentów do analizy.")

            pobrane_archiwa = []
            for idx, pos in enumerate(znalezione_pozycje):
                log_callback(f"📥 Pobieranie {idx+1}/{len(znalezione_pozycje)}...")
                btn = f"tbody tr:nth-child({pos}) button"
                sb.click(btn)
                time.sleep(2)
                sb.click("span.p-button-label:contains('Pobierz dokumenty')")

                # Mechanizm czekania na plik ZIP
                for _ in range(40):
                    time.sleep(1)
                    zips = glob.glob(os.path.join(katalog_pobrane, '*.zip'))
                    if zips:
                        najnowszy = max(zips, key=os.path.getmtime)
                        nowa_nazwa = os.path.join(katalog_pobrane, f"plik_{idx}.zip")
                        os.rename(najnowszy, nowa_nazwa)
                        pobrane_archiwa.append(nowa_nazwa)
                        log_callback(f"✅ Pobrano dokument {idx+1}")
                        break
                sb.click(btn) # Zwiń wiersz

            log_callback("🧠 Analiza plików XML...")
            for zip_p in pobrane_archiwa:
                try:
                    with zipfile.ZipFile(zip_p, 'r') as z:
                        for f_name in z.namelist():
                            if f_name.endswith('.xml'):
                                with z.open(f_name) as f_xml:
                                    content = f_xml.read().decode('utf-8', errors='ignore')
                                    
                                    rok_m = re.search(r'<[^>]*?DataDo[^>]*?>(\d{4})', content)
                                    rok = rok_m.group(1) if rok_m else "????"
                                    
                                    val = 0.0
                                    # Szukamy różnych tagów podatkowych (Auchan może mieć inny schemat)
                                    for tag in ['P_ID_11', 'P_ID_10', 'P_ID_9', 'P_ID_7']:
                                        patt = rf'<[^>]*?{tag}[^>]*?>(.*?)</[^>]*?{tag}>'
                                        match = re.search(patt, content, re.DOTALL)
                                        if match:
                                            rb_match = re.search(r'<[^>]*?RB[^>]*?>(.*?)</[^>]*?RB>', match.group(1), re.DOTALL)
                                            if rb_match:
                                                val = wyciagnij_liczbe(rb_match.group(1))
                                                if val != 0: 
                                                    log_callback(f"📈 {rok}: Znaleziono dane w {tag}")
                                                    break
                                    
                                    results.append({"Rok": rok, "Podatek (RB)": val})
                                break 
                except Exception as e_zip:
                    log_callback(f"⚠️ Błąd ZIP: {e_zip}")
                finally:
                    if os.path.exists(zip_p): os.remove(zip_p)

            return results, nazwa_firmy
        except Exception as e:
            log_callback(f"💥 Błąd krytyczny bota: {e}")
            return [], nazwa_firmy

# --- INTERFEJS ---
st.set_page_config(page_title="KRS Tax Scanner", layout="wide")
st.title("📊 Analityk Podatkowy KRS")

with st.sidebar:
    krs_input = st.text_input("Numer KRS", max_chars=10)
    przycisk = st.button("Szukaj")

if przycisk and krs_input:
    with st.status("Praca bota...", expanded=True) as status:
        l_area = st.empty()
        l_list = []
        def my_log(m):
            l_list.append(m); l_area.code("\n".join(l_list[-5:]))
        
        dane, nazwa = wykonaj_analize_krs(krs_input, my_log)
        status.update(label="Analiza zakończona", state="complete")

    if nazwa:
        st.header(nazwa)
        if dane:
            df = pd.DataFrame(dane).sort_values("Rok", ascending=False)
            df_v = df.copy()
            df_v["Podatek (RB)"] = df_v["Podatek (RB)"].apply(lambda x: f"{x:,.2f} PLN")
            st.table(df_v)
            
            suma = sum(d['Podatek (RB)'] for d in dane)
            st.metric("Suma podatku", f"{suma:,.2f} PLN")
        else:
            st.warning("Nie znaleziono dokumentów lub tagów podatkowych.")
