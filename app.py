import streamlit as st
from seleniumbase import SB
import time
import os
import glob
import zipfile
import re
import pandas as pd
import shutil

# --- KONFIGURACJA ŚRODOWISKA DLA STREAMLIT CLOUD ---
# Wymuszamy zapis sterowników w folderze /tmp, który ma uprawnienia do zapisu
os.environ["SELENIUMBASE_DRIVER_PATH"] = "/tmp/drivers"
os.environ["UC_DRIVER_PATH"] = "/tmp/uc_drivers"

# --- FUNKCJA CZYSZCZĄCA LICZBY ---
def wyciagnij_liczbe(raw_html):
    if not raw_html:
        return 0.0
    # Usuń tagi i białe znaki (w tym twarde spacje \xa0)
    clean_text = re.sub(r'<[^>]+>', '', raw_html)
    clean_text = "".join(clean_text.split())
    # Przecinki na kropki i zostawienie tylko cyfr/znaku minus
    clean_text = clean_text.replace(',', '.')
    clean_text = re.sub(r'[^\d\.\-]', '', clean_text)
    try:
        return float(clean_text)
    except:
        return 0.0

# --- LOGIKA BOTA ---
def wykonaj_analize_krs(krs, log_callback):
    results = []
    nazwa_firmy = "Nieznany podmiot"
    
    # Automatyczne szukanie przeglądarki na serwerze
    executable_path = shutil.which("chromium") or shutil.which("chromium-browser") or \
                      shutil.which("google-chrome") or shutil.which("brave-browser")

    # Folder na pobrane pliki w /tmp (kluczowe na chmurze)
    katalog_pobrane = "/tmp/downloaded_files"
    if not os.path.exists(katalog_pobrane):
        os.makedirs(katalog_pobrane)
    
    # Start bota z flagami naprawczymi dla Linux/Docker
    with SB(uc=True, 
            browser="chrome", 
            binary_location=executable_path, 
            headless=True, 
            xvfb=True,
            chromium_arg="--no-sandbox,--disable-dev-shm-usage") as sb:
        try:
            log_callback("🚀 Łączenie z Ministerstwem...")
            sb.uc_open_with_reconnect("https://rdf-przegladarka.ms.gov.pl/", 4)
            
            try:
                sb.click('button:contains("Akceptuj"), button:contains("Zgadzam się")', timeout=3)
            except: pass 

            log_callback(f"🔎 Szukanie KRS: {krs}...")
            sb.type("input[formcontrolname='numerKRS']", krs, timeout=30)
            sb.click("span.p-button-label:contains('Wyszukaj')", timeout=10)
            sb.wait_for_element("table", timeout=30)

            try:
                nazwa_firmy = sb.get_text("div:contains('Nazwa / firma podmiotu') + div")
                log_callback(f"🏢 Firma: {nazwa_firmy}")
            except:
                nazwa_firmy = f"Podmiot o KRS {krs}"

            log_callback("🎛️ Filtrowanie rocznych sprawozdań...")
            sb.click("span.p-button-label:contains('Pokaż filtry')", timeout=10)
            time.sleep(1)
            sb.click("span#rodzajDokumentuNazwaInput", timeout=10)
            time.sleep(1)
            sb.click("li:contains('Roczne sprawozdanie finansowe')", timeout=10)
            sb.click("button:contains('Filtruj')", timeout=10)
            time.sleep(3) 

            # Liczenie wierszy
            wiersze_akcji = sb.find_elements("td.actions-col")
            ile_pobrac = min(5, len(wiersze_akcji))
            
            if ile_pobrac == 0:
                log_callback("❌ Nie znaleziono sprawozdań.")
                return None, nazwa_firmy

            pobrane_archiwa = []
            for index in range(1, ile_pobrac + 1):
                log_callback(f"📥 Pobieranie dokumentu {index} z {ile_pobrac}...")
                
                # Sprzątanie /tmp przed każdym pobraniem
                for p in glob.glob(os.path.join(katalog_pobrane, '*.zip')):
                    if p not in pobrane_archiwa:
                        try: os.remove(p)
                        except: pass

                # Klikamy guzik szczegółów (wiersze są indexowane 1, 2, 3...)
                sb.click(f"tbody tr:nth-child({index}) button", timeout=15)
                time.sleep(2) 
                sb.click("span.p-button-label:contains('Pobierz dokumenty')", timeout=10)

                # Czekanie na ZIP
                plik_gotowy = False
                for _ in range(40):
                    time.sleep(1)
                    zips = [p for p in glob.glob(os.path.join(katalog_pobrane, '*.zip')) if p not in pobrane_archiwa]
                    if zips:
                        najnowszy = max(zips, key=os.path.getmtime)
                        nowa_nazwa = os.path.join(katalog_pobrane, f"data_{index}.zip")
                        os.rename(najnowszy, nowa_nazwa)
                        pobrane_archiwa.append(nowa_nazwa)
                        plik_gotowy = True
                        break
                
                if plik_gotowy:
                    sb.click(f"tbody tr:nth-child({index}) button", timeout=5)
                    time.sleep(1)

            log_callback("🧠 Analiza XML (szukanie P_ID_11)...")
            for zip_path in pobrane_archiwa:
                try:
                    with zipfile.ZipFile(zip_path, 'r') as z:
                        for nazwa_pliku in z.namelist():
                            if nazwa_pliku.endswith('.xml'):
                                with z.open(nazwa_pliku) as plik_xml:
                                    tekst = plik_xml.read().decode('utf-8', errors='ignore')
                                    
                                    # Szukanie roku
                                    rok_m = re.search(r'<[^>]*?DataDo[^>]*?>(\d{4})', tekst)
                                    rok_txt = rok_m.group(1) if rok_m else "????"
                                    
                                    podatek = 0.0
                                    # Szukanie bloku P_ID_11 (odporne na ns1, ns2, dtsf itd.)
                                    blok_m = re.search(r'<[^>]*?P_ID_11[^>]*?>(.*?)</[^>]*?P_ID_11>', tekst, re.DOTALL)
                                    if blok_m:
                                        # Szukanie RB (Rok Bieżący) wewnątrz bloku
                                        rb_m = re.search(r'<[^>]*?RB[^>]*?>(.*?)</[^>]*?RB>', blok_m.group(1), re.DOTALL)
                                        if rb_m:
                                            podatek = wyciagnij_liczbe(rb_m.group(1))
                                    
                                    results.append({"Rok": rok_txt, "Podatek (RB)": podatek})
                                break 
                finally:
                    if os.path.exists(zip_path): os.remove(zip_path)
            
            return sorted(results, key=lambda x: x['Rok'], reverse=True), nazwa_firmy

        except Exception as e:
            log_callback(f"💥 Błąd: {str(e)}")
            return None, None
        finally:
            try: sb.quit()
            except: pass
            os.system("pkill -9 chromium > /dev/null 2>&1")
            os.system("pkill -9 chrome > /dev/null 2>&1")

