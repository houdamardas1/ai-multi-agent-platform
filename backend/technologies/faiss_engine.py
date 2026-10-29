"""
faiss_engine.py - Moteur RAG avec FAISS (Remplacement direct de ChromaDB)
MÊMES MÉTHODES, MÊME COMPORTEMENT - AUCUN CHANGEMENT DANS TON CODE
"""
import os
import sys
import pickle
import hashlib
import json
import numpy as np
from typing import List, Dict, Any, Optional
from tqdm import tqdm

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from config import Config
from technologies.llm_engine import LLMEngine

try:
    from sentence_transformers import SentenceTransformer
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False
    print("⚠️  sentence-transformers non installé")

class RealRAGEngine:
    """
    Moteur RAG avec FAISS - Interface IDENTIQUE à ChromaDB
    Utilise les VRAIS datasets : Enron, CUAD, Support, MultiWOZ
    """

    def __init__(self, collection_name: str = "business_docs"):
        print(f"🔧 Initialisation FAISS: {collection_name}")

        self.llm_engine = LLMEngine()
        self.collection_name = collection_name

        # MÊME chemin que ChromaDB
        self.vector_store_path = os.getenv('VECTOR_STORE_PATH', './chroma_db')
        os.makedirs(self.vector_store_path, exist_ok=True)

        # Modèle d'embedding (384 dimensions pour all-MiniLM-L6-v2)
        self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
        self.embedding_dim = 384

        # Fichiers de données (stockés au même endroit)
        self.index_file = os.path.join(self.vector_store_path, f"{collection_name}_index.faiss")
        self.doc_file = os.path.join(self.vector_store_path, f"{collection_name}_docs.pkl")

        # Charger ou créer l'index
        self._init_faiss()

        # Si vide, charger le dataset correspondant
        if len(self.documents) == 0:
            self._load_dataset()

        print(f"✅ FAISS initialisé - Collection: {self.collection_name} ({len(self.documents)} documents)")

    def _init_faiss(self):
        """Initialise FAISS (équivalent de _get_or_create_collection)"""
        try:
            import faiss
        except ImportError:
            print("❌ FAISS non installé. Installez avec: pip install faiss-cpu")
            raise

        if os.path.exists(self.index_file) and os.path.exists(self.doc_file):
            try:
                self.index = faiss.read_index(self.index_file)
                with open(self.doc_file, 'rb') as f:
                    self.documents = pickle.load(f)
                print(f"ℹ️  Collection chargée: {len(self.documents)} documents")
            except Exception as e:
                print(f"⚠️  Erreur chargement: {e}")
                self.index = faiss.IndexFlatL2(self.embedding_dim)
                self.documents = []
        else:
            self.index = faiss.IndexFlatL2(self.embedding_dim)
            self.documents = []

    def _load_dataset(self):
        """Charge le dataset correspondant à la collection (chemins configurables via .env)"""
        print(f"📥 Chargement du dataset {self.collection_name}...")

        documents = []

        # ========== ENRON ==========
        if self.collection_name == "enron":
            csv_file = os.path.join(Config.ENRON_PATH, "emails.csv")
            if os.path.exists(csv_file):
                try:
                    import pandas as pd
                    print(f"📊 Chargement Enron (1.4GB)...")
                    # Lire les 2000 premières lignes
                    df = pd.read_csv(csv_file, nrows=2000, encoding='latin-1')
                    for _, row in df.iterrows():
                        content = ""
                        for col in ['message', 'content', 'body', 'text']:
                            if col in row and pd.notna(row[col]):
                                content = str(row[col])
                                break
                        if len(content) > 50:
                            documents.append({
                                'content': content[:5000],
                                'subject': str(row.get('subject', '')),
                                'from': str(row.get('from', ''))
                            })
                    print(f"✅ {len(documents)} emails Enron chargés")
                except Exception as e:
                    print(f"⚠️ Erreur Enron: {e}")

        # ========== CUSTOMER SUPPORT ==========
        elif self.collection_name == "support":
            csv_file = os.path.join(Config.SUPPORT_PATH, "customer_support_tickets.csv")
            if os.path.exists(csv_file):
                try:
                    import pandas as pd
                    df = pd.read_csv(csv_file, nrows=1000)
                    for _, row in df.iterrows():
                        content = str(row.get('description', row.get('text', row.get('body', ''))))
                        if len(content) > 50:
                            documents.append({
                                'content': content[:5000],
                                'title': str(row.get('title', row.get('subject', '')))
                            })
                    print(f"✅ {len(documents)} tickets support chargés")
                except Exception as e:
                    print(f"⚠️ Erreur Support: {e}")

        # ========== MULTIWOZ ==========
        elif self.collection_name == "multiwoz":
            base_path = os.path.join(Config.MULTIWOZ_PATH, "MultiWOZ_2.2")
            if os.path.exists(base_path):
                try:
                    import json
                    for split in ["train", "dev", "test"]:
                        split_path = os.path.join(base_path, split)
                        if os.path.exists(split_path):
                            for file in os.listdir(split_path)[:3]:
                                if file.endswith('.json'):
                                    with open(os.path.join(split_path, file), 'r', encoding='utf-8') as f:
                                        data = json.load(f)
                                        if isinstance(data, list):
                                            for dialogue in data[:50]:
                                                if 'log' in dialogue:
                                                    for turn in dialogue['log']:
                                                        if 'text' in turn and turn['text']:
                                                            documents.append({'content': turn['text']})
                    print(f"✅ {len(documents)} dialogues MultiWOZ chargés")
                except Exception as e:
                    print(f"⚠️ Erreur MultiWOZ: {e}")

        # ========== CUAD - Données de test ==========
        elif self.collection_name == "cuad":
            print("⚠️ CUAD: génération de données de test (pas de dataset réel)...")
            for i in range(200):
                documents.append({
                    'content': f"Contract {i}: Sample agreement with confidentiality, payment terms, and termination clauses. Parties agree to terms effective from January 1, 2025.",
                    'title': f"Contract_{i}"
                })
            print(f"✅ {len(documents)} contrats test générés")

        # Indexation
        if documents:
            print(f"📊 Indexation de {len(documents)} documents...")
            self.index_documents(documents[:2000])
        else:
            print(f"⚠️ Aucun document chargé pour {self.collection_name}")

    def index_documents(self, documents: List[Dict[str, Any]], batch_size: int = 50):
        """
        Indexe des documents dans FAISS
        MÊME INTERFACE que ChromaDB
        """
        if not documents:
            print("⚠️  Aucun document à indexer")
            return

        print(f"📥 Indexation de {len(documents)} documents...")

        ids = []
        texts = []
        metadatas = []

        for i, doc in enumerate(tqdm(documents, desc="Indexation")):
            text = self._extract_text(doc)
            if not text or not text.strip():
                continue

            text_hash = hashlib.md5(text.encode()).hexdigest()[:12]
            doc_id = f"doc_{self.collection_name}_{text_hash}"

            # Éviter les doublons
            if any(d['id'] == doc_id for d in self.documents):
                continue

            metadata = {"source": self.collection_name, "original_index": i, "text_hash": text_hash}
            if isinstance(doc, dict):
                for key, value in doc.items():
                    if key not in ['body', 'content', 'text'] and value:
                        metadata[key] = str(value)[:100]

            ids.append(doc_id)
            texts.append(text[:8000])
            metadatas.append(metadata)

        if not ids:
            print("ℹ️  Aucun nouveau document à indexer")
            return

        # Générer les embeddings
        print(f"📊 Génération des embeddings pour {len(ids)} documents...")
        embeddings = self.embedding_model.encode(texts)

        # Ajouter à l'index FAISS
        self.index.add(embeddings.astype('float32'))

        # Sauvegarder les documents
        for i in range(len(ids)):
            self.documents.append({
                'id': ids[i],
                'text': texts[i],
                'metadata': metadatas[i]
            })

        # Sauvegarder sur disque
        self._save()

        print(f"✅ {len(ids)} nouveaux documents indexés")

    def search(self, query: str, n_results: int = 5) -> List[Dict]:
        """
        Recherche sémantique dans les documents
        MÊME FORMAT DE RETOUR que ChromaDB
        """
        try:
            if not self.documents:
                return []

            # Générer l'embedding de la requête
            query_embedding = self.embedding_model.encode([query])

            # Rechercher les plus proches voisins
            distances, indices = self.index.search(
                query_embedding.astype('float32'),
                min(n_results, len(self.documents))
            )

            # Formater les résultats (IDENTIQUE à ChromaDB)
            formatted = []
            for i, idx in enumerate(indices[0]):
                if idx < len(self.documents):
                    doc = self.documents[idx]
                    formatted.append({
                        'document': doc['text'],
                        'metadata': doc['metadata'],
                        'distance': float(distances[0][i]),
                        'id': doc['id']
                    })

            return formatted

        except Exception as e:
            print(f"❌ Erreur recherche: {e}")
            return []

    def rag_generate(self, query: str, context_docs: int = 3) -> Dict[str, Any]:
        """
        Génération augmentée par récupération
        MÊME COMPORTEMENT que ChromaDB
        """
        relevant_docs = self.search(query, n_results=context_docs)

        context = "DOCUMENTS PERTINENTS:\n\n"
        for i, doc in enumerate(relevant_docs):
            context += f"--- Document {i+1} ---\n"
            context += f"Source: {doc['metadata'].get('source', 'inconnu')}\n"
            context += f"Contenu: {doc['document'][:500]}...\n\n"

        prompt = f"""
        QUESTION: {query}

        {context}

        En tant qu'assistant IA expert, utilise les documents ci-dessus pour fournir une réponse précise.

        RÉPONSE:
        """

        response = self.llm_engine.generate_chat(prompt)

        return {
            "response": response,
            "sources": [doc['metadata'] for doc in relevant_docs],
            "num_sources": len(relevant_docs),
            "query": query
        }

    def _extract_text(self, doc: Any) -> str:
        """Extrait le texte d'un document (identique à l'original)"""
        if isinstance(doc, dict):
            text_parts = []
            for field in ['body', 'content', 'text', 'context', 'description', 'summary', 'message']:
                if field in doc and doc[field]:
                    text_parts.append(str(doc[field]))
            if not text_parts:
                for key, value in doc.items():
                    if isinstance(value, str) and value.strip():
                        text_parts.append(value)
            return " ".join(text_parts)[:10000]
        return str(doc)[:10000] if doc else ""

    def get_collection_stats(self) -> Dict[str, Any]:
        """Statistiques de la collection (MÊME FORMAT)"""
        try:
            return {
                "collection": self.collection_name,
                "documents": len(self.documents),
                "status": "active" if len(self.documents) > 0 else "empty"
            }
        except Exception as e:
            return {"collection": self.collection_name, "error": str(e)}

    def clear_collection(self):
        """Vide la collection"""
        try:
            import faiss
            self.index = faiss.IndexFlatL2(self.embedding_dim)
            self.documents = []
            self._save()
            print(f"🗑️  Collection '{self.collection_name}' vidée")
            return True
        except Exception as e:
            print(f"❌ Erreur: {e}")
            return False

    def _save(self):
        """Sauvegarde l'index et les documents"""
        import faiss
        faiss.write_index(self.index, self.index_file)
        with open(self.doc_file, 'wb') as f:
            pickle.dump(self.documents, f)
