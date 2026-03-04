"""
LadySpecial ChatBot - Sipariş Servisi

Çok adımlı sipariş akışını yöneten state machine.
Her müşteri için ayrı bir OrderSession tutulur (in-memory).
Sipariş tamamlandığında DB'ye kaydedilir.

Aşamalar:
  product_selection → variant_selection → price_summary → customer_info → confirmation → payment_selection → completed
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from models.database import get_connection

# Kargo kuralları
FREE_SHIPPING_THRESHOLD = 2500  # TL
SHIPPING_COST = 89.90  # TL


@dataclass
class OrderItem:
    """Siparişteki tek bir ürün."""
    product_id: str = ""
    product_name: str = ""
    variant_info: str = ""   # "M / Siyah" gibi
    quantity: int = 1
    unit_price: float = 0.0
    url: str = ""
    in_stock_variants: list = field(default_factory=list)


@dataclass
class OrderSession:
    """Aktif sipariş oturumu."""
    user_id: int = 0
    platform: str = ""
    sender_id: str = ""
    stage: str = "product_selection"  # Mevcut aşama
    items: list = field(default_factory=list)  # list[OrderItem]
    customer_info: dict = field(default_factory=dict)
    # customer_info yapısı: {"name": "", "phone": "", "email": "", "address": ""}
    payment_method: str = ""  # "kapida_odeme", "havale", "eft"
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class OrderService:
    """Sipariş oturumlarını yöneten servis."""

    STAGES = [
        "product_selection",
        "variant_selection",
        "price_summary",
        "customer_info",
        "confirmation",
        "payment_selection",
        "completed",
    ]

    def __init__(self):
        # Aktif oturumlar: {platform_sender_id: OrderSession}
        self._sessions: dict[str, OrderSession] = {}

    # ─── Oturum Key ───
    @staticmethod
    def _key(platform: str, sender_id: str) -> str:
        return f"{platform}_{sender_id}"

    # ─── Oturum Yönetimi ───

    def start_session(self, platform: str, sender_id: str, user_id: int) -> OrderSession:
        """Yeni sipariş oturumu başlatır."""
        key = self._key(platform, sender_id)
        session = OrderSession(
            user_id=user_id,
            platform=platform,
            sender_id=sender_id,
            stage="product_selection",
        )
        self._sessions[key] = session
        print(f"[OrderService] 🛒 Yeni sipariş oturumu: {key}")
        return session

    def get_session(self, platform: str, sender_id: str) -> OrderSession | None:
        """Aktif oturum varsa döndürür."""
        return self._sessions.get(self._key(platform, sender_id))

    def cancel_session(self, platform: str, sender_id: str) -> bool:
        """Oturumu iptal eder."""
        key = self._key(platform, sender_id)
        if key in self._sessions:
            del self._sessions[key]
            print(f"[OrderService] ❌ Sipariş iptal edildi: {key}")
            return True
        return False

    def update_stage(self, platform: str, sender_id: str, new_stage: str):
        """Oturumun aşamasını günceller."""
        session = self.get_session(platform, sender_id)
        if session:
            old = session.stage
            session.stage = new_stage
            print(f"[OrderService] 📋 Aşama: {old} → {new_stage}")

    # ─── Ürün İşlemleri ───

    def add_product(self, platform: str, sender_id: str, product_data: dict) -> OrderItem:
        """Oturuma ürün ekler."""
        session = self.get_session(platform, sender_id)
        if not session:
            return None

        item = OrderItem(
            product_id=product_data.get("id", ""),
            product_name=product_data.get("name", ""),
            unit_price=product_data.get("price", 0),
            url=product_data.get("url", ""),
            in_stock_variants=product_data.get("in_stock_variants", []),
        )
        session.items.append(item)
        print(f"[OrderService] ➕ Ürün eklendi: {item.product_name} ({item.unit_price} TL)")
        return item

    def clear_products(self, platform: str, sender_id: str):
        """Ürünleri temizler (yeniden seçim için)."""
        session = self.get_session(platform, sender_id)
        if session:
            session.items.clear()

    def set_variant(self, platform: str, sender_id: str, item_index: int, variant_info: str):
        """Belirli bir ürünün varyant bilgisini set eder."""
        session = self.get_session(platform, sender_id)
        if session and 0 <= item_index < len(session.items):
            session.items[item_index].variant_info = variant_info
            print(f"[OrderService] 🏷️ Varyant: {session.items[item_index].product_name} → {variant_info}")

    # ─── Ödeme Yöntemi ───

    def set_payment_method(self, platform: str, sender_id: str, method: str):
        """Ödeme yöntemini set eder."""
        session = self.get_session(platform, sender_id)
        if session:
            session.payment_method = method
            print(f"[OrderService] 💳 Ödeme yöntemi: {method}")

    # ─── Müşteri Bilgileri ───

    def set_customer_info(self, platform: str, sender_id: str, info: dict):
        """Müşteri bilgilerini günceller (kısmi güncelleme destekler)."""
        session = self.get_session(platform, sender_id)
        if session:
            session.customer_info.update(info)
            print(f"[OrderService] 👤 Müşteri bilgisi güncellendi: {list(info.keys())}")

    # ─── Sipariş Özeti ───

    def build_order_summary(self, platform: str, sender_id: str) -> str:
        """Tam sipariş özetini oluşturur."""
        session = self.get_session(platform, sender_id)
        if not session:
            return ""

        lines = ["📦 SİPARİŞ ÖZETİ", "─" * 25]

        subtotal = 0.0
        for i, item in enumerate(session.items, 1):
            variant = f" ({item.variant_info})" if item.variant_info else ""
            lines.append(f"{i}. {item.product_name}{variant}")
            lines.append(f"   Fiyat: {item.unit_price:.2f} TL x {item.quantity} adet")
            subtotal += item.unit_price * item.quantity

        lines.append(f"\n{'─' * 25}")
        lines.append(f"Ara Toplam: {subtotal:.2f} TL")

        shipping = 0.0 if subtotal >= FREE_SHIPPING_THRESHOLD else SHIPPING_COST
        if shipping > 0:
            lines.append(f"Kargo: {shipping:.2f} TL")
        else:
            lines.append("Kargo: ÜCRETSİZ 🎉")

        grand_total = subtotal + shipping
        lines.append(f"TOPLAM: {grand_total:.2f} TL")

        # Ödeme yöntemi (varsa)
        if session.payment_method:
            payment_labels = {
                "kapida_odeme": "Kapıda Ödeme",
                "havale": "Havale",
                "eft": "EFT",
            }
            payment_label = payment_labels.get(session.payment_method, session.payment_method)
            lines.append(f"\n💳 Ödeme: {payment_label}")

        info = session.customer_info
        if info:
            lines.append(f"\n{'─' * 25}")
            lines.append("👤 TESLİMAT BİLGİLERİ")
            if info.get("name"):
                lines.append(f"İsim: {info['name']}")
            if info.get("phone"):
                lines.append(f"Telefon: {info['phone']}")
            if info.get("email"):
                lines.append(f"E-posta: {info['email']}")
            if info.get("address"):
                lines.append(f"Adres: {info['address']}")

        return "\n".join(lines)

    def build_price_summary(self, platform: str, sender_id: str) -> str:
        """Sadece fiyat özetini oluşturur (sipariş onayı öncesi gösterim için)."""
        session = self.get_session(platform, sender_id)
        if not session:
            return ""

        lines = ["💰 FİYAT ÖZETİ", "─" * 25]

        subtotal = 0.0
        for i, item in enumerate(session.items, 1):
            variant = f" ({item.variant_info})" if item.variant_info else ""
            lines.append(f"{i}. {item.product_name}{variant}")
            lines.append(f"   Fiyat: {item.unit_price:.2f} TL x {item.quantity} adet")
            subtotal += item.unit_price * item.quantity

        lines.append(f"\n{'─' * 25}")
        lines.append(f"Ara Toplam: {subtotal:.2f} TL")

        shipping = 0.0 if subtotal >= FREE_SHIPPING_THRESHOLD else SHIPPING_COST
        if shipping > 0:
            lines.append(f"Kargo Ücreti: {shipping:.2f} TL")
        else:
            lines.append("Kargo: ÜCRETSİZ 🎉")

        grand_total = subtotal + shipping
        lines.append(f"TOPLAM: {grand_total:.2f} TL")

        return "\n".join(lines)

    def get_order_data(self, platform: str, sender_id: str) -> dict:
        """Sipariş verisini dict olarak döndürür (LLM'e ve DB'ye göndermek için)."""
        session = self.get_session(platform, sender_id)
        if not session:
            return {}

        subtotal = sum(item.unit_price * item.quantity for item in session.items)
        shipping = 0.0 if subtotal >= FREE_SHIPPING_THRESHOLD else SHIPPING_COST

        return {
            "items": [
                {
                    "product_id": item.product_id,
                    "product_name": item.product_name,
                    "variant_info": item.variant_info,
                    "quantity": item.quantity,
                    "unit_price": item.unit_price,
                }
                for item in session.items
            ],
            "subtotal": subtotal,
            "shipping_cost": shipping,
            "grand_total": subtotal + shipping,
            "payment_method": session.payment_method or "",
            "customer_info": session.customer_info,
            "stage": session.stage,
        }

    # ─── Sipariş Tamamlama ───

    def complete_order(self, platform: str, sender_id: str) -> int | None:
        """Siparişi DB'ye kaydeder ve oturumu temizler. Order ID döndürür."""
        session = self.get_session(platform, sender_id)
        if not session:
            return None

        subtotal = sum(item.unit_price * item.quantity for item in session.items)
        shipping = 0.0 if subtotal >= FREE_SHIPPING_THRESHOLD else SHIPPING_COST
        grand_total = subtotal + shipping
        info = session.customer_info

        try:
            conn = get_connection()
            cursor = conn.cursor()

            cursor.execute(
                """INSERT INTO orders (user_id, platform, customer_name, customer_phone,
                   customer_email, customer_address, total_price, shipping_cost, grand_total,
                   payment_method, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session.user_id,
                    session.platform,
                    info.get("name", ""),
                    info.get("phone", ""),
                    info.get("email", ""),
                    info.get("address", ""),
                    subtotal,
                    shipping,
                    grand_total,
                    session.payment_method or "kapida_odeme",
                    "pending" if session.payment_method == "kapida_odeme" else "awaiting_payment",
                    datetime.utcnow().isoformat(),
                )
            )
            order_id = cursor.lastrowid

            for item in session.items:
                cursor.execute(
                    """INSERT INTO order_items (order_id, product_id, product_name,
                       variant_info, quantity, unit_price)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        order_id,
                        item.product_id,
                        item.product_name,
                        item.variant_info,
                        item.quantity,
                        item.unit_price,
                    )
                )

            conn.commit()
            conn.close()

            print(f"[OrderService] ✅ Sipariş kaydedildi! Order ID: {order_id}")

            # Oturumu temizle
            key = self._key(platform, sender_id)
            del self._sessions[key]

            return order_id

        except Exception as e:
            print(f"[OrderService] ❌ Sipariş kaydetme hatası: {e}")
            import traceback
            traceback.print_exc()
            return None

    # ─── Olay Tetikleyicileri (Hooks) ───

    def on_payment_requires_admin(self, order_id: int, payment_method: str, session_data: dict):
        """
        Havale veya EFT seçildiğinde tetiklenen hook.
        İlerleyen süreçte sistem yöneticisine e-posta gönderme,
        bildirim gönderme gibi işlemler için kullanılacak.
        
        Args:
            order_id: Sipariş numarası
            payment_method: Ödeme yöntemi ('havale' veya 'eft')
            session_data: Sipariş bilgileri (müşteri bilgileri, ürünler vs.)
        """
        print(f"[OrderService] 🔔 HOOK: Ödeme onayı gerekiyor! Order #{order_id} - Yöntem: {payment_method}")
        print(f"[OrderService] 🔔 Müşteri: {session_data.get('customer_info', {}).get('name', 'Bilinmiyor')}")
        print(f"[OrderService] 🔔 E-posta: {session_data.get('customer_info', {}).get('email', 'Bilinmiyor')}")
        print(f"[OrderService] 🔔 Toplam: {session_data.get('grand_total', 0):.2f} TL")
        
        # TODO: İlerleyen süreçte buraya eklenecek işlemler:
        # - Sistem yöneticisine e-posta gönder
        # - Admin paneline bildirim gönder
        # - Otomatik banka bilgisi paylaşımı
        pass
