import json
from difflib import SequenceMatcher
from models.database import get_connection
import re

WEBSITE_BASE_URL = "https://www.ladyspecial.com.tr"

class ProductService:
    """Ürün arama ve bilgi sorgulama servisi."""

    def __init__(self):
        count = self.get_product_count()
        print(f"📦 {count} ürün veritabanında mevcut.")

    def search_products(self, query: str, max_results: int = 5) -> list[dict]:
        """Ürün adı ve açıklamasında arama yapar."""
        query_lower = query.lower().strip()
        if not query_lower:
            return []

        conn = get_connection()
        rows = conn.execute("SELECT * FROM products WHERE is_active = 1").fetchall()
        conn.close()

        scored_results = []
        for row in rows:
            product = dict(row)
            name = (product.get("name") or "").lower()
            description = (product.get("description") or "").lower()
            category = (product.get("category") or "").lower()
            
            # SKU check in fallback (if user searches exact SKU)
            skus_str = (product.get("skus") or "").lower()
            if skus_str and query_lower in skus_str:
                scored_results.append((5.0, product))
                continue

            score = 0.0
            if query_lower in name: score += 2.0
            if query_lower in description: score += 1.0
            if query_lower in category: score += 0.5

            name_similarity = SequenceMatcher(None, query_lower, name).ratio()
            if name_similarity > 0.4: score += name_similarity

            for word in query_lower.split():
                if len(word) >= 3 and word in name:
                    score += 0.5

            if score > 0:
                scored_results.append((score, product))

        scored_results.sort(key=lambda x: x[0], reverse=True)

        results = []
        for _, product in scored_results[:max_results]:
            results.append(self._format_product(product))
        return results

    def extract_skus_from_text(self, text: str) -> list[str]:
        """Metin içindeki bilinen SKU'ları bulur."""
        conn = get_connection()
        rows = conn.execute("SELECT skus FROM products WHERE is_active = 1").fetchall()
        conn.close()
        
        all_skus = set()
        for row in rows:
            if row["skus"]:
                for s in row["skus"].split(","):
                    all_skus.add(s.strip())
                    
        # Match using word boundaries to ensure we don't match substrings (e.g. Y1 inside MY12)
        found_skus = []
        for sku in all_skus:
            if not sku:
                continue
            # Escape regex characters in SKU just in case
            pattern = r'\b' + re.escape(sku) + r'\b'
            if re.search(pattern, text, re.IGNORECASE):
                found_skus.append(sku)
        return list(set(found_skus))

    def get_products_by_skus(self, skus: list[str]) -> list[dict]:
        """Verilen SKU listesiyle eşleşen ürünleri getirir."""
        if not skus:
            return []
        conn = get_connection()
        rows = conn.execute("SELECT * FROM products WHERE is_active = 1").fetchall()
        conn.close()
        
        products = []
        for row in rows:
            if not row["skus"]:
                continue
            row_skus = [s.strip() for s in row["skus"].split(",")]
            if any(s in row_skus for s in skus):
                products.append(self._format_product(dict(row)))
        return products

    def get_product_by_id(self, product_id: str) -> dict | None:
        conn = get_connection()
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        conn.close()
        if row: return self._format_product(dict(row))
        return None

    def get_product_by_slug(self, slug: str) -> dict | None:
        conn = get_connection()
        row = conn.execute("SELECT * FROM products WHERE slug = ? AND is_active = 1", (slug,)).fetchone()
        conn.close()
        if row: return self._format_product(dict(row))
        return None

    def _format_product(self, product: dict) -> dict:
        slug = product.get("slug", "")
        url = f"{WEBSITE_BASE_URL}/{slug}" if slug else WEBSITE_BASE_URL
        variants = []
        try:
            vj = product.get("variants_json", "[]")
            if vj: variants = json.loads(vj)
        except Exception: pass

        in_stock_variants = [v for v in variants if v.get("stock", 0) > 0]
        return {
            "id": product.get("id", ""),
            "name": product.get("name", "Bilinmeyen Ürün"),
            "description": (product.get("description") or "")[:300],
            "price": product.get("price", 0),
            "currency": product.get("currency", "TRY"),
            "stock": product.get("total_stock", 0),
            "image_url": product.get("image_url"),
            "url": url,
            "category": product.get("category", ""),
            "skus": product.get("skus", ""),
            "in_stock_variants": in_stock_variants,
        }

    def get_product_count(self) -> int:
        conn = get_connection()
        count = conn.execute("SELECT COUNT(*) as cnt FROM products WHERE is_active = 1").fetchone()["cnt"]
        conn.close()
        return count

    def get_slug_by_id(self, product_id: str) -> str:
        conn = get_connection()
        row = conn.execute("SELECT slug FROM products WHERE id = ?", (product_id,)).fetchone()
        conn.close()
        return row["slug"] if row else ""
