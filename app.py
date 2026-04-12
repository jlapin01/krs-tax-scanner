import streamlit as st
from seleniumbase import SB
import time
import os
import glob
import zipfile
import re
import pandas as pd
import shutil

# --- ANALITYCZNA FUNKCJA CZYSZCZĄCA LICZBY ---
def wyciagnij_liczbe(raw_html):
    if not raw_html:
        return 0.0
    clean_text = re.sub(r'<[^>]+>', '', raw_html)
    clean_text = "".join(clean_text.split())
    clean_text = clean_text.replace(',', '.')
    clean_text = re.sub(r'[^\d\.\-]', '', clean_text)
    try:
        return float(clean_text)
    except:
        return 0.0

# --- GŁÓWNA LOGIKA BOTA ---
def wykonaj_analize_krs(krs, log_callback):
    results = []
    nazwa_firmy = "Nieznany podmiot"
    
    # DOPASOWANIE DO CHMURY: Szukamy dostępnej przeglądarki zamiast sztywnej ścieżki do Brave
    sciezka_binarna = shutil.which("chromium") or shutil.which("google-chrome") or shutil.which("brave-browser")
    
    # DOPASOWANIE DO CHMURY: Używamy /tmp/ bo tam wolno zapisywać pliki
    katalog_pobrane = "/tmp/downloaded_files"
    if os.path.exists(katalog_pobrane):
        shutil.rmtree(katalog_pobrane)
    os.makedirs(katalog_pobrane, exist_ok=True)
    
    # DOPASOWANIE DO CHMURY: uc=False (bezpieczniejsze uprawnienia) + manualne maskowanie
    with SB(uc=False, browser="chrome", binary_location=sciezka_binarna, 
            headless=True, xvfb=True, chromium_arg="--no-sandbox,--disable-dev-shm-usage") as sb:
        try:
            log_callback("🚀 Inicjalizacja sesji...")
            # Udajemy zwykłego Chrome, skoro wyłączyliśmy tryb UC
            sb.execute_cdp_cmd("Network.setUserAgentOverride", {
                "userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            })
            
            sb.open("https://rdf-przegladarka.ms.gov.pl/")
            
            try:
                sb.click('button:contains("Akceptuj"), button:contains("Zgadzam się")', timeout=4)
            except: pass 

            log_callback(f"🔎 Namierzanie KRS: {krs}...")
            sb.type("input[formcontrolname='numerKRS']", krs, timeout=30)
            sb.click("span.p-button-label:contains('Wyszukaj')", timeout=10)
            sb.wait_for_element("table", timeout=30)

            try:
                nazwa_firmy = sb.get_text("div:contains('Nazwa / firma podmiotu') + div")
                log_callback(f"🏢 Podmiot: {nazwa_firmy}")
            except:
                nazwa_firmy = "Podmiot KRS " + krs

            log_callback("🎛️ Ustawianie filtrów...")
            sb.click("span.p-button-label:contains('Pokaż filtry')", timeout=10)
            time.sleep(1)
            sb.click("span#rodzajDokumentuNazwaInput", timeout=10)
            time.sleep(1)
            sb.click("li:contains('Roczne sprawozdanie finansowe')", timeout=10)
            sb.click("button:contains('Filtruj')", timeout=10)
            time.sleep(5) # Dajemy serwerowi czas na odświeżenie tabeli

            wiersze = sb.find_elements("td.actions-col")
            ile_pobrac = min(5, len(wiersze))
            
            if ile_pobrac == 0:
                log_callback("❌ Brak sprawozdań do analizy.")
                return [], nazwa_firmy

            pobrane_archiwa = []
            for index in range(1, ile_pobrac + 1):
                log_callback(f"📥 Pobieranie {index} z {ile_pobrac}...")
                
                # Sprzątanie folderu /tmp
                for p in glob.glob(os.path.join(katalog_pobrane, '*.zip')):
                    if p not in pobrane_archiwa:
                        try: os.remove(p)
                        except: pass

                # TWOJA LOGIKA KLIKANIA (Wiersz po wierszu)
                btn = f"tbody tr:nth-child({index}) button"
                sb.click(btn, timeout=10)
                time.sleep(2) 
                sb.click("span.p-button-label:contains('Pobierz dokumenty')", timeout=10)

                # Radar pobierania
                plik_pobrany = False
                for _ in range(40):
                    time.sleep(1)
                    zips = [p for p in glob.glob(os.path.join(katalog_pobrane, '*.zip')) if p not in pobrane_archiwa]
                    if zips:
                        najnowszy = max(zips, key=os.path.getmtime)
                        nowa_nazwa = os.path.join(katalog_pobrane, f"data_{index}.zip")
                        os.rename(najnowszy, nowa_nazwa)
                        pobrane_archiwa.append(nowa_nazwa)
                        plik_pobrany = True
                        break
                
                if plik_pobrany:
                    sb.click(btn, timeout=5) # Zamknij szczegóły
                    time.sleep(1)

            log_callback("🧠 Ekstrakcja danych (P_ID_11)...")
            for zip_path in pobrane_archiwa:
                try:
                    with zipfile.ZipFile(zip_path, 'r') as z:
                        for nazwa_pliku in z.namelist():
                            if nazwa_pliku.endswith('.xml'):
                                with z.open(nazwa_pliku) as plik_xml:
                                    tekst = plik_xml.read().decode('utf-8', errors='ignore')
                                    
                                    rok_match = re.search(r'<[^>]*?DataDo[^>]*?>(\d{4})', tekst)
                                    rok_txt = rok_match.group(1) if rok_match else "????"
                                    
                                    # TWOJA DOKŁADNA LOGIKA REGEX (ns1, dtsf, itd.)
                                    blok_pattern = r'<[^>]*?P_ID_11[^>]*?>(.*?)</[^>]*?P_ID_11>'
                                    blok_match = re.search(blok_pattern, tekst, re.DOTALL)
                                    
                                    podatek = 0.0
                                    if blok_match:
                                        zawartosc = blok_match.group(1)
                                        rb_match = re.search(r'<[^>]*?RB[^>]*?>(.*?)</[^>]*?RB>', zawartosc, re.DOTALL)
                                        if rb_match:
                                            podatek = wyciagnij_liczbe(rb_match.group(1))
                                    
                                    results.append({"Rok": rok_txt, "Podatek (RB)": podatek})
                                break 
                finally:
                    if os.path.exists(zip_path): os.remove(zip_path)
            
            return results, nazwa_firmy

        except Exception as e:
            log_callback(f"💥 Błąd: {str(e)}")
            return [], nazwa_firmy

# --- INTERFEJS STREAMLIT ---
st.set_page_config(page_title="Scanner Podatkowy KRS", layout="wide")
st.title("🏦 Scanner Podatkowy KRS")

if 'krs_input' not in st.session_state:
    st.session_state.krs_input = ""

with st.sidebar:
    st.header("⚙️ Parametry")
    krs_val = st.text_input("Numer KRS:", value=st.session_state.krs_input)
    if st.button("Szukaj 🔍", use_container_width=True):
        if len(krs_val) == 10 and krs_val.isdigit():
            with st.status("🕵️ Praca bota...", expanded=True) as status:
                log_p = st.empty()
                logs = []
                def update_l(m):
                    logs.append(m)
                    log_p.code("\n".join(logs[-5:]))

                dane, nazwa = wykonaj_analize_krs(krs_val, update_l)
                status.update(label="Analiza zakończona!", state="complete", expanded=False)
            
            if nazwa:
                st.header(f"🏢 {nazwa}")
                if dane:
                    df = pd.DataFrame(dane).sort_values(by="Rok", ascending=False)
                    df_v = df.copy()
                    df_v["Podatek (RB)"] = df_v["Podatek (RB)"].apply(lambda x: f"{x:,.2f} PLN".replace(",", " "))
                    st.table(df_v)
                    st.metric("Suma podatku (RB)", f"{df['Podatek (RB)'].sum():,.2f} PLN".replace(",", " "))
                else:
                    st.warning("Pobrano dokumenty, ale nie znaleziono w nich tagu P_ID_11.")
        else:
            st.error("Numer KRS musi mieć 10 cyfr.")

    if st.button("Reset 🧹"):
        st.session_state.krs_input = ""
        st.rerun()
