import os
import time
import json
import numpy as np
from typing import List, Dict, Tuple, Any, Optional

# Attempt to import faiss, if not available, fallback is handled.
try:
    import faiss
    HAS_FAISS = True
except Exception:
    HAS_FAISS = False

try:
    from sentence_transformers import SentenceTransformer
    HAS_SENTENCE_TRANSFORMERS = True
except Exception:
    HAS_SENTENCE_TRANSFORMERS = False

DB_DIR = os.path.dirname(__file__)
MODEL_DIR = os.path.join(os.path.dirname(DB_DIR), "model_offline")
CACHE_DIR = os.path.join(DB_DIR, "vector_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

class VectorIndexManager:
    def __init__(self):
        self.model: Optional[Any] = None
        self.use_faiss = HAS_FAISS
        
        # FAISS index and mappings
        self.faiss_index: Optional[Any] = None
        
        # Fallback Numpy structures
        self.numpy_embeddings: Optional[np.ndarray] = None # Shape: (N, 384)
        
        # Shared mappings: index position -> material dict (code, name, spec, category)
        self.index_to_material: List[Dict[str, str]] = []
        
        self.model_name_or_path = "paraphrase-multilingual-MiniLM-L12-v2"
        self._initialize_model()

    def _initialize_model(self):
        if not HAS_SENTENCE_TRANSFORMERS:
            print("Warning: sentence-transformers is not installed. Semantic search will be disabled.")
            return

        # Check offline path first
        offline_model_path = os.path.join(MODEL_DIR, self.model_name_or_path)
        if os.path.exists(offline_model_path):
            model_path = offline_model_path
            print(f"Loading offline sentence-transformers model from {model_path}...")
        else:
            model_path = self.model_name_or_path
            print(f"Offline model not found at {offline_model_path}. Loading/downloading model from HF Hub: {model_path}...")
            
        try:
            self.model = SentenceTransformer(model_path)
            print("SentenceTransformer model loaded successfully.")
        except Exception as e:
            print(f"Error loading SentenceTransformer model: {e}. Semantic search is disabled.")
            self.model = None

    @property
    def is_enabled(self) -> bool:
        return self.model is not None

    def encode_queries(self, texts: List[str]) -> Optional[np.ndarray]:
        """Encode a batch of query texts into normalized embeddings."""
        if not self.is_enabled or not texts:
            return None
        try:
            embeddings = self.model.encode(texts, batch_size=64, show_progress_bar=False, convert_to_numpy=True)
            if HAS_FAISS:
                faiss.normalize_L2(embeddings)
            else:
                # L2 normalize using numpy
                norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
                norms = np.where(norms == 0, 1.0, norms)
                embeddings = embeddings / norms
            return embeddings
        except Exception as e:
            print(f"Error encoding query texts: {e}")
            return None

    def encode_single_query(self, text: str) -> Optional[np.ndarray]:
        """Encode a single query text into a normalized embedding."""
        res = self.encode_queries([text])
        if res is not None:
            return res[0]
        return None

    def load_cache(self) -> bool:
        """Load index and mapping cache from disk."""
        index_file = os.path.join(CACHE_DIR, "materials.index")
        npy_file = os.path.join(CACHE_DIR, "materials_embeddings.npy")
        mapping_file = os.path.join(CACHE_DIR, "index_to_material.json")
        
        if not os.path.exists(mapping_file):
            return False
            
        try:
            with open(mapping_file, "r", encoding="utf-8") as f:
                self.index_to_material = json.load(f)
        except Exception as e:
            print(f"Error loading index-to-material mapping: {e}")
            return False

        # Load FAISS index if available and requested
        if self.use_faiss and os.path.exists(index_file):
            try:
                self.faiss_index = faiss.read_index(index_file)
                print(f"Loaded FAISS index from cache with {self.faiss_index.ntotal} items.")
                return True
            except Exception as e:
                print(f"Error loading FAISS index: {e}. Falling back to Numpy.")
                self.use_faiss = False

        # Fallback to loading raw Numpy embeddings
        if os.path.exists(npy_file):
            try:
                self.numpy_embeddings = np.load(npy_file)
                print(f"Loaded Numpy embeddings cache with shape {self.numpy_embeddings.shape}.")
                return True
            except Exception as e:
                print(f"Error loading Numpy embeddings: {e}")
                
        return False

    def save_cache(self):
        """Save index and mapping cache to disk."""
        index_file = os.path.join(CACHE_DIR, "materials.index")
        npy_file = os.path.join(CACHE_DIR, "materials_embeddings.npy")
        mapping_file = os.path.join(CACHE_DIR, "index_to_material.json")
        
        try:
            with open(mapping_file, "w", encoding="utf-8") as f:
                json.dump(self.index_to_material, f, ensure_ascii=False, indent=4)
                
            if self.use_faiss and self.faiss_index is not None:
                faiss.write_index(self.faiss_index, index_file)
                
            if self.numpy_embeddings is not None:
                np.save(npy_file, self.numpy_embeddings)
            print("Saved vector index and mapping cache.")
        except Exception as e:
            print(f"Error saving vector cache: {e}")

    def build_index(self, materials: List[Dict[str, Any]]):
        """
        Build index from scratch.
        materials: List of Dict containing 'code', 'name', 'specification', 'category'
        """
        if not self.is_enabled:
            return
            
        print(f"Building vector index for {len(materials)} materials...")
        start_time = time.time()
        
        self.index_to_material = []
        texts = []
        for m in materials:
            self.index_to_material.append({
                "code": m["code"],
                "name": m["name"],
                "specification": m["specification"],
                "category": m["category"]
            })
            # Build search text (name + spec)
            texts.append(f"{m['name']} {m['specification']}".strip())

        # Encode all texts in batches
        embeddings = self.encode_queries(texts)
        if embeddings is None:
            print("Error: Failed to encode materials. Index not built.")
            return

        self.numpy_embeddings = embeddings
        
        if self.use_faiss:
            try:
                # 384 dimensions for paraphrase-multilingual-MiniLM-L12-v2
                self.faiss_index = faiss.IndexFlatIP(384)
                self.faiss_index.add(embeddings)
                print(f"FAISS index built in {time.time() - start_time:.2f}s.")
            except Exception as e:
                print(f"Error building FAISS index: {e}. Falling back to Numpy.")
                self.use_faiss = False
        else:
            print(f"Numpy index built in {time.time() - start_time:.2f}s.")

        self.save_cache()

    def add_to_index(self, material: Dict[str, Any]):
        """Incremental update: add a single material to the index."""
        if not self.is_enabled:
            return
            
        code = material["code"]
        # Check if already exists, if so, we can skip or replace.
        # For simplicity, we append it.
        text = f"{material['name']} {material['specification']}".strip()
        vector = self.encode_single_query(text)
        if vector is None:
            return

        # Append to mapping
        self.index_to_material.append({
            "code": code,
            "name": material["name"],
            "specification": material["specification"],
            "category": material["category"]
        })

        vector_expanded = np.expand_dims(vector, axis=0)

        # Update FAISS
        if self.use_faiss and self.faiss_index is not None:
            try:
                self.faiss_index.add(vector_expanded)
            except Exception as e:
                print(f"Error adding to FAISS index: {e}. Disabling FAISS.")
                self.use_faiss = False

        # Update Numpy array
        if self.numpy_embeddings is not None:
            self.numpy_embeddings = np.vstack([self.numpy_embeddings, vector_expanded])
        else:
            self.numpy_embeddings = vector_expanded

        self.save_cache()

    def search(self, query_vector: np.ndarray, top_k: int = 200) -> List[Tuple[Dict[str, str], float]]:
        """
        Search for top_k similar materials.
        query_vector: normalized 1D embedding array.
        Returns: List of (material_dict, similarity_score)
        """
        if query_vector is None:
            return []
            
        results = []
        
        # Ensure query_vector is 2D for search
        q_vec = np.expand_dims(query_vector, axis=0) if query_vector.ndim == 1 else query_vector

        if self.use_faiss and self.faiss_index is not None:
            try:
                scores, indices = self.faiss_index.search(q_vec, min(top_k, self.faiss_index.ntotal))
                for score, idx in zip(scores[0], indices[0]):
                    if idx < 0 or idx >= len(self.index_to_material):
                        continue
                    results.append((self.index_to_material[idx], float(score)))
                return results
            except Exception as e:
                print(f"Error during FAISS search: {e}. Falling back to Numpy.")
                self.use_faiss = False

        if self.numpy_embeddings is not None:
            try:
                # Compute dot product (since vectors are normalized, this is cosine similarity)
                # numpy_embeddings shape: (N, 384), query_vector shape: (384,)
                q_vec_1d = query_vector.squeeze()
                scores = np.dot(self.numpy_embeddings, q_vec_1d)
                
                # Get top_k indices sorted descending
                top_k = min(top_k, len(scores))
                top_indices = np.argpartition(scores, -top_k)[-top_k:]
                # Sort the partition
                top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]
                
                for idx in top_indices:
                    results.append((self.index_to_material[idx], float(scores[idx])))
                return results
            except Exception as e:
                print(f"Error during Numpy search: {e}")
                
        return []

    def check_db_timestamp_and_build(self, db_path: str, force: bool = False):
        """Check SQLite file modification time and rebuild if cache is outdated."""
        if not self.is_enabled:
            return
            
        timestamp_file = os.path.join(CACHE_DIR, "db_timestamp.txt")
        db_mtime = os.path.getmtime(db_path) if os.path.exists(db_path) else 0
        
        cached_mtime = 0
        if os.path.exists(timestamp_file):
            try:
                with open(timestamp_file, "r") as f:
                    cached_mtime = float(f.read().strip())
            except ValueError:
                pass

        cache_loaded = self.load_cache()
        
        if force or not cache_loaded or db_mtime > cached_mtime:
            print("Rebuilding vector index from SQLite database...")
            # Query all materials from SQLite
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT code, name, specification, category FROM materials")
            rows = cursor.fetchall()
            materials = [dict(r) for r in rows]
            conn.close()
            
            if materials:
                self.build_index(materials)
                # Save timestamp
                try:
                    with open(timestamp_file, "w") as f:
                        f.write(str(db_mtime))
                except Exception as e:
                    print(f"Error saving DB timestamp: {e}")
            else:
                print("Warning: Database is empty. Vector index not built.")
        else:
            print("Vector index is up-to-date. Loaded from cache.")

# Global instance
index_manager = VectorIndexManager()
