import os
from PIL import Image
import requests
from io import BytesIO
from services.image_service import ImageService

def test_pipeline():
    print("Testing Vision Pipeline End-to-End...")
    service = ImageService()
    
    print("\n1. Testing YOLO loading...")
    yolo = service._get_yolo_model()
    print("YOLO loaded.")
    
    print("\n2. Testing FashionCLIP loading...")
    clip = service._get_clip_model()
    print("CLIP loaded.")

    print("\n3. Downloading test image (a person wearing a dress)...")
    url = "https://images.unsplash.com/photo-1595777457583-95e059d581b8?w=800&auto=format&fit=crop"
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        img = Image.open(BytesIO(response.content))
        os.makedirs("test_images", exist_ok=True)
        test_path = "test_images/sample_dress.jpg"
        img.save(test_path)
        print(f"Image saved to {test_path}")
        
        print("\n4. Running search_by_image (YOLO crop -> FashionCLIP -> Qdrant)...")
        results = service.search_by_image(test_path, max_results=5)
        
        print("\n--- RESULTS ---")
        if not results:
             print("No results found. (Threshold rejected or DB empty)")
        for i, r in enumerate(results):
             print(f"{i+1}. {r['name']} (Score: {r['score']}, Price: {r['price']} TL)")
             
    except Exception as e:
        print(f"Test failed: {e}")

if __name__ == "__main__":
    test_pipeline()
    print("\nPipeline test complete.")
