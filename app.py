import streamlit as st
from seleniumbase import SB
import time
import os
import glob
import zipfile
import re
import pandas as pd
import shutil
import random

# --- KONFIGURACJA ŚRODOWISKA DLA STREAMLIT CLOUD ---
os.environ["SELENIUMBASE_DRIVER_PATH"] = "/tmp/drivers"
os.environ["UC_DRIVER_PATH"] = "/tmp/uc_drivers"

# --- FUNKCJA FORMATUJĄCA LICZBY NA POLSKI STANDARD ---
def formatuj_walute(kwota):
    # Format: 1 234 567,89 PLN
    return f"{kwota:,.2f}".replace(",", " ").replace(".", ",") + " PLN"

# --- FUNKCJA CZYSZCZĄCA LICZBY Z XML ---
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
    
    katalog = "/tmp/downloads"
    if os.path.exists(katalog): shutil.rmtree(katalog)
    os.makedirs(katalog)

    with SB(uc=False, browser="chrome", binary_location=executable_path, headless=True, xvfb=True,
            chromium_arg="--no-sandbox,--disable-dev-shm-usage") as sb:
        try:
            # Wymuszenie ścieżki pobierania dla Chrome w kontenerze
            sb.execute_cdp_cmd("Page.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": katalog
            })

            log_callback("🌐 Łączenie z Ministerstwem Sprawiedliwości...")
            sb.open("https://rdf-przegladarka.ms.gov.pl/")
            
            sb.type("input[formcontrolname='numerKRS']", krs)
            sb.click("span.p-button-label:contains('Wyszukaj')")
            sb.wait_for_element("table", timeout=20)
            
            nazwa_firmy = sb.get_text("div:contains('Nazwa / firma podmiotu') + div")
            log_callback(f"🏢 Firma: {nazwa_firmy}")

            # Filtrowanie rocznych sprawozdań
            sb.click("span.p-button-label:contains('Pokaż filtry')")
            time.sleep(1)
            sb.click("span#rodzajDokumentuNazwaInput")
            sb.click("li:contains('Roczne sprawozdanie finansowe')")
            sb.click("button:contains('Filtruj')")
            time.sleep(5)

            wiersze = sb.find_elements("tbody tr")
            ile_faktycznie = min(limit_lat, len(wiersze))
            log_callback(f"🔎 Wykryto {len(wiersze)} pozycji. Pobieram {ile_faktycznie} ostatnie...")

            pobrane_zips = []
            for i in range(1, ile_faktycznie + 1):
                try:
                    btn_row = f"tbody tr:nth-child({i}) button"
                    log_callback(f"📂 Otwieram wiersz {i}...")
                    sb.click(btn_row)
                    time.sleep(2)
                    
                    btn_download = "button:contains('Pobierz dokumenty')"
                    sb.wait_for_element(btn_download, timeout=10)
                    sb.click(btn_download)
                    log_callback(f"⏳ Pobieranie pliku ZIP...")
                    
                    # Czekanie na fizyczne pojawienie się pliku na dysku
                    plik_ok = False
                    for sekunda in range(45):
                        time.sleep(1)
                        found = glob.glob(os.path.join(katalog, '*.zip'))
                        # Ignorujemy pliki tymczasowe Chrome (.crdownload)
                        new_files = [f for f in found if f not in pobrane_zips and not f.endswith('.crdownload')]
                        if new_files:
                            path = os.path.join(katalog, f"data_{i}.zip")
                            os.rename(new_files[0], path)
                            pobrane_zips.append(path)
                            log_callback(f"✅ Dokument {i} gotowy.")
                            plik_ok = True
                            break
                    
                    if not plik_ok: log_callback(f"❌ Przekroczono czas pobierania dla pliku {i}")
                    
                    sb.click(btn_row) # Zwiń wiersz
                    time.sleep(1)
                except Exception as e:
                    log_callback(f"⚠️ Problem z wierszem {i}: {str(e)[:40]}...")

            log_callback(f"🧠 Przeszukiwanie XML w {len(pobrane_zips)} archiwach...")
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
                                    # Szukanie kwoty w najczęstszych tagach podatkowych
                                    for tag in ['P_ID_11', 'P_ID_10', 'P_ID_9']:
                                        pattern = rf'<[^>]*?{tag}[^>]*?>(.*?)</[^>]*?{tag}>'
                                        match = re.search(pattern, raw, re.DOTALL)
                                        if match:
                                            rb = re.search(r'<[^>]*?RB[^>]*?>(.*?)</[^>]*?RB>', match.group(1), re.DOTALL)
                                            if rb:
                                                val = wyciagnij_liczbe(rb.group(1))
                                                log_callback(f"💰 {rok}: Odczytano wartość.")
                                                break
                                    results.append({"Rok": rok, "Podatek": val})
                except Exception:
                    log_callback(f"⚠️ Nie udało się otworzyć jednego z plików ZIP.")

            return results, nazwa_firmy
        except Exception as e:
            log_callback(f"💥 Błąd krytyczny: {e}")
            return [], nazwa_firmy

# --- INTERFEJS UŻYTKOWNIKA STREAMLIT ---
st.set_page_config(page_title="Scanner Podatkowy KRS", page_icon="📊", layout="wide")

st.title("📊 Analityk Podatkowy KRS")
st.markdown("Automatyczne pobieranie i analiza pozycji **P_ID_11** z systemu RDF.")

with st.sidebar:
    st.header("⚙️ Ustawienia")
    krs_val = st.text_input("Numer KRS", max_chars=10, placeholder="Np. 0000032892")
    lat_val = st.slider("Liczba lat wstecz:", 1, 5, 1)
    
    st.divider()
    start_btn = st.button("Szukaj 🔍", use_container_width=True)
    if st.button("Reset 🧹", use_container_width=True):
        st.rerun()

if start_btn and krs_val:
    if len(krs_val) == 10 and krs_val.isdigit():
        with st.status("🕵️ Bot pracuje (może to zająć chwilę)...", expanded=True) as status:
            log_area = st.empty()
            log_list = []
            def my_log(m):
                log_list.append(m)
                log_area.code("\n".join(log_list[-5:]))
            
            dane, nazwa = wykonaj_analize_krs(krs_val, my_log, lat_val)
            status.update(label="Analiza zakończona!", state="complete")

        if nazwa:
            st.divider()
            st.header(f"🏢 {nazwa}")
            
            if dane:
                # Przygotowanie danych do tabeli
                df = pd.DataFrame(dane).sort_values("Rok", ascending=False)
                
                # Kopia do wyświetlania z polskim formatowaniem
                df_view = df.copy()
                df_view["Podatek"] = df_view["Podatek"].apply(formatuj_walute)
                
                col_t, col_m = st.columns([2, 1])
                
                with col_t:
                    st.subheader("Zestawienie roczne")
                    st.table(df_view)
                
                with col_m:
                    st.subheader("Podsumowanie")
                    suma_num = df["Podatek"].sum()
                    st.metric(label=f"Suma z {len(dane)} lat", value=formatuj_walute(suma_num))
                    st.info("Wartości pochodzą z pola 'Rok Bieżący' (RB) w sekcji podatku dochodowego.")
            else:
                st.warning("Pobrano pliki, ale nie znaleziono w nich oczekiwanych danych finansowych.")
        else:
            st.error("Nie udało się pobrać danych. Upewnij się, że numer KRS jest poprawny.")
    else:
        st.error("Numer KRS musi składać się z 10 cyfr.")
