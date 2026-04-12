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

# --- KONFIGURACJA ŚRODOWISKA DLA STREAMLIT CLOUD ---
os.environ["SELENIUMBASE_DRIVER_PATH"] = "/tmp/drivers"
os.environ["UC_DRIVER_PATH"] = "/tmp/uc_drivers"

# --- POMOCNICZA FUNKCJA CZYSZCZĄCA LICZBY ---
def wyciagnij_liczbe(raw_html):
    if not raw_html: return 0.0
    # Usuwanie tagów i wszelkich białych znaków (w tym twardych spacji)
    clean_text = re.sub(r'<[^>]+>', '', raw_html)
    clean_text = "".join(clean_text.split())
    # Polskie przecinki na kropki i zostawienie tylko cyfr/znaku minus
    clean_text = clean_text.replace(',', '.')
    clean_text = re.sub(r'[^\d\.\-]', '', clean_text)
    try:
        return float(clean_text)
    except:
        return 0.0

# --- GŁÓWNA LOGIKA BOTA ---
def wykonaj_analize_krs(krs, log_callback):
    results = []
    nazwa_firmy = None
    
    # Szukanie przeglądarki na serwerze
    executable_path = shutil.which("chromium") or shutil.which("chromium-browser") or \
                      shutil.which("google-chrome") or shutil.which("brave-browser")

    # Folder roboczy w /tmp
    katalog_pobrane = "/tmp/downloads"
    if not os.path.exists(katalog_pobrane):
        os.makedirs(katalog_pobrane)

    # Uruchomienie bota (uc=False dla stabilności uprawnień w chmurze)
    with SB(uc=False, 
            browser="chrome", 
            binary_location=executable_path, 
            headless=True, 
            xvfb=True,
            chromium_arg="--no-sandbox,--disable-dev-shm-usage,--disable-blink-features=AutomationControlled") as sb:
        try:
            log_callback("🚀 Start sesji (maskowanie przeglądarki)...")
            sb.execute_cdp_cmd("Network.setUserAgentOverride", {
                "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            })
            
            sb.open("https://rdf-przegladarka.ms.gov.pl/")
            time.sleep(2)

            try:
                sb.click('button:contains("Akceptuj"), button:contains("Zgadzam się")', timeout=4)
            except: pass 

            log_callback(f"🔎 Wyszukiwanie KRS: {krs}...")
            sb.type("input[formcontrolname='numerKRS']", krs, timeout=20)
            time.sleep(random.uniform(0.5, 1.2))
            sb.click("span.p-button-label:contains('Wyszukaj')")
            
            sb.wait_for_element("table", timeout=30)
            
            # Pobieranie nazwy firmy
            try:
                nazwa_firmy = sb.get_text("div:contains('Nazwa / firma podmiotu') + div")
                log_callback(f"🏢 Znaleziono: {nazwa_firmy}")
            except:
                nazwa_firmy = f"Podmiot o KRS {krs}"

            log_callback("🎛️ Filtrowanie sprawozdań finansowych...")
            sb.click("span.p-button-label:contains('Pokaż filtry')")
            time.sleep(1)
            sb.click("span#rodzajDokumentuNazwaInput")
            time.sleep(1)
            sb.click("li:contains('Roczne sprawozdanie finansowe')")
            sb.click("button:contains('Filtruj')")
            time.sleep(4) 

            wiersze = sb.find_elements("td.actions-col")
            ile_pobrac = min(5, len(wiersze))
            
            if ile_pobrac == 0:
                log_callback("ℹ️ Brak dokumentów spełniających kryteria.")
                return [], nazwa_firmy

            pobrane_archiwa = []
            for index in range(1, ile_pobrac + 1):
                try:
                    log_callback(f"📂 Przetwarzanie dokumentu {index}/{ile_pobrac}...")
                    
                    # Czyścimy stare pliki
                    for p in glob.glob(os.path.join(katalog_pobrane, '*.zip')):
                        if p not in pobrane_archiwa:
                            try: os.remove(p)
                            except: pass

                    btn = f"tbody tr:nth-child({index}) button"
                    sb.click(btn)
                    time.sleep(2) 
                    sb.click("span.p-button-label:contains('Pobierz dokumenty')")

                    # Oczekiwanie na ZIP
                    for _ in range(40):
                        time.sleep(1)
                        zips = [p for p in glob.glob(os.path.join(katalog_pobrane, '*.zip')) if p not in pobrane_archiwa]
                        if zips:
                            najnowszy = max(zips, key=os.path.getmtime)
                            nowa_sciezka = os.path.join(katalog_pobrane, f"spr_{index}.zip")
                            os.rename(najnowszy, nowa_sciezka)
                            pobrane_archiwa.append(nowa_sciezka)
                            break
                    
                    sb.click(btn) # Zwijamy wiersz
                    time.sleep(random.uniform(1.0, 2.0))
                except:
                    log_callback(f"⚠️ Błąd przy pobieraniu dokumentu {index}")

            log_callback("🧠 Analiza XML (szukanie tagu P_ID_11)...")
            for zip_path in pobrane_archiwa:
                try:
                    with zipfile.ZipFile(zip_path, 'r') as z:
                        for plik in z.namelist():
                            if plik.endswith('.xml'):
                                with z.open(plik) as xml_file:
                                    txt = xml_file.read().decode('utf-8', errors='ignore')
                                    
                                    # Rok
                                    rok_m = re.search(r'<[^>]*?DataDo[^>]*?>(\d{4})', txt)
                                    rok = rok_m.group(1) if rok_m else "????"
                                    
                                    # Podatek (P_ID_11)
                                    val = 0.0
                                    blok = re.search(r'<[^>]*?P_ID_11[^>]*?>(.*?)</[^>]*?P_ID_11>', txt, re.DOTALL)
                                    if blok:
                                        rb = re.search(r'<[^>]*?RB[^>]*?>(.*?)</[^>]*?RB>', blok.group(1), re.DOTALL)
                                        if rb:
                                            val = wyciagnij_liczbe(rb.group(1))
                                            log_callback(f"✅ Rok {rok}: Znaleziono pozycję P_ID_11.")
                                    else:
                                        log_callback(f"ℹ️ Rok {rok}: Brak tagu P_ID_11.")
                                    
                                    results.append({"Rok": rok, "Podatek (RB)": val})
                                break 
                finally:
                    if os.path.exists(zip_path): os.remove(zip_path)
            
            return results, nazwa_firmy

        except Exception as e:
            log_callback(f"💥 Błąd krytyczny: {str(e)}")
            return None, nazwa_firmy
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
    st.header("⚙️ Konfiguracja")
    krs_val = st.text_input("Numer KRS:", value=st.session_state.krs_input, max_chars=10)
    
    col1, col2 = st.columns(2)
    with col1:
        uruchom = st.button("Analizuj", use_container_width=True)
    with col2:
        if st.button("Reset", use_container_width=True):
            st.session_state.krs_input = ""
            st.rerun()

if uruchom:
    if len(krs_val) == 10 and krs_val.isdigit():
        with st.status("🕵️ Bot wchodzi do systemu Ministerstwa...", expanded=True) as status:
            log_area = st.empty()
            log_list = []
            def logger(m):
                log_list.append(m)
                log_area.code("\n".join(log_list[-5:]))

            dane, nazwa = wykonaj_analize_krs(krs_val, logger)
            status.update(label="Analiza zakończona!", state="complete", expanded=False)
        
        if nazwa:
            st.divider()
            st.header(f"🏢 {nazwa}")
            
            if not dane:
                st.warning("Pobrano pliki, ale nie znaleziono w nich danych o podatku (tag P_ID_11).")
            else:
                cl, cr = st.columns([2, 1])
                with cl:
                    df = pd.DataFrame(dane).sort_values(by="Rok", ascending=False)
                    df_v = df.copy()
                    df_v["Podatek (RB)"] = df_v["Podatek (RB)"].apply(lambda x: f"{x:,.2f} PLN".replace(",", " "))
                    st.subheader("Dane historyczne")
                    st.table(df_v)
                with cr:
                    st.subheader("Podsumowanie")
                    suma = df["Podatek (RB)"].sum()
                    st.metric("Suma podatku (zbadane lata)", f"{suma:,.2f} PLN".replace(",", " "))
                    st.info("Powyższa suma to agregacja wartości z pozycji P_ID_11 (Rok Bieżący).")
        else:
            st.error("Nie udało się połączyć z serwerami Ministerstwa. Spróbuj ponownie za chwilę.")
    else:
        st.error("Proszę podać poprawny, 10-cyfrowy numer KRS.")
