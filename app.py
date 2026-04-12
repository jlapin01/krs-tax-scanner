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

# Konfiguracja środowiska dla zapisu w /tmp
os.environ["SELENIUMBASE_DRIVER_PATH"] = "/tmp/drivers"
os.environ["UC_DRIVER_PATH"] = "/tmp/uc_drivers"

def wyciagnij_liczbe(raw_html):
    if not raw_html: return 0.0
    clean_text = re.sub(r'<[^>]+>', '', raw_html)
    clean_text = "".join(clean_text.split())
    clean_text = clean_text.replace(',', '.')
    clean_text = re.sub(r'[^\d\.\-]', '', clean_text)
    try: return float(clean_text)
    except: return 0.0

def wykonaj_analize_krs(krs, log_callback):
    results = []
    nazwa_firmy = "Nieznany podmiot"
    
    # Lokalizacja Chromium na serwerze
    executable_path = shutil.which("chromium") or shutil.which("chromium-browser") or \
                      shutil.which("google-chrome") or shutil.which("brave-browser")

    katalog_pobrane = "/tmp/downloads"
    if not os.path.exists(katalog_pobrane):
        os.makedirs(katalog_pobrane)

    # Używamy uc=False dla stabilności uprawnień na Streamlit Cloud
    with SB(uc=False, 
            browser="chrome", 
            binary_location=executable_path, 
            headless=True, 
            xvfb=True,
            chromium_arg="--no-sandbox,--disable-dev-shm-usage,--disable-blink-features=AutomationControlled") as sb:
        try:
            log_callback("🚀 Łączenie z RDF (próba ominięcia blokad)...")
            # Udajemy zwykłego użytkownika Chrome
            sb.execute_cdp_cmd("Network.setUserAgentOverride", {
                "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            })
            
            sb.open("https://rdf-przegladarka.ms.gov.pl/")
            time.sleep(2)

            try:
                sb.click('button:contains("Akceptuj"), button:contains("Zgadzam się")', timeout=5)
            except: pass 

            log_callback(f"🔎 Szukanie podmiotu KRS: {krs}...")
            sb.type("input[formcontrolname='numerKRS']", krs, timeout=20)
            time.sleep(random.uniform(0.5, 1.5))
            sb.click("span.p-button-label:contains('Wyszukaj')", timeout=10)
            
            sb.wait_for_element("table", timeout=30)
            
            try:
                nazwa_firmy = sb.get_text("div:contains('Nazwa / firma podmiotu') + div")
                log_callback(f"🏢 Firma: {nazwa_firmy}")
            except:
                nazwa_firmy = f"KRS {krs}"

            log_callback("🎛️ Filtrowanie dokumentów rocznych...")
            sb.click("span.p-button-label:contains('Pokaż filtry')")
            time.sleep(1)
            sb.click("span#rodzajDokumentuNazwaInput")
            time.sleep(1)
            sb.click("li:contains('Roczne sprawozdanie finansowe')")
            time.sleep(0.5)
            sb.click("button:contains('Filtruj')")
            
            # Czekamy aż tabela się przeładuje
            time.sleep(4) 

            wiersze_akcji = sb.find_elements("td.actions-col")
            ile_pobrac = min(5, len(wiersze_akcji))
            
            if ile_pobrac == 0:
                log_callback("❌ Brak dokumentów do pobrania.")
                return None, nazwa_firmy

            pobrane_archiwa = []
            for index in range(1, ile_pobrac + 1):
                try:
                    log_callback(f"📂 Przetwarzanie dokumentu {index} z {ile_pobrac}...")
                    
                    # Czyścimy stare ZIPy
                    for p in glob.glob(os.path.join(katalog_pobrane, '*.zip')):
                        if p not in pobrane_archiwa:
                            try: os.remove(p)
                            except: pass

                    # Klikamy guzik wiersza
                    selector_btn = f"tbody tr:nth-child({index}) button"
                    sb.wait_for_element(selector_btn, timeout=10)
                    sb.click(selector_btn)
                    time.sleep(2) 

                    # Klikamy Pobierz
                    log_callback(f"📥 Pobieranie pliku nr {index}...")
                    sb.click("span.p-button-label:contains('Pobierz dokumenty')")

                    # Czekanie na zakończenie pobierania (max 45 sekund na plik)
                    sciezka_pliku = ""
                    for sekunda in range(45):
                        time.sleep(1)
                        zips = [p for p in glob.glob(os.path.join(katalog_pobrane, '*.zip')) if p not in pobrane_archiwa]
                        if zips:
                            najnowszy = max(zips, key=os.path.getmtime)
                            sciezka_pliku = os.path.join(katalog_pobrane, f"rok_{index}.zip")
                            os.rename(najnowszy, sciezka_pliku)
                            pobrane_archiwa.append(sciezka_pliku)
                            log_callback(f"✅ Plik {index} odebrany.")
                            break
                    
                    # Zwijamy wiersz (ważne dla stabilności tabeli)
                    sb.click(selector_btn)
                    time.sleep(random.uniform(1.0, 2.5))
                    
                except Exception as e_loop:
                    log_callback(f"⚠️ Pominąłem dokument {index} z powodu błędu: {str(e_loop)[:50]}...")
                    continue

            log_callback("🧠 Analiza XML (szukanie P_ID_11)...")
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
                                    # Szukanie P_ID_11 (bez względu na prefix ns1, dtsf itd.)
                                    blok_m = re.search(r'<[^>]*?P_ID_11[^>]*?>(.*?)</[^>]*?P_ID_11>', tekst, re.DOTALL)
                                    if blok_m:
                                        rb_m = re.search(r'<[^>]*?RB[^>]*?>(.*?)</[^>]*?RB>', blok_m.group(1), re.DOTALL)
                                        if rb_m:
                                            podatek = wyciagnij_liczbe(rb_m.group(1))
                                    results.append({"Rok": rok_txt, "Podatek (RB)": podatek})
                                break 
                finally:
                    if os.path.exists(zip_path): os.remove(zip_path)
            
            log_callback("🧹 Kończenie pracy bota...")
            return sorted(results, key=lambda x: x['Rok'], reverse=True), nazwa_firmy

        except Exception as e:
            log_callback(f"💥 Błąd krytyczny: {str(e)}")
            return None, None
        finally:
            try: sb.quit()
            except: pass
            os.system("pkill -9 chromium > /dev/null 2>&1")

# --- INTERFEJS STREAMLIT ---
st.set_page_config(page_title="Scanner Podatkowy KRS", page_icon="📊", layout="wide")
st.title("📊 Analityk Podatkowy KRS")

if 'krs_input' not in st.session_state:
    st.session_state.krs_input = ""

with st.sidebar:
    st.header("⚙️ Opcje")
    krs_val = st.text_input("Numer KRS:", value=st.session_state.krs_input)
    if st.button("Szukaj", use_container_width=True):
        if len(krs_val) == 10 and krs_val.isdigit():
            with st.status("🕵️ Praca bota w toku...", expanded=True) as status:
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
                    st.info("Wyekstrahowano z tagu P_ID_11 XML.")
            else:
                st.error("Wystąpił błąd lub brak danych. Sprawdź logi powyżej.")
        else:
            st.error("Błędny KRS (musi być 10 cyfr).")

    if st.button("Reset", use_container_width=True):
        st.session_state.krs_input = ""
        st.rerun()
