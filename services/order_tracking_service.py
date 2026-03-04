"""
LadySpecial ChatBot - Sipariş Takip Servisi

Müşterilerin sipariş durumlarını sorgulamasını sağlar.
- Chatbot üzerinden oluşturulmuş siparişleri kontrol eder (local DB)
- ikas CSV verisinden geçmiş siparişleri arar
- Telefon, e-posta veya sipariş numarası ile arama yapar
"""

import csv
import os
import re
from datetime import datetime
from models.database import get_connection

# CSV dosyası yolu
CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ikas-siparisler.csv")

# Sipariş durumu Türkçe eşlemeleri
STATUS_MAP = {
    "Oluşturuldu": "Siparişiniz oluşturuldu, henüz işleme alınmadı.",
    "İletildi": "Siparişiniz hazırlanmak üzere işleme alındı.",
    "Gönderildi": "Siparişiniz kargoya verildi! 📦",
    "İptal Edildi": "Siparişiniz iptal edilmiş.",
    "İade Edildi": "Siparişiniz iade edilmiş.",
    "Parçalı İade": "Siparişinizin bir kısmı iade edilmiş.",
}

ORDER_TRACKING_URL = "https://ladyspecial.com.tr/pages/order-tracking"


class OrderTrackingService:
    """Sipariş takip ve sorgulama servisi."""

    def __init__(self):
        self._csv_orders = None  # Lazy-load cache
        self._csv_loaded_at = None
        self._webhook_cache = {}  # order_number -> order dict (anlık güncellemeler için)

    # ─── CSV'den Sipariş Yükleme ───

    def _load_csv_orders(self) -> list[dict]:
        """ikas CSV dosyasını okur (Değişiklik varsa otomatik yeniden yükler)."""
        if not os.path.exists(CSV_PATH):
            print(f"[OrderTracking] ⚠️ CSV bulunamadı: {CSV_PATH}")
            self._csv_orders = []
            return self._csv_orders

        current_mtime = os.path.getmtime(CSV_PATH)

        if self._csv_orders is not None and self._csv_loaded_at == current_mtime:
            return self._csv_orders

        self._csv_loaded_at = current_mtime
        orders = {}  # order_number -> order dict (aynı siparişteki birden fazla satır)
        try:
            with open(CSV_PATH, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    order_num = row.get("Sipariş Numarası", "").strip()
                    if not order_num:
                        continue

                    if order_num not in orders:
                        orders[order_num] = {
                            "order_number": order_num,
                            "customer_name": row.get("Müşteri Tam Adı", "").strip(),
                            "email": row.get("E-posta", "").strip(),
                            "phone": row.get("Kargo Adresi Telefon Numarası", "").strip(),
                            "billing_phone": row.get("Fatura Adresi Telefon Numarası", "").strip(),
                            "order_date": row.get("Sipariş Tarihi", "").strip(),
                            "order_status": row.get("Sipariş Durumu", "").strip(),
                            "payment_status": row.get("Sipariş Ödeme Durumu", "").strip(),
                            "payment_method": row.get("Ödeme Yöntemi", "").strip(),
                            "subtotal": row.get("Ara Toplam", "").strip(),
                            "shipping_cost": row.get("Kargo Fiyatı", "").strip(),
                            "grand_total": row.get("Toplam", "").strip(),
                            "shipping_type": row.get("Kargo Türü", "").strip(),
                            "shipping_city": row.get("Kargo Adresi Şehir", "").strip(),
                            "items": [],
                        }

                    # Ürün bilgisi ekle
                    product_name = row.get("Ürün Adı", "").strip()
                    if product_name:
                        variant_color = row.get("Varyant Değeri 1", "").strip()
                        variant_size = row.get("Varyant Değeri 2", "").strip()
                        variant_info = ""
                        if variant_color and variant_size:
                            variant_info = f"{variant_color} / {variant_size}"
                        elif variant_color:
                            variant_info = variant_color
                        elif variant_size:
                            variant_info = variant_size

                        orders[order_num]["items"].append({
                            "product_name": product_name,
                            "variant_info": variant_info,
                            "price": row.get("Ürün Satış Fiyatı", "").strip(),
                            "product_status": row.get("Ürün Durumu", "").strip(),
                        })

            self._csv_orders = list(orders.values())
            self._csv_loaded_at = datetime.utcnow()
            print(f"[OrderTracking] ✅ CSV'den {len(self._csv_orders)} sipariş yüklendi.")

        except Exception as e:
            print(f"[OrderTracking] ❌ CSV okuma hatası: {e}")
            import traceback
            traceback.print_exc()
            self._csv_orders = []

        return self._csv_orders

    def reload_csv(self):
        """CSV cache'ini temizler ve yeniden yükler."""
        self._csv_orders = None
        self._load_csv_orders()

    # ─── Webhook Canlı Güncelleme ───

    def update_order_from_webhook(self, webhook_data: dict):
        """Ikas'tan gelen webhook verisini hafızaya(cache) kaydeder."""
        order_num = str(webhook_data.get("order_number", "")).strip()
        if not order_num:
            return

        order = {
            "source": "ikas_webhook",
            "order_number": order_num,
            "customer_name": webhook_data.get("customer_name", ""),
            "email": webhook_data.get("email", ""),
            "phone": webhook_data.get("phone", ""),
            "order_date": webhook_data.get("order_date", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            "order_status": webhook_data.get("order_status", ""),
            "payment_method": webhook_data.get("payment_method", ""),
            "grand_total": str(webhook_data.get("grand_total", "0")),
            "items": webhook_data.get("items", []),
        }

        self._webhook_cache[order_num] = order
        print(f"[OrderTracking] Webhook cache güncellendi: #{order_num} -> {order['order_status']}")

    # ─── Yerel DB'den Sipariş Arama ───

    def find_orders_by_user_id(self, user_id: int) -> list[dict]:
        """Chatbot üzerinden oluşturulmuş siparişleri DB'den arar."""
        try:
            conn = get_connection()
            cursor = conn.cursor()
            rows = cursor.execute(
                """SELECT o.id, o.customer_name, o.customer_phone, o.customer_email,
                          o.customer_address, o.total_price, o.shipping_cost, o.grand_total,
                          o.payment_method, o.status, o.created_at
                   FROM orders o
                   WHERE o.user_id = ?
                   ORDER BY o.created_at DESC
                   LIMIT 5""",
                (user_id,)
            ).fetchall()

            orders = []
            for row in rows:
                order_id = row["id"]
                items = cursor.execute(
                    """SELECT product_name, variant_info, quantity, unit_price
                       FROM order_items WHERE order_id = ?""",
                    (order_id,)
                ).fetchall()

                orders.append({
                    "source": "chatbot",
                    "order_number": str(order_id),
                    "customer_name": row["customer_name"] or "",
                    "phone": row["customer_phone"] or "",
                    "email": row["customer_email"] or "",
                    "order_status": self._map_local_status(row["status"] or "pending"),
                    "payment_method": row["payment_method"] or "",
                    "grand_total": row["grand_total"] or 0,
                    "order_date": row["created_at"] or "",
                    "items": [
                        {
                            "product_name": item["product_name"],
                            "variant_info": item["variant_info"] or "",
                            "price": str(item["unit_price"]),
                            "quantity": item["quantity"],
                        }
                        for item in items
                    ],
                })

            conn.close()
            return orders

        except Exception as e:
            print(f"[OrderTracking] ❌ DB sipariş arama hatası: {e}")
            return []

    # ─── CSV'den Sipariş Arama ───

    def find_orders_by_phone(self, phone: str) -> list[dict]:
        """Telefon numarasına göre sipariş arar (Webhook -> CSV)."""
        phone = self._normalize_phone(phone)
        if not phone:
            return []

        results = []
        found_nums = set()

        # İlk önce webhook cache'e bak
        for order_num, order in self._webhook_cache.items():
            order_phone = self._normalize_phone(order.get("phone", ""))
            # Eğer girilen numara en az 7 haneliyse ve sistemdeki numara bu girilen kısımla bitiyorsa eşleştir (Başı eksik yazılmış olabilir)
            if len(phone) >= 7 and order_phone.endswith(phone):
                results.append(order)
                found_nums.add(order_num)
            elif phone == order_phone:
                results.append(order)
                found_nums.add(order_num)

        orders = self._load_csv_orders()
        for order in orders:
            # Eğer numara cache'te zaten bulunduysa, eski CSV versiyonunu ekleme
            if order.get("order_number") in found_nums:
                continue

            order_phone = self._normalize_phone(order.get("phone", ""))
            billing_phone = self._normalize_phone(order.get("billing_phone", ""))
            
            # Kısmi eşleşme (endswith) kontrolü: En az 7 haneli girilmişse numaraların sonu eşleşiyor mu diye bak
            is_match = False
            if len(phone) >= 7:
                is_match = order_phone.endswith(phone) or billing_phone.endswith(phone)
            else:
                is_match = phone in (order_phone, billing_phone)
                
            if is_match:
                results.append({**order, "source": "ikas"})

        # En güncel siparişler önce gelsin
        results.sort(key=lambda x: x.get("order_date", ""), reverse=True)
        return results

    def find_orders_by_email(self, email: str) -> list[dict]:
        """E-posta adresine göre sipariş arar (Kullanım dışı bırakıldı)."""
        return []

    def find_order_by_number(self, order_number: str) -> dict | None:
        """Sipariş numarasına göre arar (Webhook -> DB -> CSV)."""
        order_number = order_number.strip()

        # 1. En güncel olan: Webhook Cache
        if order_number in self._webhook_cache:
            return self._webhook_cache[order_number]

        # 2. Yerel DB'de ara
        try:
            conn = get_connection()
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT * FROM orders WHERE id = ?", (order_number,)
            ).fetchone()
            if row:
                items = cursor.execute(
                    "SELECT * FROM order_items WHERE order_id = ?", (order_number,)
                ).fetchall()
                conn.close()
                return {
                    "source": "chatbot",
                    "order_number": str(row["id"]),
                    "customer_name": row["customer_name"] or "",
                    "phone": row["customer_phone"] or "",
                    "email": row.get("customer_email", "") or "",
                    "order_status": self._map_local_status(row["status"] or "pending"),
                    "payment_method": row["payment_method"] or "",
                    "grand_total": row["grand_total"] or 0,
                    "order_date": row["created_at"] or "",
                    "items": [
                        {
                            "product_name": item["product_name"],
                            "variant_info": item["variant_info"] or "",
                            "price": str(item["unit_price"]),
                        }
                        for item in items
                    ],
                }
            conn.close()
        except Exception:
            pass

        # 3. CSV'de ara
        orders = self._load_csv_orders()
        for order in orders:
            if order.get("order_number") == order_number:
                return {**order, "source": "ikas"}

        return None

    # ─── Sipariş Durumu Formatla ───

    def format_order_status(self, order: dict) -> str:
        """Tek bir siparişin durumunu okunabilir formata çevirir."""
        lines = []
        lines.append(f"📦 Sipariş #{order.get('order_number', '?')}")
        lines.append(f"📅 Tarih: {order.get('order_date', 'Bilinmiyor')}")

        status = order.get("order_status", "")
        status_desc = STATUS_MAP.get(status, f"Durum: {status}")
        lines.append(f"📋 {status_desc}")

        # Ürünler
        items = order.get("items", [])
        if items:
            lines.append("\n🛍️ Ürünler:")
            for item in items:
                variant = f" ({item['variant_info']})" if item.get("variant_info") else ""
                price = f" — {item['price']} TL" if item.get("price") else ""
                lines.append(f"  • {item['product_name']}{variant}{price}")

        total = order.get("grand_total", "")
        if total:
            lines.append(f"\n💰 Toplam: {total} TL")

        payment = order.get("payment_method", "")
        if payment:
            lines.append(f"💳 Ödeme: {payment}")

        return "\n".join(lines)

    def format_orders_summary(self, orders: list[dict], max_display: int = 3) -> str:
        """Birden fazla siparişin özetini formatlar."""
        if not orders:
            return ""

        active_statuses = {"Oluşturuldu", "İletildi", "Gönderildi", "pending", "awaiting_payment"}
        active_orders = [o for o in orders if o.get("order_status", "") in active_statuses]
        past_orders = [o for o in orders if o.get("order_status", "") not in active_statuses]

        lines = []

        if active_orders:
            lines.append("🟢 AKTİF SİPARİŞLERİNİZ:")
            for order in active_orders[:max_display]:
                lines.append(self.format_order_status(order))
                lines.append("")

        if past_orders and not active_orders:
            lines.append("📋 GEÇMİŞ SİPARİŞLERİNİZ:")
            for order in past_orders[:max_display]:
                lines.append(self.format_order_status(order))
                lines.append("")

        if len(orders) > max_display:
            lines.append(f"... ve {len(orders) - max_display} sipariş daha.")

        return "\n".join(lines)

    # ─── Yardımcılar ───

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        """Telefon numarasını normalleştirir (Sadece son 10 hanesini alır)."""
        if not phone:
            return ""
        # Sadece rakamları al
        digits = re.sub(r'[^\d]', '', phone)
        # Sadece son 10 haneyi al (örn: 5551234567)
        if len(digits) >= 10:
            return digits[-10:]
        return digits

    @staticmethod
    def _map_local_status(status: str) -> str:
        """Yerel DB'deki durumu Türkçe'ye çevirir."""
        mapping = {
            "pending": "Oluşturuldu",
            "awaiting_payment": "Oluşturuldu",
            "processing": "İletildi",
            "shipped": "Gönderildi",
            "delivered": "Teslim Edildi",
            "cancelled": "İptal Edildi",
            "refunded": "İade Edildi",
        }
        return mapping.get(status, status)
