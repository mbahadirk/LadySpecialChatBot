import os
import sys
import time
import logging
from pyngrok import ngrok, conf
from dotenv import load_dotenv

# Loglama ayarları
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# .env dosyasını yükle
load_dotenv()

def start_ngrok_tunnel():
    """
    Uvicorn sunucusu için Ngrok tüneli başlatır.
    """
    
    # .env dosyasından token'ı kontrol et
    # Eğer .env dosyasında NGROK_AUTHTOKEN tanımlıysa onu kullan,
    # yoksa sistemdeki varsayılan konfigürasyonu (varsa) kullanır.
    ngrok_auth_token = os.getenv("NGROK_AUTHTOKEN")
    if ngrok_auth_token:
        ngrok.set_auth_token(ngrok_auth_token)
        logger.info("Ngrok auth token .env dosyasından yüklendi.")
    
    # Hedef port (main.py dosyasındaki uvicorn portu ile aynı olmalı)
    target_port = 8000
    
    logger.info(f"Port {target_port} için Ngrok tüneli başlatılıyor...")
    
    try:
        # http tüneli aç
        public_url = ngrok.connect(target_port).public_url
        
        print("\n" + "="*50)
        print(f" NGROK TÜNELİ AKTİF")
        print("="*50)
        print(f" * Public URL: {public_url}")
        print(f" * Yerel Port: {target_port}")
        print(" * Webhook URL olarak kullanmak için yukarıdaki adresi kopyalayın.")
        print("="*50 + "\n")
        
        # Scriptin kapanmasını engellemek için döngüde bekle
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Kullanıcı tarafından durduruldu. Tünel kapatılıyor...")
            ngrok.disconnect(public_url)
            ngrok.kill()
            
    except Exception as e:
        logger.error(f"Ngrok başlatılırken hata oluştu: {e}")
        print("\nİPUCU: Eğer kimlik doğrulama hatası alıyorsanız, Ngrok dashboard'dan authtoken alıp .env dosyasına ekleyin:")
        print('NGROK_AUTHTOKEN=sizin_tokeniniz')

if __name__ == "__main__":
    start_ngrok_tunnel()