# --- INTERFEJS ---
st.set_page_config(page_title="KRS Tax Scanner", page_icon="📊", layout="wide")
st.title("📊 Analityk Podatkowy KRS")

if 'krs_input' not in st.session_state:
    st.session_state.krs_input = ""

with st.sidebar:
    st.header("⚙️ Ustawienia")
    krs_val = st.text_input("Numer KRS:", value=st.session_state.krs_input)
    c1, c2 = st.columns(2)
    with c1:
        start_btn = st.button("Szukaj", use_container_width=True)
    with c2:
        if st.button("Reset", use_container_width=True):
            st.session_state.krs_input = ""
            st.rerun()

if start_btn:
    if len(krs_val) == 10 and krs_val.isdigit():
        with st.status("🕵️ Bot pracuje...", expanded=True) as status:
            lp = st.empty()
            ls = []
            def up(m):
                ls.append(m)
                lp.code("\n".join(ls[-5:]))
            dane, nazwa = wykonaj_analize_krs(krs_val, up)
            status.update(label="Gotowe!", state="complete", expanded=False)
        
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
                st.metric("Suma wpłat (5 lat)", f"{suma:,.2f} PLN".replace(",", " "))
                st.info("Dane pobrane z pozycji P_ID_11 sprawozdań XML.")
        else:
            st.error("Błąd podczas analizy sprawozdań.")
    else:
        st.error("Numer KRS musi mieć 10 cyfr.")
