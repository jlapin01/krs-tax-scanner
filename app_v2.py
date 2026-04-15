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

            log_callback(f"🌐 [{session_id}] Łączenie z bazą RDF...")
            sb.open("https://rdf-przegladarka.ms.gov.pl/")
            
            sb.type("input[formcontrolname='numerKRS']", krs)
            sb.click("span.p-button-label:contains('Wyszukaj')")
            sb.wait_for_element("table", timeout=20)
            
            nazwa_firmy = sb.get_text("div:contains('Nazwa / firma podmiotu') + div")
            log_callback(f"🏢 [{session_id}] Podmiot: {nazwa_firmy}")

            # --- KROK KLUCZOWY: ZMIANA STRONICOWANIA NA 100 WIERSZY ---
            try:
                log_callback(f"📏 [{session_id}] Rozszerzam tabelę do 100 wierszy...")
                # Klikamy w dropdown wyboru liczby wierszy (na dole po prawej)
                sb.click("p-dropdown.p-paginator-rpp-options", timeout=5)
                time.sleep(1)
                # Wybieramy opcję 100
                sb.click("li[aria-label='100']", timeout=5)
                time.sleep(3)
            except:
                log_callback(f"⚠️ Nie udało się rozwinąć tabeli do 100, sprawdzam co jest...")

            # Filtrowanie (próbujemy, ale nie polegamy tylko na nim)
            try:
                sb.click("span.p-button-label:contains('Pokaż filtry')", timeout=5)
                time.sleep(1)
                sb.click("span#rodzajDokumentuNazwaInput")
                sb.click('li:contains("Roczne sprawozdanie finansowe")', timeout=5)
                sb.click("button:contains('Filtruj')")
                time.sleep(4)
            except:
                pass

            # Skanujemy tabelę (teraz powinna mieć 100 wierszy na jednej stronie)
            wiersze = sb.find_elements("tbody tr")
            wiersze_do_pobrania = []
            
            for i, w in enumerate(wiersze):
                tekst = w.text.lower()
                if "roczne sprawozdanie finansowe" in tekst and "korekta" not in tekst:
                    wiersze_do_pobrania.append(i + 1)
                if len(wiersze_do_pobrania) >= limit_lat:
                    break

            log_callback(f"🔎 [{session_id}] Wykryto {len(wiersze_do_pobrania)} sprawozdań na liście.")

            pobrane_zips = []
            for i, pos in enumerate(wiersze_do_pobrania):
                try:
                    btn_row = f"tbody tr:nth-child({pos}) button"
                    sb.scroll_to(btn_row)
                    sb.click(btn_row)
                    time.sleep(2)
                    sb.click("button:contains('Pobierz dokumenty')")
                    
                    plik_ok = False
                    for _ in range(45):
                        time.sleep(1)
                        found = glob.glob(os.path.join(katalog_sesji, '*.zip'))
                        new_files = [f for f in found if f not in pobrane_zips and not f.endswith('.crdownload')]
                        if new_files:
                            path = os.path.join(katalog_sesji, f"file_{pos}.zip")
                            os.rename(new_files[0], path)
                            pobrane_zips.append(path)
                            log_callback(f"✅ [{session_id}] Dokument {i+1} pobrany.")
                            plik_ok = True
                            break
                    sb.click(btn_row)
                except: 
                    continue

            log_callback(f"🧠 [{session_id}] Analiza XML i detekcja skali...")
            for zp in pobrane_zips:
                try:
                    with zipfile.ZipFile(zp, 'r') as z:
                        for fname in z.namelist():
                            if fname.endswith('.xml'):
                                with z.open(fname) as fxml:
                                    raw = fxml.read().decode('utf-8', errors='ignore')
                                    
                                    # --- PANCERNA DETEKCJA SKALI ---
                                    skala = 1
                                    zaokr_match = re.search(r'WielkoscZaokraglen[^>]*?>(\d+)<', raw)
                                    if zaokr_match:
                                        z_val = zaokr_match.group(1)
                                        if z_val == "3": skala = 1000
                                        elif z_val == "6": skala = 1000000
                                    
                                    # Fallback: szukamy tekstu o tysiącach w pierwszych 5000 znaków
                                    if skala == 1 and ("tys. PLN" in raw[:5000] or "tysiącach złotych" in raw[:5000]):
                                        skala = 1000

                                    rok_m = re.search(r'DataDo[^>]*?>(\d{4})', raw)
                                    rok = rok_m.group(1) if rok_m else "????"
                                    
                                    val = 0.0
                                    found_tag = False
                                    for tag in ['P_ID_11', 'P_ID_10', 'P_ID_9']:
                                        pattern = rf'<{tag}[^>]*?>(.*?)</{tag}>'
                                        match = re.search(pattern, raw, re.DOTALL)
                                        if match:
                                            rb_m = re.search(r'<RB[^>]*?>(.*?)</RB>', match.group(1), re.DOTALL)
                                            if rb_m:
                                                val = wyciagnij_liczbe(rb_m.group(1)) * skala
                                                log_callback(f"💰 [{session_id}] {rok}: Skala x{skala}")
                                                found_tag = True
                                                break
                                    
                                    if found_tag:
                                        results.append({"Rok": rok, "Podatek": val})
                                break 
                except: pass

            return results, nazwa_firmy, None

        except Exception as e:
            return None, nazwa_firmy, str(e)
        finally:
            if os.path.exists(katalog_sesji): shutil.rmtree(katalog_sesji)

# --- UI STREAMLIT ---
st.set_page_config(page_title="Scanner Podatkowy KRS", page_icon="📊", layout="wide")
st.title("📊 Analityk Podatkowy KRS")

with st.sidebar:
    st.header("⚙️ Ustawienia")
    krs_val = st.text_input("Numer KRS", max_chars=10, placeholder="Np. 0000032892")
    lat_val = st.slider("Liczba lat:", 1, 5, 5)
    st.divider()
    start_btn = st.button("Analizuj 🔍", use_container_width=True)

if start_btn and krs_val:
    with st.status("🕵️ Bot pracuje...", expanded=True) as status:
        log_area = st.empty()
        log_list = []
        def my_log(m):
            log_list.append(m); log_area.code("\n".join(log_list[-5:]))
        
        wyniki, firma, blad = wykonaj_analize_krs(krs_val, my_log, lat_val)
        status.update(label="Analiza zakończona!", state="complete")

    if blad:
        st.error(f"❌ Błąd: {blad}")
    elif firma:
        st.header(f"🏢 {firma}")
        if wyniki:
            df = pd.DataFrame(wyniki).sort_values("Rok", ascending=False).drop_duplicates("Rok")
            df_disp = df.copy()
            df_disp["Podatek"] = df_disp["Podatek"].apply(formatuj_walute)
            st.table(df_disp)
            st.metric("Suma podatku", formatuj_walute(df["Podatek"].sum()))
        else:
            st.warning("⚠️ Nie znaleziono właściwych plików XML (P_ID_11).")
