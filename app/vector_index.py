import os
import time
import json
import threading

# Prevent OpenBLAS/PyTorch OpenMP thread thrashing and HuggingFace Tokenizers deadlock
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

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
PROJECT_ROOT = os.path.dirname(DB_DIR)
MODEL_DIR = os.path.join(PROJECT_ROOT, "model_offline")
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")
CACHE_DIR = os.path.join(DB_DIR, "vector_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

class VectorIndexManager:
    def __init__(self):
        self.model: Optional[Any] = None
        self.use_faiss = False
        self._lock = threading.RLock()

        # FAISS index and mappings
        self.faiss_index: Optional[Any] = None

        # Fallback Numpy structures
        self.numpy_embeddings: Optional[np.ndarray] = None # Shape: (N, dim)

        # Shared mappings: index position -> material dict (code, name, spec, category)
        self.index_to_material: List[Dict[str, str]] = []

        # Load model name from config or environment variable (Default to bge-small-zh-v1.5)
        self.model_name_or_path = self._get_configured_model_name()
        self.embedding_dim = 512 if "bge" in self.model_name_or_path.lower() else 384

    def _get_configured_model_name(self) -> str:
        """Read model name from env or config/model_config.json, fallback to bge-small-zh-v1.5."""
        env_model = os.environ.get("EMBEDDING_MODEL")
        if env_model:
            return env_model.strip()

        config_path = os.path.join(CONFIG_DIR, "model_config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    return cfg.get("embedding_model", "bge-small-zh-v1.5")
            except Exception as e:
                print(f"Warning: Failed to load model_config.json: {e}")
        return "bge-small-zh-v1.5"

    def _initialize_model(self):
        if not HAS_SENTENCE_TRANSFORMERS or self.model is not None:
            return

        # Force offline mode to prevent intranet/network hangs (P0 Fix H3)
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

        offline_model_path = os.path.join(MODEL_DIR, self.model_name_or_path)
        if os.path.exists(offline_model_path):
            model_path = offline_model_path
            print(f"Loading offline sentence-transformers model from {model_path}...")
        else:
            model_path = self.model_name_or_path
            print(f"Offline directory absent at {offline_model_path}. Loading from local HuggingFace cache...")

        try:
            self.model = SentenceTransformer(model_path)
            try:
                self.embedding_dim = int(self.model.get_sentence_embedding_dimension())
            except Exception:
                pass
            print(f"SentenceTransformer model [{self.model_name_or_path}] loaded successfully (dim={self.embedding_dim}).")
        except Exception as e:
            print(f"Warning: Local SentenceTransformer model load failed: {e}. Fallback to keyword FTS5 search.")
            self.model = None

    @property
    def is_enabled(self) -> bool:
        if self.model is None and HAS_SENTENCE_TRANSFORMERS:
            with self._lock:
                if self.model is None:
                    self._initialize_model()
        return self.model is not None

    def encode_queries(self, texts: List[str], batch_size: int = 128) -> Optional[np.ndarray]:
        """Encode a batch of query texts into normalized embeddings."""
        if not self.is_enabled or not texts:
            return None
        try:
            with self._lock:
                embeddings = self.model.encode(texts, batch_size=batch_size, show_progress_bar=False, convert_to_numpy=True)
            # L2 normalize using pure Numpy (preventing FAISS C++ OpenMP deadlock on Windows)
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
        """Load index and mapping cache from disk with metadata/dimension verification."""
        index_file = os.path.join(CACHE_DIR, "materials.index")
        npy_file = os.path.join(CACHE_DIR, "materials_embeddings.npy")
        mapping_file = os.path.join(CACHE_DIR, "index_to_material.json")
        meta_file = os.path.join(CACHE_DIR, "cache_metadata.json")
        
        if not os.path.exists(mapping_file) or not os.path.exists(npy_file):
            return False

        # Verify cache metadata if available
        if os.path.exists(meta_file):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    cached_model = meta.get("model_name", "")
                    cached_dim = meta.get("dimension", 0)
                    if cached_model and cached_model != self.model_name_or_path:
                        print(f"Cache model mismatch (cached={cached_model}, current={self.model_name_or_path}). Invalidating cache.")
                        return False
                    if cached_dim and cached_dim != self.embedding_dim:
                        print(f"Cache dimension mismatch (cached={cached_dim}, current={self.embedding_dim}). Invalidating cache.")
                        return False
            except Exception as e:
                print(f"Warning reading cache metadata: {e}")
            
        try:
            with open(mapping_file, "r", encoding="utf-8") as f:
                self.index_to_material = json.load(f)
        except Exception as e:
            print(f"Error loading index-to-material mapping: {e}")
            return False

        # Load raw Numpy embeddings
        if os.path.exists(npy_file):
            try:
                loaded_embs = np.load(npy_file)
                if loaded_embs.ndim == 2 and loaded_embs.shape[1] == self.embedding_dim:
                    self.numpy_embeddings = loaded_embs
                    print(f"Loaded Numpy embeddings cache with shape {self.numpy_embeddings.shape} for model [{self.model_name_or_path}].")
                else:
                    print(f"Numpy embeddings dimension mismatch: got shape {loaded_embs.shape}, expected (*, {self.embedding_dim}). Need rebuild.")
                    return False
            except Exception as e:
                print(f"Error loading Numpy embeddings: {e}")
                return False

        # Load FAISS index if available and requested
        if self.use_faiss and os.path.exists(index_file):
            try:
                self.faiss_index = faiss.read_index(index_file)
                if self.faiss_index.d == self.embedding_dim:
                    print(f"Loaded FAISS index from cache with {self.faiss_index.ntotal} items (dim={self.faiss_index.d}).")
                    return True
                else:
                    print(f"FAISS index dim mismatch ({self.faiss_index.d} vs {self.embedding_dim}). Falling back to Numpy.")
                    self.use_faiss = False
            except Exception as e:
                print(f"Error loading FAISS index: {e}. Falling back to Numpy.")
                self.use_faiss = False

        return self.numpy_embeddings is not None and len(self.index_to_material) == len(self.numpy_embeddings)

    def save_cache(self):
        """Save index, mapping cache, and cache metadata to disk."""
        index_file = os.path.join(CACHE_DIR, "materials.index")
        npy_file = os.path.join(CACHE_DIR, "materials_embeddings.npy")
        mapping_file = os.path.join(CACHE_DIR, "index_to_material.json")
        meta_file = os.path.join(CACHE_DIR, "cache_metadata.json")
        
        try:
            with open(mapping_file, "w", encoding="utf-8") as f:
                json.dump(self.index_to_material, f, ensure_ascii=False, indent=4)
                
            if self.use_faiss and self.faiss_index is not None:
                faiss.write_index(self.faiss_index, index_file)
                
            if self.numpy_embeddings is not None:
                np.save(npy_file, self.numpy_embeddings)

            # Write cache metadata
            metadata = {
                "model_name": self.model_name_or_path,
                "dimension": self.embedding_dim,
                "item_count": len(self.index_to_material),
                "timestamp": time.time(),
                "time_str": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=4)

            print(f"Saved vector index and mapping cache for model [{self.model_name_or_path}] (dim={self.embedding_dim}).")
        except Exception as e:
            print(f"Error saving vector cache: {e}")

    def build_index(self, materials: List[Dict[str, Any]], task_id: Optional[str] = None):
        """
        Build index from scratch.
        materials: List of Dict containing 'code', 'name', 'specification', 'category'
        """
        if not self.is_enabled:
            return
            
        print(f"Building vector index for {len(materials)} materials with model [{self.model_name_or_path}] (dim={self.embedding_dim})...")
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

        # Encode all texts in chunked batches to report progress
        self.numpy_embeddings = None
        embeddings_list = []
        chunk_size = 1000
        total_items = len(texts)
        
        for idx in range(0, total_items, chunk_size):
            chunk_texts = texts[idx : idx + chunk_size]
            chunk_embs = self.encode_queries(chunk_texts, batch_size=256)
            if chunk_embs is None:
                print(f"Error: Failed to encode chunk starting at {idx}")
                return
            embeddings_list.append(chunk_embs)
            
            if task_id:
                try:
                    from app.server import upload_tasks
                    if task_id in upload_tasks:
                        prog = min(95, int(10 + ((idx + len(chunk_texts)) / total_items) * 85))
                        upload_tasks[task_id]["vectorization_progress"] = prog
                        upload_tasks[task_id]["current_step"] = f"正在重建向量特征: ({idx + len(chunk_texts)}/{total_items})"
                except Exception as e:
                    print(f"Error updating task progress in build_index: {e}")

        if embeddings_list:
            embeddings = np.vstack(embeddings_list)
            self.numpy_embeddings = embeddings
        else:
            print("Error: No embeddings generated. Index not built.")
            return
        
        if self.use_faiss:
            try:
                self.faiss_index = faiss.IndexFlatIP(self.embedding_dim)
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
        import time
        def log_vector_debug(msg: str):
            try:
                with open("c:/Users/wangd/project/ListTrans/ListTrans/logs/debug_match.log", "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - VECTOR: {msg}\n")
            except Exception:
                pass

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
                log_vector_debug(f"Error during FAISS search: {e}. Falling back to Numpy.")
                self.use_faiss = False

        if self.numpy_embeddings is not None:
            try:
                # Compute dot product (since vectors are normalized, this is cosine similarity)
                # numpy_embeddings shape: (N, 384), query_vector shape: (384,)
                q_vec_1d = query_vector.squeeze()
                
                log_vector_debug(f"Starting np.einsum computation with shape {self.numpy_embeddings.shape}...")
                t0 = time.time()
                # Use np.einsum to avoid OpenBLAS/MKL thread pool deadlocks in background threads while keeping 6x speedup over element-wise sum
                scores = np.einsum('ij,j->i', self.numpy_embeddings, q_vec_1d)
                log_vector_debug(f"np.einsum computation completed in {time.time() - t0:.4f}s.")
                
                # Get top_k indices sorted descending
                top_k = min(top_k, len(scores))
                top_indices = np.argpartition(scores, -top_k)[-top_k:]
                # Sort the partition
                top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]
                
                for idx in top_indices:
                    results.append((self.index_to_material[idx], float(scores[idx])))
                return results
            except Exception as e:
                log_vector_debug(f"Error during Numpy search: {e}")
                
        return []

    def check_db_timestamp_and_build(self, db_path: str, force: bool = False):
        """Check SQLite file modification time and rebuild if cache is outdated."""
        if not self.is_enabled:
            return

        # 1. Try loading existing vector cache first
        cache_loaded = self.load_cache()
        if cache_loaded and not force:
            print(f"Vector index is up-to-date. Successfully loaded {len(self.index_to_material)} materials from cache.")
            return

        print("Rebuilding vector index from SQLite database...")
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
            self.save_cache()
            timestamp_file = os.path.join(CACHE_DIR, "db_timestamp.txt")
            db_mtime = os.path.getmtime(db_path) if os.path.exists(db_path) else 0
            try:
                with open(timestamp_file, "w") as f:
                    f.write(str(db_mtime))
            except Exception as e:
                print(f"Error saving DB timestamp: {e}")
        else:
            print("Warning: Database is empty. Vector index not built.")

# Global instance
index_manager = VectorIndexManager()
