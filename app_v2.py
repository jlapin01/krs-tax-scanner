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

# --- KONFIGURACJA ŚRODOWISKA DLA STREAMLIT CLOUD ---
os.environ["SELENIUMBASE_DRIVER_PATH"] = "/tmp/drivers"
os.environ["UC_DRIVER_PATH"] = "/tmp/uc_drivers"

# --- POMOCNICZE FUNKCJE FORMATOWANIA I PARSOWANIA ---

def formatuj_walute(kwota):
    """Formatuje liczbę na standard: 1 234 567,89 PLN"""
    return f"{kwota:,.2f}".replace(",", " ").replace(".", ",") + " PLN"

def wyciagnij_liczbe(raw_html):
    """Wyciąga czystą liczbę z tekstu XML/HTML"""
    if not raw_html: return 0.0
    clean = re.sub(r'<[^>]+>', '', raw_html)
    clean = "".join(clean.split()).replace(',', '.')
    # Wyłuskanie cyfr, kropki i minusa
    match = re.search(r'[\d\.\-]+', clean)
    return float(match.group()) if match else 0.0

# --- GŁÓWNA LOGIKA BOTA ---

def wykonaj_analize_krs(krs, log_callback, limit_lat):
    results = []
    nazwa_firmy = None
    
    # Wykrywanie przeglądarki (Chromium na Streamlit Cloud / Chrome lokalnie)
    executable_path = shutil.which("chromium") or shutil.which("google-chrome") or shutil.which("brave-browser")
    
    # IZOLACJA SESJI: Tworzymy unikalny folder dla każdego zapytania
    session_id = str(uuid.uuid4())[:8]
    katalog_sesji = f"/tmp/downloads_{session_id}"
    
    if os.path.exists(katalog_sesji):
        shutil.rmtree(katalog_sesji)
    os.makedirs(katalog_sesji)

    # Start przeglądarki
    with SB(uc=False, browser="chrome", binary_location=executable_path, headless=True, xvfb=True,
            chromium_arg="--no-sandbox,--disable-dev-shm-usage") as sb:
        try:
            # Wymuszenie zapisu plików w unikalnym folderze sesji
            sb.execute_cdp_cmd("Page.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": katalog_sesji
            })

            log_callback(f"🌐 [Sesja {session_id}] Łączenie z bazą Ministerstwa...")
            sb.open("https://rdf-przegladarka.ms.gov.pl/")
            
            # Wyszukiwanie KRS
            sb.type("input[formcontrolname='numerKRS']", krs)
            sb.click("span.p-button-label:contains('Wyszukaj')")
            sb.wait_for_element("table", timeout=20)
            
            # Pobieranie nazwy firmy
            try:
                nazwa_firmy = sb.get_text("div:contains('Nazwa / firma podmiotu') + div")
                log_callback(f"🏢 Firma: {nazwa_firmy}")
            except:
                nazwa_firmy = f"Podmiot o KRS {krs}"

            # Filtrowanie dokumentów
            sb.click("span.p-button-label:contains('Pokaż filtry')")
            time.sleep(1)
            sb.click("span#rodzajDokumentuNazwaInput")
            sb.click("li:contains('Roczne sprawozdanie finansowe')")
            sb.click("button:contains('Filtruj')")
            time.sleep(5) # Czas na odświeżenie tabeli

            wiersze = sb.find_elements("tbody tr")
            ile_pobrac = min(limit_lat, len(wiersze))
            log_callback(f"🔎 Wykryto {len(wiersze)} sprawozdań. Analizuję {ile_pobrac}...")

            pobrane_zips = []
            for i in range(1, ile_pobrac + 1):
                try:
                    btn_row = f"tbody tr:nth-child({i}) button"
                    sb.click(btn_row)
                    time.sleep(2)
                    
                    btn_download = "button:contains('Pobierz dokumenty')"
                    sb.wait_for_element(btn_download, timeout=10)
                    sb.click(btn_download)
                    
                    # Oczekiwanie na ZIP w dedykowanym folderze
                    plik_ok = False
                    for _ in range(45):
                        time.sleep(1)
                        found = glob.glob(os.path.join(katalog_sesji, '*.zip'))
                        new_files = [f for f in found if f not in pobrane_zips and not f.endswith('.crdownload')]
                        if new_files:
                            path = os.path.join(katalog_sesji, f"file_{i}.zip")
                            os.rename(new_files[0], path)
                            pobrane_zips.append(path)
                            log_callback(f"✅ Pobrano dokument {i}.")
                            plik_ok = True
                            break
                    
                    if not plik_ok:
                        log_callback(f"❌ Timeout pobierania dla dokumentu {i}.")
                    
                    sb.click(btn_row) # Zwiń
                    time.sleep(1)
                except Exception as e:
                    log_callback(f"⚠️ Pinięcie dokumentu {i} przez błąd interfejsu.")

            log_callback(f"🧠 Analiza XML (Detekcja skali i tagów)...")
            for zp in pobrane_zips:
                try:
                    with zipfile.ZipFile(zp, 'r') as z:
                        for fname in z.namelist():
                            if fname.endswith('.xml'):
                                with z.open(fname) as fxml:
                                    raw = fxml.read().decode('utf-8', errors='ignore')
                                    
                                    # 1. DETEKCJA SKALI (WielkoscZaokraglen)
                                    skala = 1
                                    zaokr_m = re.search(r'<[^>]*?WielkoscZaokraglen[^>]*?>(.*?)</[^>]*?WielkoscZaokraglen>', raw)
                                    if zaokr_m:
                                        z_val = zaokr_m.group(1).strip()
                                        if z_val == "3": 
                                            skala = 1000
                                            log_callback("📏 Skala: tysiące (x1000)")
                                        elif z_val == "6": 
                                            skala = 1000000
                                            log_callback("📏 Skala: miliony (x1000000)")

                                    # 2. ROK SPRAWOZDAWCZY
                                    rok_m = re.search(r'<[^>]*?DataDo[^>]*?>(\d{4})', raw)
                                    rok = rok_m.group(1) if rok_m else "????"
                                    
                                    # 3. SZUKANIE PODATKU (P_ID_11 i pokrewne)
                                    val = 0.0
                                    for tag in ['P_ID_11', 'P_ID_10', 'P_ID_9']:
                                        pattern = rf'<[^>]*?{tag}[^>]*?>(.*?)</[^>]*?{tag}>'
                                        match = re.search(pattern, raw, re.DOTALL)
                                        if match:
                                            rb_m = re.search(r'<[^>]*?RB[^>]*?>(.*?)</[^>]*?RB>', match.group(1), re.DOTALL)
                                            if rb_m:
                                                # Mnożymy przez wykrytą skalę
                                                val = wyciagnij_liczbe(rb_m.group(1)) * skala
                                                log_callback(f"💰 {rok}: Wykryto dane.")
                                                break
                                    
                                    results.append({"Rok": rok, "Podatek": val})
                                break 
                except Exception as e_zip:
                    log_callback(f"⚠️ Błąd analizy ZIP: {e_zip}")

            return results, nazwa_firmy

        except Exception as e:
            log_callback(f"💥 Błąd krytyczny: {e}")
            return [], nazwa_firmy
        finally:
            # CZYSZCZENIE: Usuwamy folder sesji po zakończeniu pracy
            if os.path.exists(katalog_sesji):
                shutil.rmtree(katalog_sesji)

