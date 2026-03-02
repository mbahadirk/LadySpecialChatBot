"""
LadySpecial ChatBot - Görsel Arama Servisi

CLIP modeli ile görselleri vektöre çevirir ve Qdrant'ta benzer ürünleri arar.
Görselleri yerel dosya sistemine kaydeder.
"""

import os
import json
import time
from io import BytesIO
from PIL import Image
from dotenv import load_dotenv

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = "ladyspecial_products"
UPLOADS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
WEBSITE_BASE_URL = "https://www.ladyspecial.com.tr"


class ImageService:
    """Görsel arama ve saklama servisi."""

    def __init__(self):
        self._clip_model = None  # Lazy load (RAM tasarrufu)
        self._qdrant = None
        self._product_db: list[dict] = []
        self._product_db_loaded = False

        # Uploads klasörünü oluştur
        os.makedirs(UPLOADS_DIR, exist_ok=True)

    # ──────────────────────────────────────
    #  CLIP Model
    # ──────────────────────────────────────

    def _get_clip_model(self):
        """CLIP modelini lazy-load eder."""
        if self._clip_model is None:
            print("[ImageService] CLIP modeli yukleniyor (ilk seferlik)...")
            from sentence_transformers import SentenceTransformer
            self._clip_model = SentenceTransformer('sentence-transformers/clip-ViT-L-14')
            print("[ImageService] CLIP modeli hazir.")
        return self._clip_model

    def _get_qdrant(self):
        """Qdrant istemcisini lazy-load eder."""
        if self._qdrant is None:
            from qdrant_client import QdrantClient
            self._qdrant = QdrantClient(url=QDRANT_URL)
        return self._qdrant

    # ──────────────────────────────────────
    #  Görsel Kaydetme
    # ──────────────────────────────────────

    def save_image(self, user_id: int, image_bytes: bytes) -> str:
        """
        Görseli yerel dosya sistemine kaydeder.

        Args:
            user_id: Veritabanı kullanıcı ID'si
            image_bytes: Görsel dosya içeriği

        Returns:
            Kaydedilen dosyanın yolu
        """
        user_dir = os.path.join(UPLOADS_DIR, str(user_id))
        os.makedirs(user_dir, exist_ok=True)

        filename = f"{int(time.time() * 1000)}.jpg"
        filepath = os.path.join(user_dir, filename)

        # JPEG olarak kaydet
        image = Image.open(BytesIO(image_bytes))
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.save(filepath, "JPEG", quality=85)

        print(f"[ImageService] Gorsel kaydedildi: {filepath}")
        return filepath

    # ──────────────────────────────────────
    #  Görsel Arama (CLIP + Qdrant)
    # ──────────────────────────────────────

    def search_by_image(self, image_path: str, max_results: int = 5) -> list[dict]:
        """
        Görseli CLIP ile vektöre çevirip Qdrant'ta benzer ürünleri arar.

        Args:
            image_path: Yerel görsel dosya yolu
            max_results: Döndürülecek maksimum sonuç

        Returns:
            [{"name": "...", "price": ..., "stock": ..., "in_stock": bool, "url": "...", "score": ...}, ...]
        """
        try:
            # Görseli yükle
            image = Image.open(image_path)
            if image.mode != "RGB":
                image = image.convert("RGB")

            # CLIP ile vektöre çevir
            print("[ImageService] Gorsel CLIP ile vektore cevriliyor...")
            model = self._get_clip_model()
            embedding = model.encode(image)

            # Qdrant'ta ara
            print(f"[ImageService] Qdrant'ta aranıyor (koleksiyon: {COLLECTION_NAME})...")
            qdrant = self._get_qdrant()
            search_result = qdrant.query_points(
                collection_name=COLLECTION_NAME,
                query=embedding.tolist(),
                limit=50  # Fazla çek, sonra filtrele
            ).points

            print(f"[ImageService] {len(search_result)} ham sonuc bulundu.")

            # Filtrele ve tekilleştir
            unique_products = []
            seen_names = set()

            for hit in search_result:
                payload = hit.payload
                name = payload.get("name", "")

                if name in seen_names:
                    continue
                seen_names.add(name)

                product_id = payload.get("id", "")

                # Stok ve slug bilgisini DB'den al (daha güncel)
                db_info = self._get_product_info_from_db(product_id)
                if db_info:
                    stock = db_info["stock"]
                    slug = db_info["slug"] or ""
                else:
                    stock = int(payload.get("stock", 0))
                    slug = payload.get("slug", "")
                    print(f"[ImageService] ⚠️ DB'de bulunamadı: {product_id} ({name})")

                url = f"{WEBSITE_BASE_URL}/{slug}" if slug else WEBSITE_BASE_URL

                unique_products.append({
                    "id": product_id,
                    "name": name,
                    "price": payload.get("price", 0),
                    "stock": stock,
                    "in_stock": stock > 0,
                    "image_url": payload.get("image_url", ""),
                    "url": url,
                    "score": round(hit.score, 4),
                    "description": payload.get("description", "")[:200],
                })

                if len(unique_products) >= max_results:
                    break

            in_stock = [p for p in unique_products if p["in_stock"]]
            out_of_stock = [p for p in unique_products if not p["in_stock"]]
            print(f"[ImageService] Sonuc: {len(in_stock)} stokta, {len(out_of_stock)} tukenmis.")

            return unique_products

        except Exception as e:
            print(f"[ImageService] Gorsel arama hatasi: {e}")
            import traceback
            traceback.print_exc()
            return []

    # ──────────────────────────────────────
    #  Yardımcı
    # ──────────────────────────────────────

    def _get_product_info_from_db(self, product_id: str) -> dict | None:
        """Ürünün stok ve slug bilgisini SQLite'tan alır."""
        try:
            from models.database import get_connection
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT slug, total_stock as stock FROM products WHERE id = ? AND is_active = 1",
                (product_id,)
            )
            row = cursor.fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception:
            return None

    def format_results_for_llm(self, results: list[dict]) -> str:
        """Arama sonuçlarını LLM'in okuyabileceği formata çevirir."""
        if not results:
            return "Gorsel arama sonucu: Hicbir urun bulunamadi."

        lines = ["Gorsel Arama Sonuclari:"]
        for i, p in enumerate(results, 1):
            status = "STOKTA" if p["in_stock"] else "TUKENMIS"
            line = f"{i}. {p['name']} -- {p['price']} TL -- {status}"
            if p["in_stock"]:
                line += f" -- URL: {p['url']}"
            lines.append(line)

        return "\n".join(lines)

