import os
import sys
import json
import subprocess
import shutil

def check_python_version() -> None:
    """Python sürümünün 3.11 veya üzeri olduğunu kontrol eder."""
    if sys.version_info < (3, 11):
        print("[-] Hata: Bu uygulama Python 3.11 veya üzeri gerektirir.")
        sys.exit(1)

def install_requirements() -> None:
    """Gerekli Python paketlerini kurar."""
    print("[*] Gerekli paketler kontrol ediliyor/kuruluyor...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "telethon", "--upgrade", "--quiet"])
        print("[+] Paketler başarıyla kuruldu.")
    except subprocess.CalledProcessError:
        print("[-] Hata: Paket kurulumu başarısız oldu. İnternet bağlantınızı kontrol edin.")
        sys.exit(1)

def check_termux_storage() -> None:
    """Termux depolama izinlerini kontrol eder."""
    if not os.path.exists("/storage/emulated/0"):
        print("\n[!] DİKKAT: Termux depolama iznine sahip değil!")
        print("[!] Lütfen şu komutu çalıştırarak depolama izni verin:")
        print("    termux-setup-storage")
        print("[!] İzin verdikten sonra kurulumu tekrar başlatın.\n")
        sys.exit(1)
    else:
        print("[+] Termux depolama izni mevcut.")

def setup_environment() -> None:
    """Klasörleri ve config dosyasını oluşturur."""
    os.makedirs("logs", exist_ok=True)
    os.makedirs("db", exist_ok=True)
    
    config_path = "config.json"
    if not os.path.exists(config_path):
        default_config = {
            "api_id": "",
            "api_hash": "",
            "blacklist": ["deneme", "test", "silinecek"],
            "storage_paths": ["/storage/emulated/0/TelegramPDFs"]
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=4)
        print(f"[+] '{config_path}' oluşturuldu. Lütfen API bilgilerinizi girin.")
    else:
        print(f"[+] '{config_path}' zaten mevcut.")

def main() -> None:
    print("=== Telegram PDF İndirici Kurulumu ===")
    check_python_version()
    install_requirements()
    
    if "com.termux" in os.getenv("PREFIX", ""):
        check_termux_storage()
        
    setup_environment()
    print("\n[+] Kurulum tamamlandı!")
    print("[*] 'python main.py' komutu ile uygulamayı başlatabilirsiniz.")

if __name__ == "__main__":
    main()
