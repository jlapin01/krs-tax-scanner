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

# Konfiguracja środowiska
os.environ["SELENIUMBASE_DRIVER_PATH"] = "/tmp/drivers"
os.environ["UC_DRIVER_PATH"] = "/tmp/uc_drivers"

def wyciagnij_liczbe(raw_html):
    if not raw_html: return 0.0
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
    if not os.path.exists(katalog_pobrane): os.makedirs(katalog_pobrane)

    with SB(uc=False, browser="chrome", binary_location=executable_path, headless=True, xvfb=True,
            chromium_arg="--no-sandbox,--disable-dev-shm-usage,--disable-blink-features=AutomationControlled") as sb:
        try:
            log_callback("🚀 Łączenie z RDF...")
            sb.execute_cdp_cmd("Network.setUserAgentOverride", {
                "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            })
            sb.open("https://rdf-przegladarka.ms.gov.pl/")
            time.sleep(2)
            try: sb.click('button:contains("Akceptuj"), button:contains("Zgadzam się")', timeout=4)
            except: pass 

            log_callback(f"🔎 Szukanie KRS: {krs}...")
            sb.type("input[formcontrolname='numerKRS']", krs)
            sb.click("span.p-button-label:contains('Wyszukaj')")
            sb.wait_for_element("table", timeout=30)
            
            try:
                nazwa_firmy = sb.get_text("div:contains('Nazwa / firma podmiotu') + div")
                log_callback(f"🏢 Firma: {nazwa_firmy}")
            except: nazwa_firmy = f"KRS {krs}"

            log_callback("🎛️ Filtrowanie i analiza listy dokumentów...")
            sb.click("span.p-button-label:contains('Pokaż filtry')")
            time.sleep(1)
            sb.click("span#rodzajDokumentuNazwaInput")
            time.sleep(1)
            sb.click("li:contains('Roczne sprawozdanie finansowe')")
            sb.click("button:contains('Filtruj')")
            time.sleep(5) # Czekamy na odświeżenie tabeli

            # --- INTELIGENTNE WYBIERANIE WIERSZY ---
            wiersze = sb.find_elements("tbody tr")
            pobrane_archiwa = []
            
            # Przeszukujemy wiersze, szukając tych, które są sprawozdaniami
            znalezione_wiersze = []
            for i, wiersz in enumerate(wiersze):
                tekst_wiersza = wiersz.text.lower()
                if "roczne sprawozdanie finansowe" in tekst_wiersza:
                    znalezione_wiersze.append(i + 1)
                if len(znalezione_wiersze) >= 5: break # Max 5 lat

            log_callback(f"📊 Znaleziono {len(znalezione_wiersze)} właściwych dokumentów.")

            for index in znalezione_wiersze:
                try:
                    log_callback(f"📥 Pobieranie dokumentu z pozycji {index}...")
                    
                    # Czyścimy stare ZIPy
                    for p in glob.glob(os.path.join(katalog_pobrane, '*.zip')):
                        if p not in pobrane_archiwa:
                            try: os.remove(p)
                            except: pass

                    btn = f"tbody tr:nth-child({index}) button"
                    sb.click(btn)
                    time.sleep(2) 
                    sb.click("span.p-button-label:contains('Pobierz dokumenty')")

                    for _ in range(45):
                        time.sleep(1)
                        zips = [p for p in glob.glob(os.path.join(katalog_pobrane, '*.zip')) if p not in pobrane_archiwa]
                        if zips:
                            najnowszy = max(zips, key=os.path.getmtime)
                            nowa_sciezka = os.path.join(katalog_pobrane, f"spr_{index}.zip")
                            os.rename(najnowszy, nowa_sciezka)
                            pobrane_archiwa.append(nowa_sciezka)
                            break
                    
                    sb.click(btn) # Zamknij szczegóły
                    time.sleep(1)
                except: continue

            log_callback("🧠 Głęboka analiza XML...")
            for zip_path in pobrane_archiwa:
                try:
                    with zipfile.ZipFile(zip_path, 'r') as z:
                        for plik in z.namelist():
                            if plik.endswith('.xml'):
                                with z.open(plik) as xml_file:
                                    txt = xml_file.read().decode('utf-8', errors='ignore')
                                    rok_m = re.search(r'<[^>]*?DataDo[^>]*?>(\d{4})', txt)
                                    rok = rok_m.group(1) if rok_m else "????"
                                    
                                    val = 0.0
                                    # Szukamy P_ID_11, P_ID_10 lub P_ID_9 (różne warianty sprawozdań)
                                    for tag_id in ['P_ID_11', 'P_ID_10', 'P_ID_9']:
                                        blok = re.search(rf'<[^>]*?{tag_id}[^>]*?>(.*?)</[^>]*?{tag_id}>', txt, re.DOTALL)
                                        if blok:
                                            rb = re.search(r'<[^>]*?RB[^>]*?>(.*?)</[^>]*?RB>', blok.group(1), re.DOTALL)
                                            if rb:
                                                val = wyciagnij_liczbe(rb_m.group(1) if 'rb_m' in locals() else rb.group(1))
                                                if val != 0: break
                                    
                                    results.append({"Rok": rok, "Podatek (RB)": val})
                                    log_callback(f"✅ Rok {rok}: Odczytano {val:,.2f} PLN")
                                break 
                finally:
                    if os.path.exists(zip_path): os.remove(zip_path)
            
            return results, nazwa_firmy
        except Exception as e:
            log_callback(f"💥 Błąd: {str(e)}")
            return None, nazwa_firmy
        finally:
            try: sb.quit()
            except: pass

# --- UI ---
st.set_page_config(page_title="Scanner Podatkowy KRS", page_icon="🏦", layout="wide")
st.title("🏦 Scanner Podatkowy KRS")

if 'krs' not in st.session_state: st.session_state.krs = ""

with st.sidebar:
    st.header("⚙️ Konfiguracja")
    krs_input = st.text_input("Numer KRS:", value=st.session_state.krs, max_chars=10)
    if st.button("Szukaj", use_container_width=True):
        with st.status("🕵️ Bot w akcji...", expanded=True) as status:
            log_a = st.empty()
            log_l = []
            def loguj(m):
                log_l.append(m); log_a.code("\n".join(log_l[-5:]))
            dane, nazwa = wykonaj_analize_krs(krs_input, loguj)
            status.update(label="Analiza zakończona!", state="complete")
        
        if nazwa:
            st.header(f"🏢 {nazwa}")
            if dane:
                df = pd.DataFrame(dane).sort_values(by="Rok", ascending=False)
                df["Podatek (RB)"] = df["Podatek (RB)"].apply(lambda x: f"{x:,.2f} PLN")
                st.table(df)
                suma = pd.DataFrame(dane)["Podatek (RB)"].sum()
                st.metric("Suma podatku", f"{suma:,.2f} PLN")
            else: st.warning("Pobrano dokumenty, ale nie znaleziono w nich oczekiwanych tagów finansowych.")
