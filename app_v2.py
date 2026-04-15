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
    session_id = str(uuid.uuid4())[:8]
    katalog_sesji = f"/tmp/downloads_{session_id}"
    
    executable_path = shutil.which("chromium") or shutil.which("google-chrome")
    
    if os.path.exists(katalog_sesji): shutil.rmtree(katalog_sesji)
    os.makedirs(katalog_sesji)

    with SB(uc=False, browser="chrome", binary_location=executable_path, headless=True, xvfb=True,
            chromium_arg="--no-sandbox,--disable-dev-shm-usage") as sb:
        try:
            sb.execute_cdp_cmd("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": katalog_sesji})

            log_callback(f"🌐 Łączenie z systemem Ministerstwa...")
            sb.open("https://rdf-przegladarka.ms.gov.pl/")
            
            # Wpisanie KRS i szukanie
            sb.type("input[formcontrolname='numerKRS']", krs)
            time.sleep(1)
            
            # Próba kliknięcia "Wyszukaj"
            try:
                sb.click("button[type='submit']", timeout=10)
            except:
                return None, None, "Serwer Ministerstwa nie wyświetlił przycisku wyszukiwania. Prawdopodobnie trwa przerwa techniczna lub strona jest przeciążona."

            log_callback(f"⏳ Czekam na wyniki dla KRS {krs}...")
            
            # Weryfikacja czy wyniki się pojawiły
            found = False
            for _ in range(15):
                if sb.is_text_visible("Nie znaleziono podmiotu"):
                    return None, None, f"Podmiot o numerze KRS {krs} nie został znaleziony w bazie RDF."
                if sb.is_element_visible("table"):
                    found = True
                    break
                time.sleep(1)
            
            if not found:
                return None, None, "Strona z wynikami nie załadowała się na czas. Serwery rządowe odpowiadają zbyt wolno."

            # Pobieranie nazwy
            try:
                nazwa_firmy = sb.get_text("div:contains('Nazwa / firma podmiotu') + div")
                log_callback(f"🏢 Firma: {nazwa_firmy}")
            except:
                nazwa_firmy = f"Podmiot KRS {krs}"

            # Rozszerzanie tabeli i pobieranie (uproszczone dla stabilności)
            log_callback("📥 Pobieranie dokumentów...")
            wiersze = sb.find_elements("tbody tr")
            wiersze_do_pobrania = []
            for i, w in enumerate(wiersze):
                if "roczne sprawozdanie finansowe" in w.text.lower():
                    wiersze_do_pobrania.append(i + 1)
                if len(wiersze_do_pobrania) >= limit_lat: break

            pobrane_zips = []
            for i, pos in enumerate(wiersze_do_pobrania):
                try:
                    btn_row = f"tbody tr:nth-child({pos}) button"
                    sb.click(btn_row)
                    time.sleep(2)
                    sb.click("button:contains('Pobierz dokumenty')")
                    
                    for _ in range(30):
                        time.sleep(1)
                        found_files = glob.glob(os.path.join(katalog_sesji, '*.zip'))
                        new_files = [f for f in found_files if f not in pobrane_zips and not f.endswith('.crdownload')]
                        if new_files:
                            path = os.path.join(katalog_sesji, f"f_{pos}.zip")
                            os.rename(new_files[0], path)
                            pobrane_zips.append(path)
                            log_callback(f"✅ Pobrano dokument {i+1}")
                            break
                    sb.click(btn_row)
                except: continue

            # Analiza
            log_callback("🧠 Analiza danych...")
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
                                    
                                    rok_m = re.search(r'DataDo[^>]*?>(\d{4})', raw)
                                    rok = rok_m.group(1) if rok_m else "????"
                                    
                                    for tag in ['P_ID_11', 'P_ID_10', 'P_ID_9']:
                                        patt = rf'<{tag}[^>]*?>(.*?)</{tag}>'
                                        m = re.search(patt, raw, re.DOTALL)
                                        if m:
                                            rb_m = re.search(r'<RB[^>]*?>(.*?)</RB>', m.group(1), re.DOTALL)
                                            if rb_m:
                                                val = wyciagnij_liczbe(rb_m.group(1)) * skala
                                                results.append({"Rok": rok, "Podatek": val})
                                                break
                                break
                except: pass

            return results, nazwa_firmy, None

        except Exception as e:
            # --- TŁUMACZ BŁĘDÓW ---
            err = str(e)
            if "button[type='submit']" in err:
                msg = "Nie udało się kliknąć przycisku wyszukiwania. Strona Ministerstwa mogła się zawiesić."
            elif "timeout" in err.lower():
                msg = "Przekroczono czas oczekiwania na odpowiedź serwera Ministerstwa."
            elif "table" in err:
                msg = "System nie wyświetlił tabeli wyników. Spróbuj wpisać numer KRS ponownie."
            else:
                msg = f"Wystąpił nieoczekiwany problem z połączeniem (Kod sesji: {session_id})."
            
            return None, nazwa_firmy, msg
        finally:
            if os.path.exists(katalog_sesji): shutil.rmtree(katalog_sesji)

# --- INTERFEJS UI ---
st.set_page_config(page_title="Scanner Podatkowy KRS", page_icon="📊", layout="wide")
st.title("📊 Analityk Podatkowy KRS")

with st.sidebar:
    st.header("⚙️ Konfiguracja")
    krs_val = st.text_input("Numer KRS", max_chars=10)
    lat_val = st.slider("Liczba lat:", 1, 5, 5)
    start_btn = st.button("Szukaj 🔍", use_container_width=True)

if start_btn and krs_val:
    with st.status("🕵️ Praca bota...", expanded=True) as status:
        log_a = st.empty()
        log_l = []
        def my_log(m):
            log_l.append(m); log_a.code("\n".join(log_l[-5:]))
        
        wyniki, firma, blad = wykonaj_analize_krs(krs_val, my_log, lat_val)
        status.update(label="Gotowe!", state="complete")

    if blad:
        st.error(f"❌ {blad}")
        st.info("💡 Porada: Serwery Ministerstwa działają niestabilnie. Najlepiej spróbować ponownie za około 30 sekund.")
    elif firma:
        st.header(f"🏢 {firma}")
        if wyniki:
            df = pd.DataFrame(wyniki).sort_values("Rok", ascending=False).drop_duplicates("Rok")
            df_disp = df.copy()
            df_disp["Podatek"] = df_disp["Podatek"].apply(formatuj_walute)
            st.table(df_disp)
            st.metric("Suma podatku", formatuj_walute(df["Podatek"].sum()))
        else:
            st.warning("⚠️ Nie odnaleziono tagów finansowych w pobranych plikach.")
