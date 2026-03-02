import re
import os
from openai import OpenAI
from indexer import load_model, QdrantClient, COLLECTION_NAME, QDRANT_URL
from ikas_service import IkasClient
from PIL import Image
from io import BytesIO
import requests
from object_detection import ObjectDetector
import base64


# Initialize Clients
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
qdrant = QdrantClient(url=QDRANT_URL)
# We load CLIP model lazily or globally. For prod, global is better but consumes RAM.
# clip_model = load_model() # Uncomment if we have enough RAM and want speed

# Configuration
DEBUG_MODE = False # Set to True to see technical logs in chat

class Orchestrator:
    def __init__(self):
        self.ikas = IkasClient()
        # self.clip_model = load_model() # Load once
        self.clip_model = None # Lazy load
        self.detector = ObjectDetector() # Initialize YOLO
        
        # Enhanced System Prompt
        self.system_prompt = {
            "role": "system",
            "content": """
Rol: Sen LadySpecial markasının profesyonel, samimi ve yardımsever Satış Asistanısın. Yapay Zeka değilsin.
Görevin: Müşterilerin Instagram DM üzerinden gönderdiği fotoğraflara veya sorulara cevap vererek onlara doğru ürünleri bulmak ve satışa yönlendirmek.

Kurallar ve Tone:
1. **Samimi ve Profesyonel:** "Merhabalar! 🌸", "Harika bir tercih!" gibi sıcak bir dil kullan. Emoji kullanmaktan çekinme ama abartma.
2. **Dürüstlük:** Asla stokta olmayan bir ürünü var gibi gösterme. Fiyat bilgisini uydurma. Veritabanından gelen veriyi esas al.
3. **Satış Odaklılık:** Müşteriyi ürünü incelemeye veya satın almaya teşvik et. ("Bu elbiseyi web sitemizde detaylı inceleyebilirsiniz 👇")
4. **Marka Bilinci:** Sadece LadySpecial ürünleri hakkında konuş. Rakip markalardan veya politika/din/spor gibi konulardan bahsetme.
5. **Kısa ve Verimli:** Instagram kullanıcıları uzun metin sevmez. Cevapların net ve mobil uyumlu olsun.

Cevap Stratejisi:
- Eğer kullanıcı resim attıysa: "Buna çok benzeyen harika modellerimiz var!" diyerek en iyi eşleşmeleri sun.
- Eğer ürün bulamadıysan: "Tam olarak aynısı kalmamış ama şu tarz alternatiflerimiz çok hoşuna gidebilir" de.
- Fiyat sorulursa: Net fiyatı söyle.

Unutma: Sen bir asistansın, teknik bir bot değilsin. "Sistem verilerine göre..." yerine "Stoklarımızda..." demeyi tercih et. Stokta olmayan ürünleri kullanıcıya söyleme .
Eğer senden stoğu bitmiş bir ürünle ilgili bilgi isterse stokta olmadığını söyle. Hangi renkleri var gibi sorularda yalnızca stokta olan verileri söyle.
"""
        }
        self.history = [self.system_prompt]

    def verify_match_with_gpt(self, user_image, candidates):
        """
        Uses GPT-4o to verify the best match among candidates.
        user_image: PIL Image (cropped)
        candidates: List of Qdrant ScoredPoint
        """
        
        # Convert PIL Image to base64
        if user_image.mode in ("RGBA", "P"):
            user_image = user_image.convert("RGB")
            
        buffered = BytesIO()
        user_image.save(buffered, format="JPEG")
        base64_image = base64.b64encode(buffered.getvalue()).decode('utf-8')
        
        print(f"DEBUG: Starting verify_match_with_gpt with {len(candidates)} candidates.")
        
        # Prepare candidate data for parallel download
        from concurrent.futures import ThreadPoolExecutor
        import time
        
        start_download = time.time()
        
        def _download_and_format(index, cand_payload):
             # Helper to process one candidate
             info = cand_payload
             cand_text = f"Candidate {index+1} (ID: {info.get('id', 'N/A')}, Name: {info['name']}, Price: {info['price']})"
             
             result_parts = [{
                "type": "text",
                "text": cand_text
             }]
             
             if info.get('image_url'):
                 img_data = None
                 try:
                     img_url = info['image_url']
                     # print(f"Downloading candidate image: {img_url}")
                     resp = requests.get(img_url, timeout=4) # Short timeout
                     if resp.status_code == 200:
                         cand_b64 = base64.b64encode(resp.content).decode('utf-8')
                         img_data = f"data:image/jpeg;base64,{cand_b64}"
                 except Exception:
                     pass # Silently fail to keep logs clean

                 if img_data:
                     result_parts.append({
                        "type": "image_url",
                        "image_url": {"url": img_data}
                    })
                 else:
                     result_parts.append({
                        "type": "text",
                        "text": "[IMAGE DOWNLOAD FAILED]"
                    })
             return result_parts

        candidate_content = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(_download_and_format, i, c.payload) for i, c in enumerate(candidates)]
            for future in futures:
                candidate_content.extend(future.result())
        
        print(f"DEBUG: Downloads finished in {time.time() - start_download:.2f}s")

        prompt_messages = [
            {
                "role": "system",
                "content": "You are a fashion product matcher for 'Ladyspecial'. Your goal is to identify if any of the candidate products MATCH the user's uploaded image. Focus on visual similarity in pattern, cut, color, and style.\n\nOutput format:\nReasoning: [Brief explanation of why you picked the match or why none matched]\nMatch: [ID or 'None']"
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Here is the user's uploaded image:"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    },
                    {
                        "type": "text",
                        "text": "Here are candidates found by search. Which one is the best match?"
                    },
                    *candidate_content
                ]
            }
        ]
        
        try:
            print("Identifying best match with GPT-4o...")
            start_gpt = time.time()
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=prompt_messages,
                max_tokens=150
            )
            print(f"DEBUG: GPT-4o call finished in {time.time() - start_gpt:.2f}s")
            content = response.choices[0].message.content.strip()
            print(f"GPT-4o Response: {content}")
            
            # Simple parsing
            match_id = "None"
            reasoning = "No reasoning provided."
            
            if "Match:" in content:
                parts = content.split("Match:")
                reasoning = parts[0].replace("Reasoning:", "").strip()
                match_id = parts[1].strip()
            else:
                # Fallback if format is missed
                if "None" in content:
                    match_id = "None"
                else:
                    match_id = content # Hope it's just the ID
            
            return match_id, reasoning
            
        except Exception as e:
            print(f"GPT Verification Error: {e}")
            return "None", f"Error: {e}"



    def get_clip_model(self):
        if not self.clip_model:
            from sentence_transformers import SentenceTransformer
            # Must match the model used in indexer.py (768 dim)
            self.clip_model = SentenceTransformer('sentence-transformers/clip-ViT-L-14')
        return self.clip_model

    def analyze_message(self, message_data):
        """
        Determine if message is text, image, or link.
        """
        msg_type = message_data.get("type")
        
        if msg_type == "text":
            text_body = message_data.get("text", {}).get("body", "")
            if "instagram.com/reel" in text_body or "http" in text_body:
                return "link", text_body
            return "text", text_body
        
        if msg_type == "image":
            image_id = message_data.get("image", {}).get("id")
            # We would need to fetch image URL from ID using Meta API
            # For this prototype we assume we get a URL or handle it later
            return "image", image_id

        return "unknown", None

    def handle_text(self, text):
        print(f"Handling Text: {text}")
        # 1. Use OpenAI to determine intent or search
        # Function Calling definition
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search_product",
                    "description": "Search for a product by name in the store.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "The product name to search for"},
                        },
                        "required": ["query"],
                    },
                }
            }
        ]


        # Add history to context
        messages = [self.system_prompt]
        messages.extend(self.history[-5:]) # Last 5 turns context
        messages.append({"role": "user", "content": text})

        
        try:
            completion = client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                tools=tools,
            )
            
            tool_calls = completion.choices[0].message.tool_calls
            if tool_calls:
                for tool_call in tool_calls:
                    if tool_call.function.name == "search_product":
                         import json
                         import difflib
                         
                         args = json.loads(tool_call.function.arguments)
                         query = args.get("query", "").lower()
                         print(f"Searching for: {query}")
                         
                         # Load JSON DB
                         try:
                             with open("chatbot_database.json", "r", encoding="utf-8") as f:
                                 products = json.load(f)
                             
                             # Simple keyword search
                             results = []
                             for p in products:
                                 if query in p["name"].lower() or query in p["description"].lower():
                                    results.append(p)
                                    
                             if not results:
                                 return f"Üzgünüm, '{query}' ile ilgili bir ürün bulamadım."
                             
                             # Format response
                             response = f"'{query}' için {len(results)} ürün buldum:\n"
                             for r in results[:5]: # Top 5
                                 response += f"- **{r['name']}** - {r['price']} TL (Stok: {r['stock']})\n"
                                 if r.get("image_url"):
                                     response += f"  ![Ürün]({r['image_url']})\n"
                             
                             return response
                             
                         except Exception as e:
                             print(f"Search error: {e}")
                             return "Ürün aranırken bir hata oluştu."
            
            return completion.choices[0].message.content
            
        except Exception as e:
            return f"Error processing text: {e}"

    def AddToHistory(self, role, content):
        self.history.append({"role": role, "content": content})


    def detect_clothing_category(self, image):
        # Convert PIL Image to base64
        if image.mode in ("RGBA", "P"):
            image = image.convert("RGB")
            
        buffered = BytesIO()
        image.save(buffered, format="JPEG")
        base64_image = base64.b64encode(buffered.getvalue()).decode('utf-8')
        
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": "Analyze the clothing item in the image and classify it into ONE of these categories: 'Elbise', 'Pantolon', 'Etek', 'Kazak' (includes Blouse/Shirt/Top/Hırka), 'Ceket' (includes Vest/Coat), 'Takım' (Set). Return ONLY the category name."
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                        ]
                    }
                ],
                max_tokens=10
            )
            category = response.choices[0].message.content.strip().lower()
            print(f"DEBUG: Detected Category: {category}")
            
            # Map valid outputs
            if "elbise" in category: return "elbise"
            if "pantolon" in category: return "pantolon"
            if "etek" in category: return "etek"
            if "kazak" in category or "bluz" in category or "gömlek" in category or "hırka" in category: return "kazak"
            if "ceket" in category or "mont" in category: return "ceket"
            if "takım" in category: return "takım"
            
            return None
        except Exception as e:
            print(f"Category Detection Error: {e}")
            return None

    def handle_image(self, image_source):
        print(f"Handling Image...")
        
        try:
            image = None
            if isinstance(image_source, str):
                if image_source.startswith("http"):
                    # URL
                    response = requests.get(image_source)
                    image = Image.open(BytesIO(response.content))
                else:
                    return "Geçersiz görsel linki."
            else:
                # Assume bytes or PIL Image
                if isinstance(image_source, bytes):
                    image = Image.open(BytesIO(image_source))
                else:
                    image = image_source # Already PIL Image?

            if not image:
                return "Görsel işlenemedi."
            
            # Ensure RGB (Fix for YOLO 4-channel error and JPEG saving)
            if image.mode != "RGB":
                image = image.convert("RGB")

            # Trace Log for Debugging
            trace = ["🔍 **Sistem Düşünce Günlüğü (Debug)**"]
            
            # 1.5. YOLO Crop (Remove Background Noise)
            print("Running Object Detection...")
            try:
                original_size = image.size
                image = self.detector.crop_person(image)
                new_size = image.size
                if original_size != new_size:
                    trace.append(f"- ✂️ YOLO: Kişi tespit edildi ve kırpıldı (Boyut: {original_size} -> {new_size}).")
                else:
                    trace.append(f"- ⚠️ YOLO: Kişi tespit edilemedi, tam resim kullanılıyor.")
            except Exception as e:
                trace.append(f"- ❌ YOLO Hatası: {e}")
            
            # 1.8. Smart Category Detection (Hybrid Search)
            # trace.append(f"- 🧠 GPT-4o: Görsel kategorisi analiz ediliyor...")
            # detected_category = self.detect_clothing_category(image)
            detected_category = None # DISABLED to save cost
            # trace.append(f"- 🏷️ Kategori: {detected_category}")

            # 2. Vectorize
            model = self.get_clip_model()
            embedding = model.encode(image)
            trace.append(f"- 🧠 CLIP: Görsel vektöre çevrildi (768 boyut).")
            
            # 3. Search Qdrant
            # Retrieve MORE results then filter
            print(f"Querying Qdrant collection: {COLLECTION_NAME}")
            search_result = qdrant.query_points(
                collection_name=COLLECTION_NAME,
                query=embedding.tolist(),
                limit=50 # Fetch more for filtering
            ).points
            
            trace.append(f"- 🔎 Qdrant: {len(search_result)} ham sonuç bulundu.")
            
            # Filtering & Deduplication Logic
            unique_products = []
            seen_names = set()
            
            for hit in search_result:
                payload = hit.payload
                score = hit.score
                p_name = payload.get('name')
                p_category = payload.get('category', '').lower()
                
                # Category Filter (DISABLED)
                if detected_category:
                     # ... (logic skipped since None)
                     pass
            
                p_stock = int(payload.get('stock', 0)) # Fallback to 0 if missing
                
                if p_stock <= 0:
                    continue

                if p_name in seen_names:
                    continue
                
                seen_names.add(p_name)
                unique_products.append(hit)
                
                # Add score to trace
                trace.append(f"  * Aday: {p_name} (Skor: {score:.4f})")
                
                if len(unique_products) >= 5:
                     break
            
            trace.append(f"- 🧹 Filtreleme: Stok ve kopyalar temizlendi. Geriye {len(unique_products)} aday kaldı.")

            if not unique_products:
                return "Üzgünüm, şu an stoklarımızda buna benzer bir parça bulamadım. 😔 Ama yeni sezon ürünlerimize göz atmak istersen link profilimizde! 💖" + ("\n\n" + "\n".join(trace) if DEBUG_MODE else "")
            
            # --- Generate Response ---
            # We have unique_products (Best Match + Alternatives)
            best_match = unique_products[0].payload
            alternatives = unique_products[1:]
            
            # Construct a friendly message
            response_text = f"Harika bir stil! ✨ Paylaştığın görsele en çok benzeyen modelimiz bu:\n\n"
            
            # Best Match Info
            name = best_match.get('name', 'Ürün')
            price = best_match.get('price', 0)
            url = best_match.get('image_url')
            
            response_text += f"👗 **{name}**\n💰 Fiyat: {price} TL\n"
            if url:
                response_text += f"![Ürün Görseli]({url})\n"
            
            response_text += "\n👇 Hemen incelemek için tıkla!\n(Link buraya gelecek)\n"
            
            if alternatives:
                response_text += "\nAlternatif olarak şunlar da ilgini çekebilir:\n"
                for alt in alternatives[:3]:
                    alt_name = alt.payload.get('name', 'Ürün')
                    alt_price = alt.payload.get('price', 0)
                    response_text += f"- {alt_name} ({alt_price} TL)\n"
            
            response_text += "\nBaşka bir şey arıyorsan sormaktan çekinme! 💕"

            # Append trace only if DEBUG_MODE is on
            if DEBUG_MODE:
                response_text += "\n\n---\n" + "\n".join(trace)

            return response_text
            
        except Exception as e:
            print(f"Error handling image: {e}")
            err_msg = "Bir teknik aksaklık oldu, lütfen tekrar dene. 🙏"
            if DEBUG_MODE:
                err_msg += f"\nError: {e}"
            return err_msg

    def handle_link(self, url):
        print(f"Handling Link: {url}")
        # Scrape OG image using requests/BeautifulSoup or regex
        try:
            resp = requests.get(url)
            # Simple regex for og:image
            import re
            match = re.search(r'<meta property="og:image" content="([^"]+)"', resp.text)
            if match:
                image_url = match.group(1)
                print(f"Found thumbnail: {image_url}")
                return self.handle_image(image_url)
            else:
                return "Could not extract image from link."
        except Exception as e:
            return f"Error processing link: {e}"
