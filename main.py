import os
import sys
import json
import time
import asyncio
import sqlite3
import hashlib
import shutil
import logging
import subprocess
from datetime import datetime
from typing import List, Optional

from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeFilename

# --- YAPILANDIRMA VE SABİTLER ---
CONFIG_FILE = "config.json"
STATE_FILE = "state.json"
DB_FILE = "db/pdfs.sqlite"
LOG_DIR = "logs"
CATEGORIES = ["TYT", "AYT", "YDT", "DIGER"]
MAX_RETRIES = 6
RETRY_DELAY = 10
CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB chunk ile maksimum hız

# --- VERİTABANI YÖNETİMİ ---
def init_db() -> None:
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS downloaded_pdfs (
                hash TEXT PRIMARY KEY,
                filename TEXT,
                category TEXT,
                storage_path TEXT,
                download_date DATETIME
            )
        ''')

def is_hash_downloaded(file_hash: str) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM downloaded_pdfs WHERE hash = ?", (file_hash,))
        return cursor.fetchone() is not None

def save_to_db(file_hash: str, filename: str, category: str, path: str) -> None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''
            INSERT INTO downloaded_pdfs (hash, filename, category, storage_path, download_date)
            VALUES (?, ?, ?, ?, ?)
        ''', (file_hash, filename, category, path, datetime.now().isoformat()))

def get_total_downloaded_from_db() -> int:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM downloaded_pdfs")
        return cursor.fetchone()[0]

