import streamlit as st
from seleniumbase import SB
import time
import os
import glob
import zipfile
import re
import pandas as pd
import shutil
import uuid  # Dodajemy do generowania unikalnych ID

# --- KONFIGURACJA ŚRODOWISKA ---
os.environ["SELENIUMBASE_DRIVER_PATH"] = "/tmp/drivers"
os.environ["UC_DRIVER_PATH"] = "/tmp/uc_drivers"

def formatuj_walute(kwota):
    return f"{kwota:,.2f}".replace(",", " ").replace(".", ",") + " PLN"

def wyciagnij_liczbe(raw_html):
    if not raw_html: return 0.0
    clean = re.sub(r'<[^>]+>', '', raw_html)
    clean = "".join(clean.split()).replace(',', '.')
    clean = re.search(r'[\d\.\-]+', clean)
    return float(clean.group()) if clean else 0.0

# --- GŁÓWNA LOGIKA BOTA ---
def wykonaj_analize_krs(krs, log_callback, limit_lat):
    results = []
    nazwa_firmy = None
    executable_path = shutil.which("chromium") or shutil.which("google-chrome")
    
    # KLUCZOWA ZMIANA: Unikalny folder dla KAŻDEGO uruchomienia
    session_id = str(uuid.uuid4())[:8] # Krótkie ID sesji
    katalog_sesji = f"/tmp/downloads_{session_id}"
    
    if os.path.exists(katalog_sesji): shutil.rmtree(katalog_sesji)
    os.makedirs(katalog_sesji)

    with SB(uc=False, browser="chrome", binary_location=executable_path, headless=True, xvfb=True,
            chromium_arg="--no-sandbox,--disable-dev-shm-usage") as sb:
        try:
            # Wskazujemy Chrome unikalny folder pobierania
            sb.execute_cdp_cmd("Page.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": katalog_sesji
            })

            log_callback(f"🌐 Sesja {session_id}: Łączenie z RDF...")
            sb.open("https://rdf-przegladarka.ms.gov.pl/")
            
            sb.type("input[formcontrolname='numerKRS']", krs)
            sb.click("span.p-button-label:contains('Wyszukaj')")
            sb.wait_for_element("table", timeout=20)
            
            nazwa_firmy = sb.get_text("div:contains('Nazwa / firma podmiotu') + div")
            log_callback(f"🏢 Firma: {nazwa_firmy}")

            sb.click("span.p-button-label:contains('Pokaż filtry')")
            time.sleep(1)
            sb.click("span#rodzajDokumentuNazwaInput")
            sb.click("li:contains('Roczne sprawozdanie finansowe')")
            sb.click("button:contains('Filtruj')")
            time.sleep(5)

            wiersze = sb.find_elements("tbody tr")
            ile_faktycznie = min(limit_lat, len(wiersze))
            log_callback(f"🔎 Pobieram {ile_faktycznie} dokumenty...")

            pobrane_zips = []
            for i in range(1, ile_faktycznie + 1):
                try:
                    btn_row = f"tbody tr:nth-child({i}) button"
                    sb.click(btn_row)
                    time.sleep(2)
                    
                    btn_download = "button:contains('Pobierz dokumenty')"
                    sb.wait_for_element(btn_download, timeout=10)
                    sb.click(btn_download)
                    
                    plik_ok = False
                    for sekunda in range(45):
                        time.sleep(1)
                        # Szukamy tylko w folderze tej konkretnej sesji
                        found = glob.glob(os.path.join(katalog_sesji, '*.zip'))
                        new_files = [f for f in found if f not in pobrane_zips and not f.endswith('.crdownload')]
                        if new_files:
                            path = os.path.join(katalog_sesji, f"data_{i}.zip")
                            os.rename(new_files[0], path)
                            pobrane_zips.append(path)
                            log_callback(f"✅ Dokument {i} pobrany.")
                            plik_ok = True
                            break
                    
                    sb.click(btn_row)
                    time.sleep(1)
                except Exception as e:
                    log_callback(f"⚠️ Wiersz {i}: {str(e)[:30]}")

            log_callback(f"🧠 Analiza XML...")
            for zp in pobrane_zips:
                try:
                    with zipfile.ZipFile(zp, 'r') as z:
                        for fname in z.namelist():
                            if fname.endswith('.xml'):
                                with z.open(fname) as fxml:
                                    raw = fxml.read().decode('utf-8', errors='ignore')
                                    rok_m = re.search(r'<[^>]*?DataDo[^>]*?>(\d{4})', raw)
                                    rok = rok_m.group(1) if rok_m else "????"
                                    
                                    val = 0.0
                                    for tag in ['P_ID_11', 'P_ID_10', 'P_ID_9']:
                                        pattern = rf'<[^>]*?{tag}[^>]*?>(.*?)</[^>]*?{tag}>'
                                        match = re.search(pattern, raw, re.DOTALL)
                                        if match:
                                            rb = re.search(r'<[^>]*?RB[^>]*?>(.*?)</[^>]*?RB>', match.group(1), re.DOTALL)
                                            if rb:
                                                val = wyciagnij_liczbe(rb.group(1))
                                                break
                                    results.append({"Rok": rok, "Podatek": val})
                                break 
                except Exception:
                    pass

            return results, nazwa_firmy
        finally:
            # Bardzo ważne: Sprzątamy folder po zakończeniu pracy bota
            if os.path.exists(katalog_sesji):
                shutil.rmtree(katalog_sesji)

# --- UI ---
st.set_page_config(page_title="Scanner KRS", page_icon="📊", layout="wide")
st.title("📊 Analityk Podatkowy KRS")

with st.sidebar:
    st.header("⚙️ Ustawienia")
    krs_val = st.text_input("Numer KRS", max_chars=10)
    lat_val = st.slider("Liczba lat:", 1, 5, 1)
    start_btn = st.button("Analizuj 🔍", use_container_width=True)

if start_btn and krs_val:
    with st.status("🕵️ Praca bota...", expanded=True) as status:
        l_area = st.empty()
        l_list = []
        def my_log(m):
            l_list.append(m); l_area.code("\n".join(l_list[-5:]))
        
        dane, nazwa = wykonaj_analize_krs(krs_val, my_log, lat_val)
        status.update(label="Gotowe!", state="complete")

    if nazwa:
        st.header(f"🏢 {nazwa}")
        if dane:
            df = pd.DataFrame(dane).sort_values("Rok", ascending=False)
            df_view = df.copy()
            df_view["Podatek"] = df_view["Podatek"].apply(formatuj_walute)
            st.table(df_view)
            st.metric("Suma", formatuj_walute(df["Podatek"].sum()))
        else:
            st.warning("Nie znaleziono danych podatkowych.")
