"""
LadySpecial ChatBot - Ürün Senkronizasyon Servisi

ikas XML Feed'inden ürünleri çeker, mevcut verilerle karşılaştırır (delta sync),
ve yalnızca değişiklikleri uygular:
- Stok/fiyat değişiklikleri → Qdrant payload güncelle (CLIP GEREKMEZ)
- Yeni ürünler → Görselleri indir, CLIP ile encode, Qdrant'a ekle
- Kaldırılan ürünler → Qdrant'tan sil, DB'den pasif yap
"""

import os
import re
import json
import asyncio
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
from io import BytesIO

import httpx
from PIL import Image
from dotenv import load_dotenv

from models.database import get_connection

load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = "ladyspecial_products"
WEBSITE_BASE_URL = "https://www.ladyspecial.com.tr"


def clean_html(raw_html: str) -> str:
    """HTML taglarını temizler."""
    if not raw_html:
        return ""
    return re.sub(r'<.*?>', '', raw_html).strip()


class ProductSync:
    """ikas XML → SQLite + Qdrant delta senkronizasyonu."""

    def __init__(self, image_service=None):
        self._image_service = image_service
        self._exporter_url = os.getenv("PRODUCT_EXPORTER_URL", "")
        self._is_syncing = False

    # ══════════════════════════════════════════
    #  ANA SYNC FONKSİYONU
    # ══════════════════════════════════════════

    async def sync(self) -> dict:
        """
        Tam delta sync döngüsü çalıştırır.
        Returns: {"new": N, "updated": N, "removed": N, "images_queued": N}
        """
        if self._is_syncing:
            print("[Sync] Zaten çalışıyor, atlanıyor.")
            return {"skipped": True}

        self._is_syncing = True
        stats = {"new": 0, "updated": 0, "removed": 0, "images_queued": 0}

        try:
            print(f"\n[Sync] ══════ Senkronizasyon başlıyor ══════")

            # 1. XML'i çek ve parse et
            xml_products = await self._fetch_and_parse_xml()
            if xml_products is None:
                print("[Sync] XML alınamadı, atlanıyor.")
                return stats

            print(f"[Sync] XML'den {len(xml_products)} ürün parse edildi.")

            # 2. Mevcut ürünleri DB'den al
            db_products = self._get_db_products()
            xml_ids = set(xml_products.keys())
            db_ids = set(db_products.keys())

            # 3. Delta hesapla
            new_ids = xml_ids - db_ids
            removed_ids = db_ids - xml_ids
            existing_ids = xml_ids & db_ids

            # 4. Yeni ürünleri ekle
            for pid in new_ids:
                self._insert_product(xml_products[pid])
                stats["new"] += 1

            # 5. Mevcut ürünleri güncelle (stok/fiyat)
            for pid in existing_ids:
                changed = self._update_product_if_changed(
                    db_products[pid], xml_products[pid]
                )
                if changed:
                    stats["updated"] += 1

            # 6. Kaldırılan ürünleri pasif yap
            for pid in removed_ids:
                self._deactivate_product(pid)
                stats["removed"] += 1

            # 7. Qdrant'taki stok/fiyat payload'larını güncelle
            if stats["updated"] > 0 or stats["removed"] > 0:
                self._update_qdrant_payloads()

            # 8. Yeni ürünlerin görsellerini indexleme kuyruğuna ekle
            images_queued = self._queue_new_images()
            stats["images_queued"] = images_queued

            # 9. İndexlenmemiş görselleri işle (tümü bitene kadar)
            if images_queued > 0:
                print(f"[Sync] Toplam {images_queued} indexlenmemiş görsel bulundu, indexlemeye başlanıyor...", flush=True)
                while True:
                    indexed = await self._index_pending_images(batch_size=20)
                    if not indexed or indexed == 0:
                        break
                    print(f"[Sync] {indexed} görsel indexlendi, devam ediliyor...", flush=True)

            print(f"[Sync] ══════ Tamamlandı: "
                  f"+{stats['new']} yeni, "
                  f"~{stats['updated']} güncelleme, "
                  f"-{stats['removed']} kaldırma, "
                  f"📷{stats['images_queued']} görsel ══════\n")

        except Exception as e:
            print(f"[Sync] HATA: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._is_syncing = False

        return stats

    # ══════════════════════════════════════════
    #  XML FETCH & PARSE
    # ══════════════════════════════════════════

    async def _fetch_and_parse_xml(self) -> dict | None:
        """XML feed'ini çeker ve product dict'e parse eder."""
        if not self._exporter_url:
            print("[Sync] PRODUCT_EXPORTER_URL tanımlı değil!")
            return None

        try:
            async with httpx.AsyncClient() as client:
                print(f"[Sync] XML indiriliyor...")
                resp = await client.get(self._exporter_url, timeout=60)
                if resp.status_code != 200:
                    print(f"[Sync] XML HTTP hatası: {resp.status_code}")
                    return None

            root = ET.fromstring(resp.content)
            products = {}

            for product_elem in root.findall("product"):
                p = self._parse_product_element(product_elem)
                if p:
                    products[p["id"]] = p

            return products

        except Exception as e:
            print(f"[Sync] XML fetch/parse hatası: {e}")
            return None

    def _parse_product_element(self, elem) -> dict | None:
        """Tek bir <product> XML elementini dict'e çevirir."""
        try:
            pid = elem.findtext("id", "").strip()
            if not pid:
                return None

            name = elem.findtext("name", "").strip()
            description = clean_html(elem.findtext("description", ""))

            # Slug
            meta = elem.find("metaData")
            slug = meta.findtext("slug", "") if meta is not None else ""

            # Kategori
            categories = []
            for cat in elem.findall("categories/category"):
                names = [n.text for n in cat.findall("name") if n.text]
                if names:
                    categories.append(" > ".join(names))
            category = " | ".join(categories)

            # Varyantları işle
            variants = []
            all_images = []
            seen_images = set()
            skus_set = set()
            total_stock = 0
            min_price = float('inf')

            for var_elem in elem.findall("variants/variant"):
                vid = var_elem.findtext("id", "")
                sku = var_elem.findtext("sku", "")
                if sku:
                    skus_set.add(sku)

                # Stok
                stock = 0
                for s in var_elem.findall("stocks/stock"):
                    stock += int(s.findtext("stockCount", "0") or 0)
                total_stock += stock

                # Fiyat
                price = 0
                for p in var_elem.findall("prices/price"):
                    price = float(p.findtext("sellPrice", "0") or 0)
                if price > 0 and price < min_price:
                    min_price = price

                # Varyant değerleri
                var_values = {}
                for vv in var_elem.findall("variantValues/variantValue"):
                    vtype = vv.findtext("variantTypeName", "")
                    vname = vv.findtext("variantValueName", "")
                    if vtype and vname:
                        var_values[vtype] = vname

                variants.append({
                    "id": vid,
                    "stock": stock,
                    "price": price,
                    "size": var_values.get("Boyut/Ebat", var_values.get("Beden", "")),
                    "color": var_values.get("Renk", ""),
                })

                # Görseller (bu varyanttan)
                for img_elem in var_elem.findall("images/image"):
                    url = img_elem.findtext("imageUrl", "").strip()
                    if url and url not in seen_images:
                        seen_images.add(url)
                        is_main = img_elem.findtext("isMain", "false") == "true"
                        order = int(img_elem.findtext("order", "0") or 0)

                        # .mp4 → video, diğer → image
                        media_type = "video" if url.endswith(".mp4") else "image"

                        all_images.append({
                            "url": url,
                            "is_main": is_main,
                            "order": order,
                            "media_type": media_type,
                        })

            if min_price == float('inf'):
                min_price = 0

            # Görselleri sırala
            all_images.sort(key=lambda x: (not x["is_main"], x["order"]))

            return {
                "id": pid,
                "name": name,
                "slug": slug,
                "description": description[:1000],
                "category": category,
                "price": min_price,
                "currency": "TRY",
                "total_stock": total_stock,
                "image_url": all_images[0]["url"] if all_images else "",
                "all_images": all_images,
                "variants": variants,
                "skus": ",".join(sorted(list(skus_set)))
            }

        except Exception as e:
            print(f"[Sync] Ürün parse hatası: {e}")
            return None

    # ══════════════════════════════════════════
    #  DATABASE İŞLEMLERİ
    # ══════════════════════════════════════════

    def _get_db_products(self) -> dict:
        """Mevcut ürünleri DB'den dict olarak döndürür."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM products WHERE is_active = 1"
        ).fetchall()
        conn.close()
        return {row["id"]: dict(row) for row in rows}

    def _insert_product(self, p: dict):
        """Yeni ürünü DB'ye ekler."""
        conn = get_connection()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO products
                (id, name, slug, description, category, price, currency,
                 total_stock, image_url, all_image_urls, variants_json,
                 skus, is_active, last_synced)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """, (
                p["id"], p["name"], p["slug"], p["description"],
                p["category"], p["price"], p["currency"],
                p["total_stock"], p["image_url"],
                json.dumps([img["url"] for img in p["all_images"]]),
                json.dumps(p["variants"], ensure_ascii=False),
                p["skus"],
                datetime.utcnow().isoformat()
            ))

            # Görselleri ekle
            for img in p["all_images"]:
                if img["media_type"] == "image":  # Şimdilik sadece görseller
                    conn.execute("""
                        INSERT OR IGNORE INTO product_images
                        (product_id, image_url, is_indexed, is_main, sort_order, media_type)
                        VALUES (?, ?, 0, ?, ?, ?)
                    """, (
                        p["id"], img["url"],
                        1 if img["is_main"] else 0,
                        img["order"], img["media_type"]
                    ))

            conn.commit()
        except Exception as e:
            print(f"[Sync] Insert hatası ({p['name']}): {e}")
        finally:
            conn.close()

    def _update_product_if_changed(self, db_row: dict, xml_p: dict) -> bool:
        """Stok veya fiyat değiştiyse günceller. True dönerse değişmiş demektir."""
        changed = False

        if db_row["total_stock"] != xml_p["total_stock"]:
            changed = True
        if abs((db_row["price"] or 0) - xml_p["price"]) > 0.01:
            changed = True
        if db_row["name"] != xml_p["name"]:
            changed = True
        if db_row.get("skus", "") != xml_p.get("skus", ""):
            changed = True

        if not changed:
            return False

        conn = get_connection()
        try:
            conn.execute("""
                UPDATE products SET
                    name = ?, price = ?, total_stock = ?,
                    variants_json = ?, skus = ?, last_synced = ?,
                    image_url = ?, all_image_urls = ?
                WHERE id = ?
            """, (
                xml_p["name"], xml_p["price"], xml_p["total_stock"],
                json.dumps(xml_p["variants"], ensure_ascii=False),
                xml_p["skus"],
                datetime.utcnow().isoformat(),
                xml_p["image_url"],
                json.dumps([img["url"] for img in xml_p["all_images"]]),
                xml_p["id"]
            ))

            # Yeni görseller varsa ekle
            for img in xml_p["all_images"]:
                if img["media_type"] == "image":
                    conn.execute("""
                        INSERT OR IGNORE INTO product_images
                        (product_id, image_url, is_indexed, is_main, sort_order, media_type)
                        VALUES (?, ?, 0, ?, ?, ?)
                    """, (
                        xml_p["id"], img["url"],
                        1 if img["is_main"] else 0,
                        img["order"], img["media_type"]
                    ))

            conn.commit()

            if db_row["total_stock"] != xml_p["total_stock"]:
                print(f"[Sync] Stok: {xml_p['name']}: {db_row['total_stock']} → {xml_p['total_stock']}")
            if abs((db_row["price"] or 0) - xml_p["price"]) > 0.01:
                print(f"[Sync] Fiyat: {xml_p['name']}: {db_row['price']} → {xml_p['price']}")

        finally:
            conn.close()

        return True

    def _deactivate_product(self, product_id: str):
        """Ürünü pasif yapar (silmez)."""
        conn = get_connection()
        conn.execute("UPDATE products SET is_active = 0, last_synced = ? WHERE id = ?",
                      (datetime.utcnow().isoformat(), product_id))
        conn.commit()
        conn.close()
        print(f"[Sync] Ürün pasif yapıldı: {product_id}")

    # ══════════════════════════════════════════
    #  QDRANT PAYLOAD GÜNCELLEME (CLIP'SIZ!)
    # ══════════════════════════════════════════

    def _update_qdrant_payloads(self):
        """Qdrant'taki tüm noktaların stok/fiyat payload'larını günceller.
        Bu işlem CLIP modeline DOKUNMAZ — sadece metadata günceller."""
        try:
            from qdrant_client import QdrantClient
            qdrant = QdrantClient(url=QDRANT_URL)

            conn = get_connection()
            products = conn.execute(
                "SELECT id, name, price, total_stock FROM products WHERE is_active = 1"
            ).fetchall()
            conn.close()

            product_map = {row["id"]: dict(row) for row in products}

            # Qdrant'tan tüm noktaları al ve güncelle
            offset = None
            updated = 0
            while True:
                result = qdrant.scroll(
                    collection_name=COLLECTION_NAME,
                    limit=100,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                points, offset = result

                if not points:
                    break

                for point in points:
                    pid = point.payload.get("id", "")
                    if pid in product_map:
                        db_p = product_map[pid]
                        if (point.payload.get("stock") != db_p["total_stock"] or
                                abs(point.payload.get("price", 0) - db_p["price"]) > 0.01):
                            qdrant.set_payload(
                                collection_name=COLLECTION_NAME,
                                payload={
                                    "stock": db_p["total_stock"],
                                    "price": db_p["price"],
                                    "name": db_p["name"],
                                },
                                points=[point.id],
                            )
                            updated += 1

                if offset is None:
                    break

            if updated > 0:
                print(f"[Sync] Qdrant: {updated} nokta payload güncellendi (CLIP'siz).")

        except Exception as e:
            print(f"[Sync] Qdrant payload güncelleme hatası: {e}")

    # ══════════════════════════════════════════
    #  YENİ GÖRSEL İNDEXLEME (CLIP)
    # ══════════════════════════════════════════

    def _queue_new_images(self) -> int:
        """İndexlenmemiş görsellerin sayısını döndürür."""
        conn = get_connection()
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM product_images WHERE is_indexed = 0 AND media_type = 'image'"
        ).fetchone()["cnt"]
        conn.close()
        return count

    async def _index_pending_images(self, batch_size: int = 20):
        """İndexlenmemiş görselleri batch halinde CLIP ile encode edip Qdrant'a ekler."""
        if not self._image_service:
            print("[Sync] ImageService yok, görsel indexleme atlanıyor.")
            return 0

        indexed_count = 0
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.http import models
            qdrant = QdrantClient(url=QDRANT_URL)

            conn = get_connection()
            pending = conn.execute("""
                SELECT pi.id, pi.product_id, pi.image_url, pi.is_main,
                       p.name, p.price, p.total_stock, p.description, p.slug
                FROM product_images pi
                JOIN products p ON p.id = pi.product_id
                WHERE pi.is_indexed = 0 AND pi.media_type = 'image'
                ORDER BY pi.is_main DESC
                LIMIT ?
            """, (batch_size,)).fetchall()
            conn.close()

            if not pending:
                return 0

            print(f"[Sync] Bu partide {len(pending)} görsel indexlenecek...", flush=True)

            clip_model = await asyncio.to_thread(self._image_service._get_clip_model)

            async with httpx.AsyncClient() as client:
                for row in pending:
                    try:
                        # Görseli indir
                        resp = await client.get(row["image_url"], timeout=10)
                        if resp.status_code != 200:
                            continue

                        img = Image.open(BytesIO(resp.content))
                        if img.mode != "RGB":
                            img = img.convert("RGB")

                        # CLIP embedding (Asenkron loop'u bloklamamak için thread kullan)
                        embedding = await asyncio.to_thread(clip_model.encode, img)

                        # Benzersiz Qdrant point ID (Deterministic - always the same)
                        point_id_str = f"{row['product_id']}_{row['image_url']}"
                        point_id = int(hashlib.md5(point_id_str.encode("utf-8")).hexdigest()[:15], 16)

                        slug = row["slug"] or ""
                        url = f"{WEBSITE_BASE_URL}/{slug}" if slug else WEBSITE_BASE_URL

                        # Qdrant'a upsert
                        qdrant.upsert(
                            collection_name=COLLECTION_NAME,
                            points=[models.PointStruct(
                                id=point_id,
                                vector=embedding.tolist(),
                                payload={
                                    "id": row["product_id"],
                                    "name": row["name"],
                                    "price": row["price"],
                                    "stock": row["total_stock"],
                                    "image_url": row["image_url"],
                                    "description": (row["description"] or "")[:200],
                                    "is_main": row["is_main"] == 1,
                                }
                            )]
                        )

                        # DB'de indexlenmiş olarak işaretle
                        conn2 = get_connection()
                        conn2.execute(
                            "UPDATE product_images SET is_indexed = 1, qdrant_point_id = ? WHERE id = ?",
                            (point_id, row["id"])
                        )
                        conn2.commit()
                        conn2.close()

                        print(f"[Sync] ✅ Indexed: {row['name']} ({row['image_url'][-30:]})", flush=True)
                        indexed_count += 1

                        # Her görsel arası küçük bekleme (CPU nefes alsın)
                        await asyncio.sleep(0.05)

                    except Exception as e:
                        print(f"[Sync] Görsel index hatası: {e}")
                        continue

        except Exception as e:
            print(f"[Sync] Batch index hatası: {e}")
            import traceback
            traceback.print_exc()
        
        return indexed_count

    # ══════════════════════════════════════════
    #  HELPER
    # ══════════════════════════════════════════

    def get_product_count(self) -> int:
        """Aktif ürün sayısını döndürür."""
        conn = get_connection()
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM products WHERE is_active = 1"
        ).fetchone()["cnt"]
        conn.close()
        return count

    def get_indexed_image_count(self) -> int:
        """İndexlenmiş görsel sayısını döndürür."""
        conn = get_connection()
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM product_images WHERE is_indexed = 1"
        ).fetchone()["cnt"]
        conn.close()
        return count
