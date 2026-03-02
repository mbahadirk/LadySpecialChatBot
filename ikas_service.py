import os
import requests
import time
from dotenv import load_dotenv

load_dotenv()


class IkasClient:
    def __init__(self):
        # .env dosyasından ayarları al
        self.shop_name = os.getenv("IKAS_SHOP_NAME", "").strip()
        self.client_id = os.getenv("IKAS_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("IKAS_CLIENT_SECRET", "").strip()

        self.base_url = f"https://{self.shop_name}.myikas.com/api/admin"
        self.graphql_url = "https://api.myikas.com/api/v1/admin/graphql"
        self.token = None

    def authenticate(self):
        """Token alır ve kaydeder"""
        if not self.client_id or not self.client_secret:
            print("❌ Hata: .env dosyasında CLIENT_ID veya SECRET eksik.")
            return

        url = f"{self.base_url}/oauth/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }

        try:
            response = requests.post(url, data=payload, timeout=10)
            if response.status_code == 200:
                self.token = response.json().get("access_token")
            else:
                print(f"❌ Auth Hatası: {response.text}")
        except Exception as e:
            print(f"❌ Bağlantı Hatası: {e}")

    def get_product_stock_price(self, product_id):
        """
        Belirli bir ürünün ID'sine göre güncel stok ve fiyatını sorar.
        Chatbot için 'Canlı Kontrol' fonksiyonudur.
        """
        if not self.token:
            self.authenticate()

        # TEK ÜRÜN SORGUSU (Daha hızlı)
        query = """
        query GetProduct($id: String!) {
          product(id: $id) {
            id
            name
            variants {
              id
              sku
              prices {
                sellPrice
                discountPrice
              }
              stocks {
                stockCount
              }
              images {
                imageId
                isMain
              }
            }
          }
        }
        """

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(
                self.graphql_url,
                json={'query': query, 'variables': {'id': product_id}},
                headers=headers
            )

            data = response.json().get("data", {}).get("product", {})
            if not data:
                return None

            # Varyantlardaki toplam stoğu ve en iyi fiyatı hesapla
            total_stock = 0
            price = 0.0
            image_url = None

            variants = data.get("variants", [])
            if variants:
                # Fiyat (İlk varyant)
                p_obj = variants[0].get("prices", {})
                # discountPrice varsa onu al, yoksa sellPrice
                price = p_obj.get("discountPrice") or p_obj.get("sellPrice") or 0.0

                # Stok (Toplam) - stockCount kullanıyoruz!
                for v in variants:
                    stocks = v.get("stocks", [])
                    for s in stocks:
                        total_stock += s.get("stockCount", 0)

                # Resim Bulma
                for v in variants:
                    imgs = v.get("images", [])
                    if imgs:
                        # Varsa main, yoksa ilki
                        target_img = next((i for i in imgs if i.get("isMain")), imgs[0])
                        image_url = f"https://cdn.myikas.com/images/{target_img['imageId']}"
                        break

            return {
                "name": data["name"],
                "price": float(price),
                "stock": int(total_stock),
                "image_url": image_url
            }

        except Exception as e:
            print(f"Sorgu hatası: {e}")
            return None


# Test Bloğu
if __name__ == "__main__":
    client = IkasClient()
    # CSV'den aldığın bir ID ile test et (Örn: '3e578aba-860f-4a4f-bd26-2fd45a311d2f')
    # ID'yi CSV çıktısından veya önceki loglardan alabilirsin.
    # print(client.get_product_stock_price("URUN_ID_BURAYA"))
    print("✅ İstemci hazır. Chatbot canlı sorgu yapabilir.")