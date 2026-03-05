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
            outputs = self.model.get_image_features(**inputs)
            
            # DÜZELTME: Bazı transformer versiyonlarında veya modellerde get_image_features 
            # doğrudan tensor yerine 'BaseModelOutputWithPooling' nesnesi dönebilir.
            if not isinstance(outputs, torch.Tensor):
                if hasattr(outputs, "image_embeds"):
                    # Bu doğrudan CLIP projeksiyonu yapılmış vektördür
                    image_features = outputs.image_embeds
                elif hasattr(outputs, "pooler_output"):
                    # Bu ham vizyon çıktısıdır, projeksiyon katmanı uygulanmalıdır
                    image_features = outputs.pooler_output
                    if hasattr(self.model, "visual_projection"):
                        image_features = self.model.visual_projection(image_features)
                else:
                    # Bilinmeyen durum, olduğu gibi bırak
                    image_features = outputs
            else:
                image_features = outputs
                
            # Normalize to match SentenceTransformer behavior (cosine similarity)
            image_features = image_features / image_features.norm(p=2, dim=-1, keepdim=True)
            
        embeddings = image_features.cpu().numpy()
        
        if len(embeddings) == 1:
            return embeddings[0]
        return embeddings
