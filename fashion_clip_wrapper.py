import torch
from transformers import CLIPProcessor, CLIPModel

class FashionCLIPWrapper:
    def __init__(self, model_name='patrickjohncyh/fashion-clip'):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.processor = CLIPProcessor.from_pretrained(model_name)
        self.model = CLIPModel.from_pretrained(model_name).to(self.device)
        self.model.eval()

    def encode(self, images):
        """
        Encode PIL Images or a single PIL Image.
        Returns a numpy array of embeddings similar to SentenceTransformer.
        """
        if not isinstance(images, list):
            images = [images]
            
        with torch.no_grad():
            inputs = self.processor(images=images, return_tensors="pt").to(self.device)
            image_features = self.model.get_image_features(**inputs)
            # Normalize to match SentenceTransformer behavior (cosine similarity)
            image_features = image_features / image_features.norm(p=2, dim=-1, keepdim=True)
            
        embeddings = image_features.cpu().numpy()
        
        if len(embeddings) == 1:
            return embeddings[0]
        return embeddings
