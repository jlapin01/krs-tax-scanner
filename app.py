import streamlit as st
from seleniumbase import SB
import time
import os
import glob
import zipfile
import re
import pandas as pd
import shutil

# --- FUNKCJA CZYSZCZĄCA LICZBY ---
def wyciagnij_liczbe(raw_html):
    if not raw_html: return 0.0
    clean_text = re.sub(r'<[^>]+>', '', raw_html)
    clean_text = "".join(clean_text.split())
    clean_text = clean_text.replace(',', '.')
    clean_text = re.sub(r'[^\d\.\-]', '', clean_text)
    try: return float(clean_text)
    except: return 0.0

# --- GŁÓWNA LOGIKA BOTA ---
def wykonaj_analize_krs(krs, log_callback):
    results = []
    nazwa_firmy = "Nieznany podmiot"
    
    # Automatyczne szukanie przeglądarki
    executable_path = shutil.which("chromium") or shutil.which("chromium-browser") or \
                      shutil.which("google-chrome") or shutil.which("brave-browser")

    # Folder /tmp jest jedynym miejscem z prawem zapisu na Streamlit Cloud
    katalog_pobrane = "/tmp/downloads"
    if not os.path.exists(katalog_pobrane):
        os.makedirs(katalog_pobrane)

    # WYŁĄCZAMY uc=True, aby uniknąć PermissionError. 
    # Zastępujemy go agresywnym maskowaniem (agent, no-sandbox).
    with SB(uc=False, # Zmiana na False rozwiązuje błąd PermissionError
            browser="chrome",
            binary_location=executable_path,
            headless=True,
            xvfb=True,
            # Flagi maskujące i stabilizujące dla serwera
            chromium_arg="--no-sandbox,--disable-dev-shm-usage,--disable-blink-features=AutomationControlled") as sb:
        try:
            log_callback("🚀 Łączenie z systemem RDF...")
            # Ustawiamy User-Agent, żeby udawać zwykłą przeglądarkę
            sb.execute_cdp_cmd("Network.setUserAgentOverride", {
                "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            })

            sb.open("https://rdf-przegladarka.ms.gov.pl/")
            
            try:
                sb.click('button:contains("Akceptuj"), button:contains("Zgadzam się")', timeout=4)
            except: pass 

            log_callback(f"🔎 Szukanie podmiotu: {krs}...")
            sb.type("input[formcontrolname='numerKRS']", krs, timeout=20)
            sb.click("span.p-button-label:contains('Wyszukaj')", timeout=10)
            sb.wait_for_element("table", timeout=30)

            try:
                nazwa_firmy = sb.get_text("div:contains('Nazwa / firma podmiotu') + div")
                log_callback(f"🏢 Rozpoznano: {nazwa_firmy}")
            except:
                nazwa_firmy = f"KRS {krs}"

            log_callback("🎛️ Filtrowanie sprawozdań...")
            sb.click("span.p-button-label:contains('Pokaż filtry')")
            time.sleep(1)
            sb.click("span#rodzajDokumentuNazwaInput")
            time.sleep(1)
            sb.click("li:contains('Roczne sprawozdanie finansowe')")
            sb.click("button:contains('Filtruj')")
            time.sleep(3) 

            wiersze_akcji = sb.find_elements("td.actions-col")
            ile_pobrac = min(5, len(wiersze_akcji))
            
            if ile_pobrac == 0:
                log_callback("❌ Nie znaleziono sprawozdań.")
                return None, nazwa_firmy

            pobrane_archiwa = []
            for index in range(1, ile_pobrac + 1):
                log_callback(f"📥 Pobieranie rocznika {index}...")
                
                for p in glob.glob(os.path.join(katalog_pobrane, '*.zip')):
                    if p not in pobrane_archiwa:
                        try: os.remove(p)
                        except: pass

                sb.click(f"tbody tr:nth-child({index}) button")
                time.sleep(2) 
                sb.click("span.p-button-label:contains('Pobierz dokumenty')")

                # Czekanie na plik w /tmp
                sciezka_pliku = ""
                for _ in range(30):
                    time.sleep(1)
                    zips = [p for p in glob.glob(os.path.join(katalog_pobrane, '*.zip')) if p not in pobrane_archiwa]
                    if zips:
                        najnowszy = max(zips, key=os.path.getmtime)
                        sciezka_pliku = os.path.join(katalog_pobrane, f"file_{index}.zip")
                        os.rename(najnowszy, sciezka_pliku)
                        pobrane_archiwa.append(sciezka_pliku)
                        break
                
                if sciezka_pliku:
                    sb.click(f"tbody tr:nth-child({index}) button")
                    time.sleep(1)

            log_callback("🧠 Analiza danych XML...")
            for zip_path in pobrane_archiwa:
                try:
                    with zipfile.ZipFile(zip_path, 'r') as z:
                        for nazwa_pliku in z.namelist():
                            if nazwa_pliku.endswith('.xml'):
                                with z.open(nazwa_pliku) as plik_xml:
                                    tekst = plik_xml.read().decode('utf-8', errors='ignore')
                                    rok_m = re.search(r'<[^>]*?DataDo[^>]*?>(\d{4})', tekst)
                                    rok_txt = rok_m.group(1) if rok_m else "????"
                                    podatek = 0.0
                                    blok_m = re.search(r'<[^>]*?P_ID_11[^>]*?>(.*?)</[^>]*?P_ID_11>', tekst, re.DOTALL)
                                    if blok_m:
                                        rb_m = re.search(r'<[^>]*?RB[^>]*?>(.*?)</[^>]*?RB>', blok_m.group(1), re.DOTALL)
                                        if rb_m:
                                            podatek = wyciagnij_liczbe(rb_m.group(1))
                                    results.append({"Rok": rok_txt, "Podatek (RB)": podatek})
                                break 
                finally:
                    if os.path.exists(zip_path): os.remove(zip_path)
            
            return sorted(results, key=lambda x: x['Rok'], reverse=True), nazwa_firmy

        except Exception as e:
            log_callback(f"💥 Błąd krytyczny: {str(e)}")
            return None, None
        finally:
            try: sb.quit()
            except: pass

# --- UI ---
st.set_page_config(page_title="KRS Tax Scanner", page_icon="🏦", layout="wide")
st.title("🏦 Scanner Podatkowy KRS")

if 'krs_input' not in st.session_state: st.session_state.krs_input = ""

with st.sidebar:
    st.header("⚙️ Opcje")
    krs_val = st.text_input("Numer KRS:", value=st.session_state.krs_input)
    if st.button("Szukaj", use_container_width=True):
        if len(krs_val) == 10 and krs_val.isdigit():
            with st.status("🕵️ Trwa analiza...", expanded=True) as status:
                log_p = st.empty()
                logs = []
                def up(m):
                    logs.append(m)
                    log_p.code("\n".join(logs[-5:]))
                dane, nazwa = wykonaj_analize_krs(krs_val, up)
                status.update(label="Analiza zakończona!", state="complete", expanded=False)
            
            if dane and nazwa:
                st.header(f"🏢 {nazwa}")
                cl, cr = st.columns([2, 1])
                with cl:
                    df = pd.DataFrame(dane)
                    df_v = df.copy()
                    df_v["Podatek (RB)"] = df_v["Podatek (RB)"].apply(lambda x: f"{x:,.2f} PLN".replace(",", " "))
                    st.table(df_v)
                with cr:
                    suma = df["Podatek (RB)"].sum()
                    st.metric("Suma (5 lat)", f"{suma:,.2f} PLN".replace(",", " "))
            else: st.error("Błąd analizy.")
        else: st.error("Podaj 10 cyfr KRS.")

    if st.button("Reset", use_container_width=True):
        st.session_state.krs_input = ""
        st.rerun()
