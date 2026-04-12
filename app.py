import streamlit as st
from seleniumbase import SB
import time
import os
import glob
import zipfile
import re
import pandas as pd

# --- ANALITYCZNA FUNKCJA CZYSZCZĄCA LICZBY ---
def wyciagnij_liczbe(raw_html):
    if not raw_html:
        return 0.0
    # Usuwamy wszelkie tagi wewnętrzne, jeśli istnieją
    clean_text = re.sub(r'<[^>]+>', '', raw_html)
    # Agresywne usuwanie spacji (również tych twardych \xa0)
    clean_text = "".join(clean_text.split())
    # Polskie przecinki na kropki
    clean_text = clean_text.replace(',', '.')
    # Zostawiamy tylko to, co tworzy liczbę
    clean_text = re.sub(r'[^\d\.\-]', '', clean_text)
    
    try:
        return float(clean_text)
    except:
        return 0.0

# --- GŁÓWNA LOGIKA BOTA ---
def wykonaj_analize_krs(krs, log_callback):
    results = []
    nazwa_firmy = "Nieznany podmiot"
    sciezka_do_brave = "/usr/bin/brave-browser" 
    katalog_pobrane = os.path.join(os.getcwd(), "downloaded_files")
    os.makedirs(katalog_pobrane, exist_ok=True)
    
    # Tryb headless=True + xvfb=True dla czystej pracy w tle
    with SB(uc=True, browser="chrome", binary_location=sciezka_do_brave, 
            headless=True, xvfb=True) as sb:
        try:
            log_callback("🚀 Inicjalizacja sesji z Ministerstwem...")
            sb.uc_open_with_reconnect("https://rdf-przegladarka.ms.gov.pl/", 4)
            
            try:
                sb.click('button:contains("Akceptuj"), button:contains("Zgadzam się")', timeout=3)
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

            log_callback("🎛️ Ustawianie filtrów (Roczne sprawozdania)...")
            sb.click("span.p-button-label:contains('Pokaż filtry')", timeout=10)
            time.sleep(1)
            sb.click("span#rodzajDokumentuNazwaInput", timeout=10)
            time.sleep(1)
            sb.click("li:contains('Roczne sprawozdanie finansowe')", timeout=10)
            sb.click("button:contains('Filtruj')", timeout=10)
            time.sleep(3) 

            wiersze = sb.find_elements("td.actions-col")
            ile_pobrac = min(5, len(wiersze))
            
            if ile_pobrac == 0:
                log_callback("❌ Brak sprawozdań do analizy.")
                return None, nazwa_firmy

            pobrane_archiwa = []
            for index in range(1, ile_pobrac + 1):
                log_callback(f"📥 Pobieranie archiwum {index} z {ile_pobrac}...")
                
                # Sprzątanie folderu
                for p in glob.glob(os.path.join(katalog_pobrane, '*.zip')):
                    if p not in pobrane_archiwa:
                        try: os.remove(p)
                        except: pass

                sb.click(f"tbody tr:nth-child({index}) button", timeout=15)
                time.sleep(2) 
                sb.click("span.p-button-label:contains('Pobierz dokumenty')", timeout=10)

                # Radar pobierania
                for _ in range(40):
                    time.sleep(1)
                    zips = [p for p in glob.glob(os.path.join(katalog_pobrane, '*.zip')) if p not in pobrane_archiwa]
                    if zips:
                        najnowszy = max(zips, key=os.path.getmtime)
                        nowa_nazwa = os.path.join(katalog_pobrane, f"data_{index}.zip")
                        os.rename(najnowszy, nowa_nazwa)
                        pobrane_archiwa.append(nowa_nazwa)
                        break
                
                sb.click(f"tbody tr:nth-child({index}) button", timeout=5)
                time.sleep(1)

            log_callback("🧠 Ekstrakcja danych (ignorowanie przestrzeni nazw)...")
            for zip_path in pobrane_archiwa:
                try:
                    with zipfile.ZipFile(zip_path, 'r') as z:
                        for nazwa_pliku in z.namelist():
                            if nazwa_pliku.endswith('.xml'):
                                with z.open(nazwa_pliku) as plik_xml:
                                    tekst = plik_xml.read().decode('utf-8', errors='ignore')
                                    
                                    # Szukanie roku (DataDo)
                                    rok_match = re.search(r'<[^>]*?DataDo[^>]*?>(\d{4})', tekst)
                                    rok_txt = rok_match.group(1) if rok_match else "????"
                                    
                                    # Szukanie P_ID_11 - RegEx odporny na ns1, ns2, dtsf itd.
                                    # Dopasuje: <ns1:P_ID_11>, <dtsf:P_ID_11>, <P_ID_11>
                                    blok_pattern = r'<[^>]*?P_ID_11[^>]*?>(.*?)</[^>]*?P_ID_11>'
                                    blok_match = re.search(blok_pattern, tekst, re.DOTALL)
                                    
                                    podatek = 0.0
                                    if blok_match:
                                        zawartosc = blok_match.group(1)
                                        # To samo dla tagu RB (Rok Bieżący)
                                        rb_match = re.search(r'<[^>]*?RB[^>]*?>(.*?)</[^>]*?RB>', zawartosc, re.DOTALL)
                                        if rb_match:
                                            podatek = wyciagnij_liczbe(rb_match.group(1))
                                    
                                    results.append({"Rok": rok_txt, "Podatek (RB)": podatek})
                                break 
                finally:
                    if os.path.exists(zip_path): os.remove(zip_path)
            
            log_callback("🧹 Finalizacja i czyszczenie procesów...")
            return sorted(results, key=lambda x: x['Rok'], reverse=True), nazwa_firmy

        except Exception as e:
            log_callback(f"💥 Błąd: {str(e)}")
            return None, None
        finally:
            try: sb.quit()
            except: pass
            os.system("pkill -9 brave-browser > /dev/null 2>&1")

