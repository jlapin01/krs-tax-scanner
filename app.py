import streamlit as st
from seleniumbase import SB
import time, os, glob, zipfile, re, pandas as pd, shutil

# --- KONFIGURACJA ŚRODOWISKA ---
os.environ["SELENIUMBASE_DRIVER_PATH"] = "/tmp/drivers"
os.environ["UC_DRIVER_PATH"] = "/tmp/uc_drivers"

def wyciagnij_liczbe(raw_html):
    if not raw_html: return 0.0
    clean = re.sub(r'<[^>]+>', '', raw_html)
    clean = "".join(clean.split()).replace(',', '.')
    clean = re.search(r'[\d\.\-]+', clean)
    return float(clean.group()) if clean else 0.0

def wykonaj_analize_krs(krs, log_callback, limit_lat):
    results = []
    nazwa_firmy = None
    executable_path = shutil.which("chromium") or shutil.which("google-chrome")
    
    katalog = "/tmp/downloads"
    if os.path.exists(katalog): shutil.rmtree(katalog)
    os.makedirs(katalog)

    # KLUCZOWE: Dodajemy set_downloads_path w ustawieniach SB
    with SB(uc=False, browser="chrome", binary_location=executable_path, headless=True, xvfb=True,
            chromium_arg="--no-sandbox,--disable-dev-shm-usage") as sb:
        try:
            # Wymuszamy na Chrome ścieżkę pobierania
            sb.execute_cdp_cmd("Page.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": katalog
            })

            log_callback("🌐 Łączenie z RDF...")
            sb.open("https://rdf-przegladarka.ms.gov.pl/")
            
            sb.type("input[formcontrolname='numerKRS']", krs)
            sb.click("span.p-button-label:contains('Wyszukaj')")
            sb.wait_for_element("table", timeout=20)
            
            nazwa_firmy = sb.get_text("div:contains('Nazwa / firma podmiotu') + div")
            log_callback(f"🏢 Firma: {nazwa_firmy}")

            # Filtrowanie
            sb.click("span.p-button-label:contains('Pokaż filtry')")
            time.sleep(1)
            sb.click("span#rodzajDokumentuNazwaInput")
            sb.click("li:contains('Roczne sprawozdanie finansowe')")
            sb.click("button:contains('Filtruj')")
            time.sleep(5)

            wiersze = sb.find_elements("tbody tr")
            ile_faktycznie = min(limit_lat, len(wiersze))
            log_callback(f"🔎 Wykryto {len(wiersze)} pozycji. Pobieram {ile_faktycznie}...")

            pobrane_zips = []
            for i in range(1, ile_faktycznie + 1):
                try:
                    btn_row = f"tbody tr:nth-child({i}) button"
                    log_callback(f"📂 Otwieram wiersz {i}...")
                    sb.click(btn_row)
                    time.sleep(2)
                    
                    # Szukamy przycisku pobierania i klikamy go specyficznie
                    btn_download = "button:contains('Pobierz dokumenty')"
                    sb.wait_for_element(btn_download, timeout=10)
                    sb.click(btn_download)
                    log_callback(f"⏳ Czekam na plik {i}...")
                    
                    # Radar pobierania
                    plik_ok = False
                    for sekunda in range(45):
                        time.sleep(1)
                        found = glob.glob(os.path.join(katalog, '*.zip'))
                        # Szukamy plików, które nie są jeszcze "nasze" (obsługa plików tymczasowych .crdownload)
                        new_files = [f for f in found if f not in pobrane_zips and not f.endswith('.crdownload')]
                        if new_files:
                            path = os.path.join(katalog, f"data_{i}.zip")
                            os.rename(new_files[0], path)
                            pobrane_zips.append(path)
                            log_callback(f"✅ Dokument {i} pobrany!")
                            plik_ok = True
                            break
                    
                    if not plik_ok:
                        log_callback(f"❌ Timeout pobierania dla wiersza {i}")
                    
                    sb.click(btn_row) # Zwiń wiersz
                    time.sleep(1)
                except Exception as e:
                    log_callback(f"⚠️ Problem z wierszem {i}: {str(e)[:40]}")

            log_callback(f"🧠 Analiza {len(pobrane_zips)} plików...")
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
                                                log_callback(f"💰 {rok}: {val} PLN")
                                                break
                                    results.append({"Rok": rok, "Podatek": val})
                except Exception as e_zip:
                    log_callback(f"⚠️ Błąd ZIP: {e_zip}")

            return results, nazwa_firmy
        except Exception as e:
            log_callback(f"💥 Błąd krytyczny: {e}")
            return [], nazwa_firmy

# --- UI ---
st.set_page_config(page_title="Scanner KRS", layout="wide")
st.title("📊 Analityk Podatkowy KRS")

with st.sidebar:
    st.header("⚙️ Ustawienia")
    krs_val = st.text_input("Numer KRS", max_chars=10)
    lat_val = st.slider("Liczba lat:", 1, 5, 1)
    start_btn = st.button("Szukaj 🔍", use_container_width=True)

if start_btn and krs_val:
    with st.status("🕵️ Bot pracuje...", expanded=True) as status:
        l_area = st.empty()
        l_list = []
        def my_log(m):
            l_list.append(m); l_area.code("\n".join(l_list[-5:]))
        
        dane, nazwa = wykonaj_analize_krs(krs_val, my_log, lat_val)
        status.update(label="Gotowe!", state="complete")

    if nazwa:
        st.header(nazwa)
        if dane:
            df = pd.DataFrame(dane).sort_values("Rok", ascending=False)
            df_view = df.copy()
            df_view["Podatek"] = df_view["Podatek"].apply(lambda x: f"{x:,.2f} PLN")
            st.table(df_view)
            st.metric("Suma", f"{df['Podatek'].sum():,.2f} PLN")
        else:
            st.warning("Pobrano pliki, ale nie znaleziono w nich tagów podatkowych.")
