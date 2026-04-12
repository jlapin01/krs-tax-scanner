import streamlit as st
from seleniumbase import SB
import time, os, glob, zipfile, re, pandas as pd, shutil

# Ustawienia środowiska
os.environ["SELENIUMBASE_DRIVER_PATH"] = "/tmp/drivers"
os.environ["UC_DRIVER_PATH"] = "/tmp/uc_drivers"

def wyciagnij_liczbe(raw_html):
    if not raw_html: return 0.0
    clean = re.sub(r'<[^>]+>', '', raw_html)
    clean = "".join(clean.split()).replace(',', '.')
    clean = re.search(r'[\d\.\-]+', clean)
    return float(clean.group()) if clean else 0.0

def wykonaj_analize_krs(krs, log_callback):
    results = []
    nazwa_firmy = None
    executable_path = shutil.which("chromium") or shutil.which("google-chrome")
    
    katalog = "/tmp/downloads"
    if os.path.exists(katalog): shutil.rmtree(katalog)
    os.makedirs(katalog)

    with SB(uc=False, browser="chrome", binary_location=executable_path, headless=True, xvfb=True,
            chromium_arg="--no-sandbox,--disable-dev-shm-usage") as sb:
        try:
            log_callback("🌐 Łączenie z Ministerstwem...")
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
            log_callback(f"🔎 Widzę {len(wiersze)} wierszy w tabeli.")

            pobrane_zips = []
            # Pobieramy maksymalnie 3 roczniki dla testu, żeby nie zapchać RAMu
            for i in range(1, min(4, len(wiersze) + 1)):
                try:
                    btn = f"tbody tr:nth-child({i}) button"
                    log_callback(f"📂 Klikam wiersz nr {i}...")
                    sb.click(btn)
                    time.sleep(2)
                    sb.click("span.p-button-label:contains('Pobierz dokumenty')")
                    
                    # Czekanie na plik
                    for _ in range(30):
                        time.sleep(1)
                        found = glob.glob(os.path.join(katalog, '*.zip'))
                        new_files = [f for f in found if f not in pobrane_zips]
                        if new_files:
                            path = os.path.join(katalog, f"test_{i}.zip")
                            os.rename(new_files[0], path)
                            pobrane_zips.append(path)
                            log_callback(f"📥 Pobrano plik {i}")
                            break
                    sb.click(btn) # Zwiń
                except Exception as e:
                    log_callback(f"⚠️ Błąd wiersza {i}: {str(e)[:50]}")

            log_callback(f"🧠 Start analizy {len(pobrane_zips)} plików...")
            for zp in pobrane_zips:
                with zipfile.ZipFile(zp, 'r') as z:
                    log_callback(f"📦 ZIP zawiera: {', '.join(z.namelist())}")
                    for fname in z.namelist():
                        if fname.endswith('.xml'):
                            with z.open(fname) as fxml:
                                raw = fxml.read().decode('utf-8', errors='ignore')
                                log_callback(f"📄 Podgląd XML ({fname}): {raw[:150]}...")
                                
                                rok_m = re.search(r'<[^>]*?DataDo[^>]*?>(\d{4})', raw)
                                rok = rok_m.group(1) if rok_m else "????"
                                
                                val = 0.0
                                # Szukamy tagu P_ID_11 lub P_ID_10
                                for tag in ['P_ID_11', 'P_ID_10']:
                                    pattern = rf'<[^>]*?{tag}[^>]*?>(.*?)</[^>]*?{tag}>'
                                    match = re.search(pattern, raw, re.DOTALL)
                                    if match:
                                        rb = re.search(r'<[^>]*?RB[^>]*?>(.*?)</[^>]*?RB>', match.group(1), re.DOTALL)
                                        if rb:
                                            val = wyciagnij_liczbe(rb.group(1))
                                            log_callback(f"💰 {rok}: Znaleziono {val} w {tag}")
                                            break
                                results.append({"Rok": rok, "Podatek": val})
            return results, nazwa_firmy
        except Exception as e:
            log_callback(f"💥 BŁĄD KRYTYCZNY: {e}")
            return [], nazwa_firmy

# --- INTERFEJS STREAMLIT ---
st.set_page_config(page_title="DEBUG KRS", layout="wide")
st.title("🕵️ DEBUGGER Analityka KRS")

krs = st.sidebar.text_input("KRS", max_chars=10)
if st.sidebar.button("ANALIZUJ"):
    with st.status("Praca bota...", expanded=True) as status:
        log_a = st.empty()
        log_l = []
        def my_log(m):
            log_l.append(m); log_a.code("\n".join(log_l[-10:])) # Widzimy 10 linii logów
        
        dane, nazwa = wykonaj_analize_krs(krs, my_log)
        status.update(label="Koniec", state="complete")

    if nazwa:
        st.subheader(nazwa)
        st.write(pd.DataFrame(dane))
