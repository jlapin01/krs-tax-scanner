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
    
    executable_path = shutil.which("chromium") or shutil.which("google-chrome")
    session_id = str(uuid.uuid4())[:8]
    katalog_sesji = f"/tmp/downloads_{session_id}"
    
    if os.path.exists(katalog_sesji): shutil.rmtree(katalog_sesji)
    os.makedirs(katalog_sesji)

    with SB(uc=False, browser="chrome", binary_location=executable_path, headless=True, xvfb=True,
            chromium_arg="--no-sandbox,--disable-dev-shm-usage") as sb:
        try:
            sb.execute_cdp_cmd("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": katalog_sesji})

            log_callback(f"🌐 [{session_id}] Łączenie z RDF...")
            sb.open("https://rdf-przegladarka.ms.gov.pl/")
            
            # Wpisanie KRS i próba wyszukania
            sb.type("input[formcontrolname='numerKRS']", krs)
            time.sleep(1)
            sb.click("button[type='submit']") # Klikamy bezpośrednio w button zamiast w span
            
            log_callback(f"⏳ [{session_id}] Oczekiwanie na odpowiedź serwera...")
            
            # --- KROK 1: SPRAWDZAMY CZY PODMIOT ISTNIEJE ---
            found = False
            for _ in range(15): # Czekamy max 15 sekund
                if sb.is_text_visible("Nie znaleziono podmiotu", timeout=1):
                    return None, None, "Podany numer KRS nie figuruje w bazie RDF."
                if sb.is_element_visible("table"):
                    found = True
                    break
                time.sleep(1)
            
            if not found:
                return None, None, "Serwer Ministerstwa nie zwrócił wyników (Timeout). Spróbuj ponownie."

            # --- KROK 2: POBIERANIE NAZWY (ODPORNIEJSZE) ---
            try:
                # Szukamy nazwy w kontenerze szczegółów
                nazwa_firmy = sb.get_text("div.p-grid div:nth-child(2)").split('\n')[1]
            except:
                try:
                    nazwa_firmy = sb.get_text("div:contains('Nazwa / firma podmiotu') + div")
                except:
                    nazwa_firmy = f"Podmiot KRS {krs}"
            
            log_callback(f"🏢 [{session_id}] Firma: {nazwa_firmy}")

            # --- KROK 3: ROZSZERZANIE TABELI ---
            try:
                sb.execute_script("document.querySelector('p-dropdown').click()")
                time.sleep(1)
                sb.click("li[aria-label='100']")
                time.sleep(3)
            except: pass

            # Filtrowanie
            try:
                sb.click("span.p-button-label:contains('Pokaż filtry')", timeout=5)
                sb.click("span#rodzajDokumentuNazwaInput")
                sb.click('li:contains("Roczne sprawozdanie finansowe")')
                sb.click("button:contains('Filtruj')")
                time.sleep(4)
            except: pass

            # Skanowanie i pobieranie
            wiersze = sb.find_elements("tbody tr")
            wiersze_do_pobrania = []
            for i, w in enumerate(wiersze):
                if "roczne sprawozdanie finansowe" in w.text.lower():
                    wiersze_do_pobrania.append(i + 1)
                if len(wiersze_do_pobrania) >= limit_lat: break

            if not wiersze_do_pobrania:
                return [], nazwa_firmy, None

            pobrane_zips = []
            for i, pos in enumerate(wiersze_do_pobrania):
                try:
                    btn_row = f"tbody tr:nth-child({pos}) button"
                    sb.scroll_to(btn_row)
                    sb.click(btn_row)
                    time.sleep(2)
                    sb.click("button:contains('Pobierz dokumenty')")
                    
                    for _ in range(40):
                        time.sleep(1)
                        found_files = glob.glob(os.path.join(katalog_sesji, '*.zip'))
                        new_files = [f for f in found_files if f not in pobrane_zips and not f.endswith('.crdownload')]
                        if new_files:
                            path = os.path.join(katalog_sesji, f"file_{pos}.zip")
                            os.rename(new_files[0], path)
                            pobrane_zips.append(path)
                            log_callback(f"✅ [{session_id}] Dokument {i+1} pobrany.")
                            break
                    sb.click(btn_row)
                except: continue

            # Analiza plików
            log_callback(f"🧠 [{session_id}] Analiza plików...")
            for zp in pobrane_zips:
                try:
                    with zipfile.ZipFile(zp, 'r') as z:
                        for fname in z.namelist():
                            if fname.endswith('.xml'):
                                with z.open(fname) as fxml:
                                    raw = fxml.read().decode('utf-8', errors='ignore')
                                    skala = 1
                                    z_m = re.search(r'WielkoscZaokraglen[^>]*?>(\d+)<', raw)
                                    if z_m and z_m.group(1) == "3": skala = 1000
                                    elif z_m and z_m.group(1) == "6": skala = 1000000
                                    if skala == 1 and ("tys. PLN" in raw[:5000] or "tysiącach złotych" in raw[:5000]):
                                        skala = 1000

                                    rok_m = re.search(r'DataDo[^>]*?>(\d{4})', raw)
                                    rok = rok_m.group(1) if rok_m else "????"
                                    
                                    for tag in ['P_ID_11', 'P_ID_10', 'P_ID_9']:
                                        pattern = rf'<{tag}[^>]*?>(.*?)</{tag}>'
                                        match = re.search(pattern, raw, re.DOTALL)
                                        if match:
                                            rb_m = re.search(r'<RB[^>]*?>(.*?)</RB>', match.group(1), re.DOTALL)
                                            if rb_m:
                                                val = wyciagnij_liczbe(rb_m.group(1)) * skala
                                                results.append({"Rok": rok, "Podatek": val})
                                                break
                                break 
                except: pass

            return results, nazwa_firmy, None

        except Exception as e:
            return None, nazwa_firmy, f"Błąd krytyczny bota: {str(e)}"
        finally:
            if os.path.exists(katalog_sesji): shutil.rmtree(katalog_sesji)

# --- UI STREAMLIT ---
st.set_page_config(page_title="Scanner Podatkowy KRS", page_icon="📊", layout="wide")
st.title("📊 Analityk Podatkowy KRS")

with st.sidebar:
    st.header("⚙️ Ustawienia")
    krs_val = st.text_input("Numer KRS", max_chars=10)
    lat_val = st.slider("Liczba lat:", 1, 5, 5)
    start_btn = st.button("Szukaj 🔍", use_container_width=True)

if start_btn and krs_val:
    with st.status("🕵️ Praca bota...", expanded=True) as status:
        log_area = st.empty()
        log_list = []
        def my_log(m):
            log_list.append(m); log_area.code("\n".join(log_list[-5:]))
        
        wyniki, firma, blad = wykonaj_analize_krs(krs_val, my_log, lat_val)
        status.update(label="Gotowe!", state="complete")

    if blad:
        st.error(f"❌ {blad}")
    elif firma:
        st.header(f"🏢 {firma}")
        if wyniki:
            df = pd.DataFrame(wyniki).sort_values("Rok", ascending=False).drop_duplicates("Rok")
            df_disp = df.copy()
            df_disp["Podatek"] = df_disp["Podatek"].apply(formatuj_walute)
            st.table(df_disp)
            st.metric("Suma podatku", formatuj_walute(df["Podatek"].sum()))
        else:
            st.warning("⚠️ Nie znaleziono sprawozdań finansowych spełniających kryteria.")