# --- INTERFEJS STREAMLIT ---
st.set_page_config(page_title="Scanner Podatkowy KRS", page_icon="🏦", layout="wide")

st.title("🏦 Scanner Podatkowy KRS")
st.markdown("Narzędzie do automatycznej ekstrakcji pozycji **P_ID_11** (Podatek dochodowy) z plików XML.")

if 'krs_input' not in st.session_state:
    st.session_state.krs_input = ""

with st.sidebar:
    st.header("⚙️ Parametry")
    krs_val = st.text_input("Numer KRS:", value=st.session_state.krs_input)
    c1, c2 = st.columns(2)
    with c1:
        start_btn = st.button("Szukaj 🔍", use_container_width=True)
    with c2:
        if st.button("Reset 🧹", use_container_width=True):
            st.session_state.krs_input = ""
            st.rerun()

if start_btn:
    if len(krs_val) == 10 and krs_val.isdigit():
        with st.status("🕵️ Trwa analiza dokumentów...", expanded=True) as status:
            log_p = st.empty()
            logs = []
            def update_l(m):
                logs.append(m)
                log_p.code("\n".join(logs[-5:]))

            dane, nazwa = wykonaj_analize_krs(krs_val, update_l)
            status.update(label="Analiza zakończona!", state="complete", expanded=False)
        
        if dane and nazwa:
            st.divider()
            st.header(f"🏢 {nazwa}")
            
            col_l, col_r = st.columns([2, 1])
            with col_l:
                df = pd.DataFrame(dane)
                df_v = df.copy()
                df_v["Podatek (RB)"] = df_v["Podatek (RB)"].apply(lambda x: f"{x:,.2f} PLN".replace(",", " "))
                st.subheader("Zestawienie roczne")
                st.table(df_v)
            with col_r:
                suma = df["Podatek (RB)"].sum()
                st.subheader("Podsumowanie")
                st.metric("Suma podatku (RB)", f"{suma:,.2f} PLN".replace(",", " "))
                st.info("Pamiętaj: suma dotyczy wyłącznie wartości wykazanych w tagu P_ID_11 plików XML.")
        else:
            st.error("Nie znaleziono odpowiednich danych dla podanego numeru KRS.")
    else:
        st.error("Numer KRS musi mieć dokładnie 10 cyfr.")