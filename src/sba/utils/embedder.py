from sentence_transformers import SentenceTransformer
import torch

class Embedder:
    def __init__(self):
        self.model = SentenceTransformer('BAAI/bge-m3', device='cpu')
    
    def encode(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, batch_size=32, normalize_embeddings=True)
    
    def encode_single(self, text: str) -> list[float]:
        return self.model.encode([text], normalize_embeddings=True)[0]