# --- SİSTEM VE DURUM (STATE) YÖNETİMİ ---
def get_battery_percentage() -> int:
    """Termux-API aracılığıyla batarya yüzdesini döndürür."""
    try:
        res = subprocess.run(["termux-battery-status"], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            return data.get("percentage", 100)
    except Exception:
        pass
    return 100 # API yoksa veya hata verirse %100 kabul et

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_state(state: dict) -> None:
    state["last_run_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=4)

# --- DEPOLAMA VE YARDIMCI FONKSİYONLAR ---
def load_config() -> dict:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(config: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)

def setup_logger() -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    log_file = os.path.join(LOG_DIR, f"{timestamp}.txt")
    
    logger = logging.getLogger("PDFDownloader")
    logger.setLevel(logging.INFO)
    
    if not logger.handlers:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    return logger

def get_storage_usage(path: str) -> float:
    check_path = path if os.path.exists(path) else os.path.dirname(path.rstrip('/'))
    if not os.path.exists(check_path):
        check_path = "/" 
    try:
        total, used, free = shutil.disk_usage(check_path)
        return (used / total) * 100
    except Exception:
        return 100.0

def get_active_storage(configured_paths: List[str]) -> Optional[str]:
    for path in configured_paths:
        if not os.path.exists(path):
            try:
                os.makedirs(path, exist_ok=True)
            except OSError:
                continue
        if get_storage_usage(path) < 95.0:
            return path
    return None

def determine_category(filename: str) -> str:
    fname_upper = filename.upper()
    for cat in ["TYT", "AYT", "YDT"]:
        if cat in fname_upper:
            return cat
    return "DIGER"

def clear_screen() -> None:
    os.system("clear" if os.name == "posix" else "cls")

def parse_channel_input(user_input: str):
    user_input = user_input.strip()
    if user_input.replace('-', '').isdigit():
        channel_id = int(user_input)
        if channel_id > 0:
            return int(f"-100{channel_id}")
        return channel_id
    return user_input

# --- ANA İNDİRME İŞLEMİ (ASYNC) ---
async def start_download_process(config: dict, logger: logging.Logger, is_resume: bool = False) -> None:
    api_id = config.get("api_id")
    api_hash = config.get("api_hash")
    
    if not api_id or not api_hash:
        print("\n[!] Hata: config.json içinde api_id ve api_hash eksik!")
        input("Devam etmek için Enter'a basın...")
        return

    if is_resume:
        state = load_state()
        if not state or "channels" not in state:
            print("\n[-] Devam edilecek kayıtlı bir oturum bulunamadı.")
            input("Devam etmek için Enter'a basın...")
            return
        print(f"\n[*] {state.get('last_run_date', 'Bilinmeyen tarih')} tarihli oturumdan devam ediliyor...")
    else:
        raw_input = input("\nKanalları virgülle ayırarak girin (Max 10) (Örn: @kanal1, -100123456): ").strip()
        if not raw_input:
            return
        
        channels = [c.strip() for c in raw_input.split(",") if c.strip()][:10]
        state = {
            "channels": channels,
            "current_index": 0,
            "last_id": 0
        }
        save_state(state)

    client = TelegramClient('session_pdf', int(api_id), api_hash)
    try:
        await client.start()
    except Exception as e:
        print(f"\n[!] Telegram bağlantı hatası: {e}")
        input("Devam etmek için Enter'a basın...")
        return

    blacklist = [word.lower() for word in config.get("blacklist", [])]
    downloaded_count = 0
    failed_count = 0
    storage_stats = {path: 0 for path in config["storage_paths"]}
    start_time = time.time()

    for idx in range(state["current_index"], len(state["channels"])):
        channel_raw = state["channels"][idx]
        entity = parse_channel_input(channel_raw)
        last_msg_id = state.get("last_id", 0)

        print(f"\n[>] KANAL BAŞLIYOR: {channel_raw} (Başlangıç ID: {last_msg_id})")
        
        try:
            # reverse=True ile eskiden yeniye doğru doğrudan indiriyoruz, RAM kullanımı sıfıra yakın.
            async for msg in client.iter_messages(entity, reverse=True, min_id=last_msg_id):
                
                # Batarya Kontrolü
                battery = get_battery_percentage()
                if battery <= 5:
                    logger.warning(f"Batarya kritik seviyede (%{battery}). Durum kaydedilip çıkılıyor.")
                    print(f"\n\n[!] DİKKAT: Şarjınız %{battery}. İndirme durduruldu ve ilerleme kaydedildi!")
                    save_state(state)
                    await client.disconnect()
                    return

                if not getattr(msg, 'document', None):
                    continue

                filename = "Bilinmeyen_Dosya.pdf"
                for attr in msg.document.attributes:
                    if isinstance(attr, DocumentAttributeFilename):
                        filename = attr.file_name
                        break
                
                if not filename.lower().endswith(".pdf"):
                    continue
                    
                is_blacklisted = any(bad_word in filename.lower() for bad_word in blacklist)
                if is_blacklisted:
                    state["last_id"] = msg.id
                    save_state(state)
                    continue

                # Depolama Kontrolü
                active_storage = get_active_storage(config["storage_paths"])
                if not active_storage:
                    logger.error("TÜM DEPOLAMA ALANLARI DOLU (%95+). İşlem durduruluyor.")
                    print("\n\n[!] KRİTİK HATA: Tüm depolama alanları dolu! İşlem durduruldu.")
                    save_state(state)
                    await client.disconnect()
                    return

                category = determine_category(filename)
                target_dir = os.path.join(active_storage, category)
                os.makedirs(target_dir, exist_ok=True)
                temp_file = os.path.join(target_dir, f"{msg.id}_temp.pdf")
                
                success = False
                file_hash = ""
                file_size = 0

                # İndirme İşlemi
                for attempt in range(1, MAX_RETRIES + 1):
                    try:
                        sha256 = hashlib.sha256()
                        with open(temp_file, "wb") as f:
                            async for chunk in client.iter_download(msg.document, chunk_size=CHUNK_SIZE):
                                f.write(chunk)
                                sha256.update(chunk)
                                file_size += len(chunk)
                        
                        file_hash = sha256.hexdigest()
                        success = True
                        break
                    except Exception as e:
                        if os.path.exists(temp_file):
                            os.remove(temp_file)
                        if attempt < MAX_RETRIES:
                            await asyncio.sleep(RETRY_DELAY)
                        
                if not success:
                    failed_count += 1
                    logger.error(f"Başarısız: {filename}")
                    continue

                # Kopya Kontrol ve Kayıt
                if is_hash_downloaded(file_hash):
                    os.remove(temp_file)
                else:
                    final_path = os.path.join(target_dir, filename)
                    if os.path.exists(final_path):
                        name, ext = os.path.splitext(filename)
                        final_path = os.path.join(target_dir, f"{name}_{file_hash[:8]}{ext}")
                        
                    os.rename(temp_file, final_path)
                    save_to_db(file_hash, filename, category, final_path)
                    downloaded_count += 1
                    storage_stats[active_storage] += file_size
                    logger.info(f"İndirildi: {filename}")

                # İlerlemeyi Kaydet
                state["last_id"] = msg.id
                save_state(state)

                sys.stdout.write(f"\r[>] {channel_raw}: Son ID {msg.id} işlendi. (İndirilen: {downloaded_count}) | Şarj: %{battery} ")
                sys.stdout.flush()

        except Exception as e:
            print(f"\n[!] Kanal işlenirken hata oluştu ({channel_raw}): {e}")
            logger.error(f"Kanal hatası {channel_raw}: {e}")

        # Bir kanal bittiğinde diğerine geçerken 60 saniye bekle
        if idx < len(state["channels"]) - 1:
            state["current_index"] += 1
            state["last_id"] = 0
            save_state(state)
            print(f"\n\n[+] {channel_raw} tamamlandı. Sistemin dinlenmesi için 60 saniye bekleniyor...")
            for i in range(60, 0, -1):
                sys.stdout.write(f"\rSonraki kanala geçişe: {i} saniye ")
                sys.stdout.flush()
                await asyncio.sleep(1)
            print()

    # Tüm kanallar bittiğinde state'i temizle
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)

    await client.disconnect()
    
    end_time = time.time()
    elapsed_time = end_time - start_time
    m, s = divmod(elapsed_time, 60)
    h, m = divmod(m, 60)
    
    print("\n\n=== İNDİRME TAMAMLANDI ===")
    print(f"- Toplam Süre: %d:%02d:%02d" % (h, m, s))
    print(f"- Başarıyla İndirilen Yeni Dosya: {downloaded_count}")
    print(f"- Başarısız/Hatalı: {failed_count}")
    
    for path, size in storage_stats.items():
        if size > 0:
            size_mb = size / (1024 * 1024)
            print(f"- {path} alanına yazılan: {size_mb:.2f} MB")
            
    input("\nAna menüye dönmek için Enter'a basın...")

# --- ARAYÜZ (UI) ---
def print_header(config: dict) -> None:
    clear_screen()
    now = datetime.now()
    print("=" * 60)
    print(f"Tarih: {now.strftime('%Y-%m-%d')} | Saat: {now.strftime('%H:%M')}")
    
    for path in config.get("storage_paths", []):
        usage = get_storage_usage(path)
        status = f"%{usage:.1f} Dolu"
        warn = "[!] DOLMAK ÜZERE" if usage >= 95.0 else ""
        print(f"Depolama: {status} ({path}) {warn}")
        
    total_db = get_total_downloaded_from_db()
    print(f"Toplam İndirilen PDF (DB): {total_db}")
    
    state = load_state()
    if state and "last_run_date" in state:
        print(f"[*] Kayıtlı oturum var. (Son işlem: {state['last_run_date']})")
        
    print("=" * 60)

def main_menu() -> None:
    init_db()
    
    while True:
        config = load_config()
        print_header(config)
        
        print("\nANA MENÜ:")
        print("1) Yeni indirme başlat (Çoklu Kanal)")
        print("2) Yarım kalan indirmeye devam et")
        print("3) Kara liste düzenle")
        print("4) Depolama alanı manuel ekle/gör")
        print("5) Logları görüntüle")
        print("6) Çıkış")
        
        choice = input("\nSeçiminiz (1-6): ").strip()
        
        if choice == '1':
            logger = setup_logger()
            try:
                asyncio.run(start_download_process(config, logger, is_resume=False))
            except KeyboardInterrupt:
                print("\n[!] Kullanıcı tarafından iptal edildi. Güvenli çıkış yapılıyor...")
                time.sleep(1)
        
        elif choice == '2':
            logger = setup_logger()
            try:
                asyncio.run(start_download_process(config, logger, is_resume=True))
            except KeyboardInterrupt:
                print("\n[!] Kullanıcı tarafından iptal edildi. Güvenli çıkış yapılıyor...")
                time.sleep(1)
                
        elif choice == '3':
            clear_screen()
            print("--- Kara Liste Yönetimi ---")
            print("Mevcut yasaklı kelimeler:", ", ".join(config["blacklist"]))
            new_words = input("Eklemek istediğiniz kelimeleri virgülle ayırarak girin (Boş bırakırsanız değişmez): ")
            if new_words:
                words = [w.strip() for w in new_words.split(",") if w.strip()]
                config["blacklist"].extend(words)
                config["blacklist"] = list(set(config["blacklist"]))
                save_config(config)
                print("[+] Kara liste güncellendi!")
                time.sleep(1)
                
        elif choice == '4':
            clear_screen()
            print("--- Depolama Alanı Yönetimi ---")
            print("Mevcut Alanlar:")
            for i, p in enumerate(config["storage_paths"]):
                print(f"{i+1}. {p}")
                
            print("\nYeni bir depolama yolu eklemek için tam dizini yazın.")
            print("Örnek: /storage/emulated/0/Belgeler")
            print("Veya harici SD kart: /storage/1234-5678/PDFler")
            new_path = input("\nYeni Yol (İptal etmek için boş bırakın): ").strip()
            
            if new_path:
                if new_path not in config["storage_paths"]:
                    config["storage_paths"].append(new_path)
                    save_config(config)
                    print("[+] Depolama alanı başarıyla eklendi!")
                else:
                    print("[-] Bu yol zaten ekli.")
                time.sleep(2)
                
        elif choice == '5':
            clear_screen()
            os.makedirs(LOG_DIR, exist_ok=True)
            logs = sorted(os.listdir(LOG_DIR), reverse=True)
            if not logs:
                print("[-] Kayıtlı log bulunamadı.")
            else:
                print("En son log dosyası okunuyor...\n")
                with open(os.path.join(LOG_DIR, logs[0]), "r", encoding="utf-8") as f:
                    print(f.read()[-2000:]) 
            input("\nDevam etmek için Enter'a basın...")
            
        elif choice == '6':
            print("Çıkış yapılıyor. Görüşmek üzere!")
            sys.exit(0)

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\n[!] Programdan çıkılıyor...")
        sys.exit(0)
