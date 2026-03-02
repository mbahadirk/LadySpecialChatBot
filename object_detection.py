from ultralytics import YOLO
from PIL import Image
import numpy as np

class ObjectDetector:
    def __init__(self, model_name="yolov8l.pt"):
        print(f"Loading YOLO model: {model_name}...")
        self.model = YOLO(model_name)
        # COCO class 0 is 'person'
        self.target_classes = [0] 

    def crop_person(self, image: Image.Image):
        """
        Detects person in the image. 
        If found, crops the image to the person's bounding box.
        If not found, returns the original image.
        """
        try:
            # Convert PIL to numpy for YOLO
            img_np = np.array(image)
            
            # Predict
            results = self.model(img_np, verbose=False)
            
            if not results:
                return image
            
            # Get boxes
            boxes = results[0].boxes
            
            best_box = None
            max_area = 0
            
            for box in boxes:
                cls = int(box.cls[0])
                if cls in self.target_classes:
                    # Found a person
                    xyxy = box.xyxy[0].cpu().numpy() # [x1, y1, x2, y2]
                    width = xyxy[2] - xyxy[0]
                    height = xyxy[3] - xyxy[1]
                    area = width * height
                    
                    # Pick largest person
                    if area > max_area:
                        max_area = area
                        best_box = xyxy
            
            if best_box is not None:
                # Crop
                print(f"✂️  Person detected! Cropping to {best_box}")
                x1, y1, x2, y2 = map(int, best_box)
                
                # Add a little padding (margin)
                margin = 0.05 # 5%
                img_w, img_h = image.size
                
                w = x2 - x1
                h = y2 - y1
                
                x1 = max(0, x1 - int(w * margin))
                y1 = max(0, y1 - int(h * margin))
                x2 = min(img_w, x2 + int(w * margin))
                y2 = min(img_h, y2 + int(h * margin))
                
                return image.crop((x1, y1, x2, y2))
            else:
                print("🤷 No person detected. Using full image.")
                return image
                
        except Exception as e:
            print(f"YOLO Error: {e}")
            return image
