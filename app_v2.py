import streamlit as st
from seleniumbase import SB
import time
import os
import glob
import zipfile
import re
import pandas as pd
import shutil
import uuid

# --- KONFIGURACJA ŚRODOWISKA ---
os.environ["SELENIUMBASE_DRIVER_PATH"] = "/tmp/drivers"
os.environ["UC_DRIVER_PATH"] = "/tmp/uc_drivers"

def formatuj_walute(kwota):
    return f"{kwota:,.2f}".replace(",", " ").replace(".", ",") + " PLN"

def wyciagnij_liczbe(raw_html):
    if not raw_html: return 0.0
    clean = re.sub(r'<[^>]+>', '', raw_html)
    clean = "".join(clean.split()).replace(',', '.')
    match = re.search(r'[\d\.\-]+', clean)
    return float(match.group()) if match else 0.0

# --- GŁÓWNA LOGIKA BOTA ---

def wykonaj_analize_krs(krs, log_callback, limit_lat):
    results = []
    nazwa_firmy = None
    error_msg = None # Tu zapiszemy konkretny błąd techniczny
    
    executable_path = shutil.which("chromium") or shutil.which("google-chrome")
    session_id = str(uuid.uuid4())[:8]
    katalog_sesji = f"/tmp/downloads_{session_id}"
    
    if os.path.exists(katalog_sesji): shutil.rmtree(katalog_sesji)
    os.makedirs(katalog_sesji)

    with SB(uc=False, browser="chrome", binary_location=executable_path, headless=True, xvfb=True,
            chromium_arg="--no-sandbox,--disable-dev-shm-usage") as sb:
        try:
            sb.execute_cdp_cmd("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": katalog_sesji})

            log_callback(f"🌐 Łączenie z bazą Ministerstwa...")
            sb.open("https://rdf-przegladarka.ms.gov.pl/")
            
            sb.type("input[formcontrolname='numerKRS']", krs)
            sb.click("span.p-button-label:contains('Wyszukaj')")
            sb.wait_for_element("table", timeout=20)
            
            nazwa_firmy = sb.get_text("div:contains('Nazwa / firma podmiotu') + div")
            log_callback(f"🏢 Firma: {nazwa_firmy}")

            # FILTROWANIE - Wzmocniona logika
            try:
                sb.click("span.p-button-label:contains('Pokaż filtry')")
                time.sleep(1)
                sb.click("span#rodzajDokumentuNazwaInput")
                time.sleep(1)
                # Szukamy elementu listy w sposób bardziej odporny
                sb.click('li:contains("Roczne sprawozdanie finansowe")', timeout=10)
                sb.click("button:contains('Filtruj')")
                time.sleep(5)
            except Exception as e:
                error_msg = f"Nie udało się ustawić filtrów na stronie Ministerstwa (Błąd: {str(e)[:50]})"
                raise Exception(error_msg)

            wiersze = sb.find_elements("tbody tr")
            if not wiersze or "nie znaleziono" in wiersze[0].text.lower():
                return [], nazwa_firmy, None

            ile_pobrac = min(limit_lat, len(wiersze))
            log_callback(f"🔎 Analizuję {ile_pobrac} ostatnie dokumenty...")

            pobrane_zips = []
            for i in range(1, ile_pobrac + 1):
                try:
                    btn_row = f"tbody tr:nth-child({i}) button"
                    sb.click(btn_row)
                    time.sleep(2)
                    sb.click("button:contains('Pobierz dokumenty')")
                    
                    plik_ok = False
                    for _ in range(40):
                        time.sleep(1)
                        found = glob.glob(os.path.join(katalog_sesji, '*.zip'))
                        new_files = [f for f in found if f not in pobrane_zips and not f.endswith('.crdownload')]
                        if new_files:
                            path = os.path.join(katalog_sesji, f"file_{i}.zip")
                            os.rename(new_files[0], path)
                            pobrane_zips.append(path)
                            log_callback(f"✅ Dokument {i} pobrany.")
                            plik_ok = True
                            break
                    sb.click(btn_row) # Zwiń
                except: continue

            log_callback("🧠 Analiza XML...")
            for zp in pobrane_zips:
                try:
                    with zipfile.ZipFile(zp, 'r') as z:
                        for fname in z.namelist():
                            if fname.endswith('.xml'):
                                with z.open(fname) as fxml:
                                    raw = fxml.read().decode('utf-8', errors='ignore')
                                    skala = 1
                                    zaokr_m = re.search(r'<[^>]*?WielkoscZaokraglen[^>]*?>(.*?)</[^>]*?WielkoscZaokraglen>', raw)
                                    if zaokr_m and zaokr_m.group(1).strip() == "3": skala = 1000
                                    elif zaokr_m and zaokr_m.group(1).strip() == "6": skala = 1000000

                                    rok_m = re.search(r'<[^>]*?DataDo[^>]*?>(\d{4})', raw)
                                    rok = rok_m.group(1) if rok_m else "????"
                                    
                                    val = 0.0
                                    for tag in ['P_ID_11', 'P_ID_10', 'P_ID_9']:
                                        pattern = rf'<[^>]*?{tag}[^>]*?>(.*?)</[^>]*?{tag}>'
                                        match = re.search(pattern, raw, re.DOTALL)
                                        if match:
                                            rb_m = re.search(r'<[^>]*?RB[^>]*?>(.*?)</[^>]*?RB>', match.group(1), re.DOTALL)
                                            if rb_m:
                                                val = wyciagnij_liczbe(rb_m.group(1)) * skala
                                                break
                                    results.append({"Rok": rok, "Podatek": val})
                                break 
                except: pass

            return results, nazwa_firmy, None

        except Exception as e:
            # Zwracamy błąd do UI
            return None, nazwa_firmy, error_msg or str(e)
        finally:
            if os.path.exists(katalog_sesji): shutil.rmtree(katalog_sesji)

# --- UI ---
st.set_page_config(page_title="Scanner Podatkowy KRS", page_icon="📊", layout="wide")
st.title("📊 Analityk Podatkowy KRS")

with st.sidebar:
    st.header("⚙️ Ustawienia")
    krs_val = st.text_input("Numer KRS", max_chars=10)
    lat_val = st.slider("Liczba lat:", 1, 5, 1)
    start_btn = st.button("Szukaj 🔍", use_container_width=True)

if start_btn and krs_val:
    with st.status("🕵️ Praca bota...", expanded=True) as status:
        log_area = st.empty()
        log_list = []
        def my_log(m):
            log_list.append(m); log_area.code("\n".join(log_list[-5:]))
        
        wyniki, firma, blad_techniczny = wykonaj_analize_krs(krs_val, my_log, lat_val)
        status.update(label="Gotowe!", state="complete")

    if blad_techniczny:
        st.error(f"❌ Wystąpił błąd techniczny: {blad_techniczny}")
        st.info("💡 Spróbuj uruchomić analizę ponownie. Czasami serwery Ministerstwa wymagają odświeżenia.")
    elif firma:
        st.header(f"🏢 {firma}")
        if wyniki:
            df = pd.DataFrame(wyniki).sort_values("Rok", ascending=False)
            df_disp = df.copy()
            df_disp["Podatek"] = df_disp["Podatek"].apply(formatuj_walute)
            st.table(df_disp)
            st.metric("Suma podatku", formatuj_walute(df["Podatek"].sum()))
        else:
            st.warning("⚠️ Brak dokumentów typu 'Roczne sprawozdanie finansowe' lub brak tagów finansowych w plikach.")
