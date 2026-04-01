from sentence_transformers import SentenceTransformer
import torch

class Embedder:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.model = SentenceTransformer('BAAI/bge-m3', device=device)
    
    def encode(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, batch_size=32, normalize_embeddings=True)
    
    def encode_single(self, text: str) -> list[float]:
        return self.model.encode([text], normalize_embeddings=True)[0]