# --- INTERFEJS STREAMLIT ---

st.set_page_config(page_title="Scanner Podatkowy KRS", page_icon="📊", layout="wide")

st.title("📊 Analityk Podatkowy KRS")
st.markdown("Automatyczna ekstrakcja podatku dochodowego z systemu Ministerstwa Sprawiedliwości.")

with st.sidebar:
    st.header("⚙️ Ustawienia")
    krs_val = st.text_input("Numer KRS", max_chars=10, placeholder="Np. 0000032892")
    lat_val = st.slider("Liczba lat do wstecz:", 1, 5, 1)
    
    st.divider()
    start_btn = st.button("Szukaj i Analizuj 🔍", use_container_width=True)
    if st.button("Reset 🧹", use_container_width=True):
        st.rerun()

if start_btn and krs_val:
    if len(krs_val) == 10 and krs_val.isdigit():
        with st.status("🕵️ Bot pracuje (proszę nie odświeżać strony)...", expanded=True) as status:
            log_area = st.empty()
            log_list = []
            def my_logger(m):
                log_list.append(m)
                log_area.code("\n".join(log_list[-5:]))
            
            wyniki, firma = wykonaj_analize_krs(krs_val, my_logger, lat_val)
            status.update(label="Analiza zakończona!", state="complete")

        if firma:
            st.divider()
            st.header(f"🏢 {firma}")
            
            if wyniki:
                df = pd.DataFrame(wyniki).sort_values("Rok", ascending=False)
                
                # Formatowanie do wyświetlenia
                df_disp = df.copy()
                df_disp["Podatek"] = df_disp["Podatek"].apply(formatuj_walute)
                
                col_tab, col_met = st.columns([2, 1])
                
                with col_tab:
                    st.subheader("Zestawienie roczne")
                    st.table(df_disp)
                
                with col_met:
                    st.subheader("Podsumowanie")
                    suma = df["Podatek"].sum()
                    st.metric(label=f"Suma wpłat ({len(wyniki)} lat)", value=formatuj_walute(suma))
                    st.info("UWAGA: System automatycznie wykrywa skalę (tysiące/miliony) na podstawie nagłówka XML.")
            else:
                st.warning("Pobrano dokumenty, ale nie znaleziono w nich oczekiwanych tagów podatkowych (P_ID_11).")
        else:
            st.error("Nie udało się pobrać danych dla tego numeru KRS.")
    else:
        st.error("Numer KRS musi składać się z dokładnie 10 cyfr.")
