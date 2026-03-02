"""
LadySpecial ChatBot - XML Product Exporter

ikas platformundan XML export URL'i üzerinden ürün verilerini çeker ve
yapılandırılmış Python dict listesine dönüştürür.
"""

import os
import re
import xml.etree.ElementTree as ET

import httpx
from dotenv import load_dotenv

load_dotenv()


class ProductExporter:
    """ikas XML export verilerini çeker ve parse eder."""

    def __init__(self):
        self.export_url = os.getenv("PRODUCT_EXPORTER_URL", "").strip()
        if not self.export_url:
            print("⚠️ PRODUCT_EXPORTER_URL .env dosyasında tanımlı değil!")

    async def fetch_xml(self) -> str | None:
        """XML verisini URL'den çeker."""
        if not self.export_url:
            print("[Exporter] URL tanımlı değil, atlanıyor.")
            return None

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(self.export_url)
                if response.status_code == 200:
                    print(f"[Exporter] XML başarıyla çekildi ({len(response.text)} karakter)")
                    return response.text
                else:
                    print(f"[Exporter] XML çekme hatası: HTTP {response.status_code}")
                    return None
        except Exception as e:
            print(f"[Exporter] XML çekme bağlantı hatası: {e}")
            return None

    def parse_xml(self, xml_content: str) -> list[dict]:
        """
        XML verisini parse ederek ürün listesi döndürür.

        Returns:
            [
                {
                    "id": "uuid",
                    "name": "Ürün Adı",
                    "description": "Açıklama",
                    "slug": "urun-adi",
                    "brand": "Lady Special",
                    "categories": ["Dış Giyim > Ceket", ...],
                    "tags": ["Tunik", "Kadın Ceket"],
                    "variants": [
                        {
                            "id": "uuid",
                            "barcode": "12345",
                            "size": "L",
                            "color": "Kırmızı",
                            "stock": 3,
                            "sell_price": 799.0,
                            "discount_price": None,
                            "images": [
                                {"url": "https://...", "is_main": True, "order": 0, "type": "image"},
                                {"url": "https://...mp4", "is_main": False, "order": 1, "type": "video"},
                            ]
                        }, ...
                    ],
                    "total_stock": 10,
                    "min_price": 449.0,
                    "all_images": [...],
                    "main_image": "https://..."
                }, ...
            ]
        """
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as e:
            print(f"[Exporter] XML parse hatası: {e}")
            return []

        products = []

        for product_elem in root.findall("product"):
            try:
                product = self._parse_product(product_elem)
                if product:
                    products.append(product)
            except Exception as e:
                pid = product_elem.findtext("id", "?")
                print(f"[Exporter] Ürün parse hatası (ID: {pid}): {e}")

        print(f"[Exporter] {len(products)} ürün parse edildi.")
        return products

    def _parse_product(self, elem: ET.Element) -> dict | None:
        """Tek bir <product> elementini parse eder."""
        product_id = elem.findtext("id")
        name = elem.findtext("name")

        if not product_id or not name:
            return None

        # Açıklama (CDATA içerebilir, HTML temizliği yapılacak)
        description_raw = elem.findtext("description", "")
        description = self._clean_html(description_raw)

        # Slug
        meta = elem.find("metaData")
        slug = meta.findtext("slug", "") if meta is not None else ""

        # Marka
        brand_elem = elem.find("brand")
        brand = brand_elem.findtext("name", "") if brand_elem is not None else ""

        # Kategoriler
        categories = []
        cats_elem = elem.find("categories")
        if cats_elem is not None:
            for cat in cats_elem.findall("category"):
                cat_names = [n.text for n in cat.findall("name") if n.text]
                if cat_names:
                    categories.append(" > ".join(cat_names))

        # Etiketler
        tags = []
        tags_elem = elem.find("tags")
        if tags_elem is not None:
            for tag in tags_elem.findall("tag"):
                tag_name = tag.findtext("name")
                if tag_name:
                    tags.append(tag_name)

        # Varyantlar
        variants = []
        all_images = []
        seen_image_urls = set()
        total_stock = 0
        prices = []

        variants_elem = elem.find("variants")
        if variants_elem is not None:
            for var_elem in variants_elem.findall("variant"):
                variant = self._parse_variant(var_elem)
                if variant:
                    variants.append(variant)
                    total_stock += variant["stock"]
                    prices.append(variant["sell_price"])

                    # Tüm görselleri topla (tekrarları önle)
                    for img in variant["images"]:
                        if img["url"] not in seen_image_urls:
                            seen_image_urls.add(img["url"])
                            all_images.append(img)

        min_price = min(prices) if prices else 0

        # Ana görsel
        main_image = None
        for img in all_images:
            if img["is_main"] and img["type"] == "image":
                main_image = img["url"]
                break
        if not main_image and all_images:
            # İlk resmi main yap (video değilse)
            for img in all_images:
                if img["type"] == "image":
                    main_image = img["url"]
                    break

        return {
            "id": product_id,
            "name": name,
            "description": description,
            "slug": slug,
            "brand": brand,
            "categories": categories,
            "tags": tags,
            "variants": variants,
            "total_stock": total_stock,
            "min_price": min_price,
            "all_images": all_images,
            "main_image": main_image,
        }

    def _parse_variant(self, elem: ET.Element) -> dict | None:
        """Tek bir <variant> elementini parse eder."""
        variant_id = elem.findtext("id")
        if not variant_id:
            return None

        # Barkod
        barcode_elem = elem.find("barcodeList")
        barcode = ""
        if barcode_elem is not None:
            bc = barcode_elem.findtext("barcode", "")
            barcode = bc

        # Görseller
        images = []
        images_elem = elem.find("images")
        if images_elem is not None:
            for img_elem in images_elem.findall("image"):
                url = img_elem.findtext("imageUrl", "")
                if not url:
                    continue
                is_main = img_elem.findtext("isMain", "false").lower() == "true"
                order = int(img_elem.findtext("order", "0"))

                # URL'den medya tipini belirle
                media_type = "video" if url.endswith(".mp4") else "image"

                images.append({
                    "url": url,
                    "is_main": is_main,
                    "order": order,
                    "type": media_type,
                })

        # Fiyat
        sell_price = 0.0
        discount_price = None
        prices_elem = elem.find("prices")
        if prices_elem is not None:
            price_elem = prices_elem.find("price")
            if price_elem is not None:
                sell_price = float(price_elem.findtext("sellPrice", "0"))
                dp = price_elem.findtext("discountPrice")
                if dp:
                    try:
                        discount_price = float(dp)
                    except ValueError:
                        pass

        # Stok
        stock = 0
        stocks_elem = elem.find("stocks")
        if stocks_elem is not None:
            for stock_elem in stocks_elem.findall("stock"):
                stock += int(stock_elem.findtext("stockCount", "0"))

        # Varyant değerleri (beden, renk, vb.)
        size = ""
        color = ""
        variant_values_elem = elem.find("variantValues")
        if variant_values_elem is not None:
            for vv in variant_values_elem.findall("variantValue"):
                type_name = vv.findtext("variantTypeName", "").lower()
                value_name = vv.findtext("variantValueName", "")
                if "beden" in type_name or "boyut" in type_name or "ebat" in type_name:
                    size = value_name
                elif "renk" in type_name or "color" in type_name:
                    color = value_name

        return {
            "id": variant_id,
            "barcode": barcode,
            "size": size,
            "color": color,
            "stock": stock,
            "sell_price": sell_price,
            "discount_price": discount_price,
            "images": images,
        }

    @staticmethod
    def _clean_html(raw_html: str) -> str:
        """HTML etiketlerini temizler."""
        if not isinstance(raw_html, str):
            return ""
        clean = re.compile(r"<.*?>")
        text = re.sub(clean, "", raw_html)
        # Fazla boşlukları temizle
        text = re.sub(r"\s+", " ", text).strip()
        return text
