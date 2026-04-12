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

    with SB(uc=False, browser="chrome", binary_location=executable_path, headless=True, xvfb=True,
            chromium_arg="--no-sandbox,--disable-dev-shm-usage") as sb:
        try:
            log_callback("🌐 Łączenie z RDF...")
            sb.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"})
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
            # Dynamiczne ograniczenie pętli do wyboru użytkownika
            ile_faktycznie = min(limit_lat, len(wiersze))
            log_callback(f"🔎 Znaleziono {len(wiersze)} pozycji. Pobieram {ile_faktycznie} ostatnie...")

            pobrane_zips = []
            for i in range(1, ile_faktycznie + 1):
                try:
                    btn = f"tbody tr:nth-child({i}) button"
                    log_callback(f"📂 Otwieram wiersz {i}...")
                    sb.click(btn)
                    time.sleep(2)
                    sb.click("span.p-button-label:contains('Pobierz dokumenty')")
                    
                    for _ in range(40):
                        time.sleep(1)
                        found = glob.glob(os.path.join(katalog, '*.zip'))
                        new_files = [f for f in found if f not in pobrane_zips]
                        if new_files:
                            path = os.path.join(katalog, f"data_{i}.zip")
                            os.rename(new_files[0], path)
                            pobrane_zips.append(path)
                            log_callback(f"✅ Pobrano dokument {i}")
                            break
                    sb.click(btn) # Zwiń
                except Exception as e:
                    log_callback(f"⚠️ Pinięcie wiersza {i}: {str(e)[:40]}")

            log_callback(f"🧠 Analiza {len(pobrane_zips)} plików...")
            for zp in pobrane_zips:
                with zipfile.ZipFile(zp, 'r') as z:
                    for fname in z.namelist():
                        if fname.endswith('.xml'):
                            with z.open(fname) as fxml:
                                raw = fxml.read().decode('utf-8', errors='ignore')
                                
                                rok_m = re.search(r'<[^>]*?DataDo[^>]*?>(\d{4})', raw)
                                rok = rok_m.group(1) if rok_m else "????"
                                
                                val = 0.0
                                # Szukamy tagu P_ID_11 lub P_ID_10
                                for tag in ['P_ID_11', 'P_ID_10', 'P_ID_9']:
                                    pattern = rf'<[^>]*?{tag}[^>]*?>(.*?)</[^>]*?{tag}>'
                                    match = re.search(pattern, raw, re.DOTALL)
                                    if match:
                                        rb = re.search(r'<[^>]*?RB[^>]*?>(.*?)</[^>]*?RB>', match.group(1), re.DOTALL)
                                        if rb:
                                            val = wyciagnij_liczbe(rb.group(1))
                                            log_callback(f"💰 {rok}: Odczytano {val} PLN")
                                            break
                                results.append({"Rok": rok, "Podatek": val})
            return results, nazwa_firmy
        except Exception as e:
            log_callback(f"💥 Błąd: {e}")
            return [], nazwa_firmy

# --- INTERFEJS STREAMLIT ---
st.set_page_config(page_title="Scanner KRS", layout="wide")

st.title("📊 Analityk Podatkowy KRS")

with st.sidebar:
    st.header("⚙️ Ustawienia")
    krs_val = st.text_input("Numer KRS", max_chars=10)
    
    # NOWA OPCJA: Suwak wyboru lat
    lat_val = st.slider("Liczba lat do wstecz:", 1, 5, 1)
    
    start_btn = st.button("Analizuj 🔍", use_container_width=True)

if start_btn and krs_val:
    with st.status("🕵️ Bot pracuje (proszę nie odświeżać)...", expanded=True) as status:
        l_area = st.empty()
        l_list = []
        def my_log(m):
            l_list.append(m); l_area.code("\n".join(l_list[-5:]))
        
        # Przekazujemy lat_val do funkcji
        dane, nazwa = wykonaj_analize_krs(krs_val, my_log, lat_val)
        status.update(label="Analiza zakończona!", state="complete")

    if nazwa:
        st.header(f"🏢 {nazwa}")
        if dane:
            df = pd.DataFrame(dane).sort_values("Rok", ascending=False)
            df_view = df.copy()
            df_view["Podatek"] = df_view["Podatek"].apply(lambda x: f"{x:,.2f} PLN")
            st.table(df_view)
            
            suma = df["Podatek"].sum()
            st.metric(f"Suma podatku ({len(dane)} lat)", f"{suma:,.2f} PLN")
        else:
            st.warning("Pobrano pliki, ale nie znaleziono w nich oczekiwanych danych o podatku (P_ID_11).")
