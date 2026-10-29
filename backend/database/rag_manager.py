"""
database/rag_manager.py
Gestionnaire RAG complet avec FAISS
- Stockage des embeddings
- Recherche vectorielle
- Indexation automatique des datasets réels (Enron, CUAD, Support, MultiWOZ)
"""
import os
import sys
import json
import pickle
import hashlib
import numpy as np
import logging
from typing import List, Dict, Any, Optional
from tqdm import tqdm

# Ajouter le chemin parent
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from config import Config

# Import des modèles d'embedding
try:
    from sentence_transformers import SentenceTransformer
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False
    print("⚠️  sentence-transformers non installé")

try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    print("⚠️  faiss-cpu non installé")

logger = logging.getLogger(__name__)


# ===================================================================
# GESTIONNAIRE RAG PRINCIPAL
# ===================================================================
class RAGManager:
    """
    Gestionnaire RAG complet avec FAISS
    - Indexation automatique des datasets (Enron, CUAD, Support, MultiWOZ)
    - Recherche vectorielle pour le contexte
    - Sauvegarde persistante
    """

    def __init__(self):
        if not EMBEDDING_AVAILABLE:
            raise ImportError("sentence-transformers est requis")
        if not FAISS_AVAILABLE:
            raise ImportError("faiss-cpu est requis")

        # Modèle d'embedding (384 dimensions pour all-MiniLM-L6-v2)
        print("📦 Chargement du modèle d'embeddings...")
        self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
        self.embedding_dim = 384

        # Dossier de stockage
        self.store_path = Config.VECTOR_STORE_PATH
        os.makedirs(self.store_path, exist_ok=True)

        # Index et documents par collection
        self.indexes = {}
        self.documents = {}

        # Charger toutes les collections existantes
        self._load_all_collections()

        print("✅ RAG Manager initialisé")

    def _load_all_collections(self):
        """Charge toutes les collections existantes"""
        collections = ['enron', 'cuad', 'support', 'multiwoz', 'contrats', 'factures']
        for col in collections:
            self._load_collection(col)

    def _load_collection(self, collection_name: str):
        """Charge une collection spécifique"""
        index_file = os.path.join(self.store_path, f"{collection_name}_index.faiss")
        doc_file = os.path.join(self.store_path, f"{collection_name}_docs.pkl")

        if os.path.exists(index_file) and os.path.exists(doc_file):
            try:
                self.indexes[collection_name] = faiss.read_index(index_file)
                with open(doc_file, 'rb') as f:
                    self.documents[collection_name] = pickle.load(f)
                logger.info(f"✅ Collection chargée: {collection_name} ({len(self.documents[collection_name])} docs)")
            except Exception as e:
                logger.error(f"❌ Erreur chargement {collection_name}: {e}")
                self.indexes[collection_name] = faiss.IndexFlatL2(self.embedding_dim)
                self.documents[collection_name] = []
        else:
            self.indexes[collection_name] = faiss.IndexFlatL2(self.embedding_dim)
            self.documents[collection_name] = []

    def index_dataset(self, collection_name: str, documents: List[Dict], texts: List[str]):
        """Indexe un dataset dans FAISS"""
        if not texts:
            logger.warning(f"Aucun document à indexer pour {collection_name}")
            return

        print(f"📥 Indexation de {len(texts)} documents dans {collection_name}...")

        # Générer les embeddings
        embeddings = self.embedding_model.encode(texts, show_progress_bar=True)

        # Ajouter à l'index
        self.indexes[collection_name].add(embeddings.astype('float32'))

        # Sauvegarder les documents
        for i, doc in enumerate(documents):
            doc['embedding'] = embeddings[i].tolist()
            self.documents[collection_name].append(doc)

        # Sauvegarder sur disque
        self._save_collection(collection_name)

        print(f"✅ {len(texts)} documents indexés dans {collection_name}")

    def search(self, query: str, collection: str = None, n_results: int = 5) -> List[Dict]:
        """
        Recherche vectorielle dans une ou toutes les collections
        collection: None pour chercher dans toutes, ou nom spécifique
        """
        query_embedding = self.embedding_model.encode([query])

        all_results = []

        # Déterminer les collections à chercher
        collections = [collection] if collection else list(self.documents.keys())

        for col in collections:
            if col not in self.documents or not self.documents[col]:
                continue

            # Rechercher dans l'index
            distances, indices = self.indexes[col].search(
                query_embedding.astype('float32'),
                min(n_results, len(self.documents[col]))
            )

            for i, idx in enumerate(indices[0]):
                if idx >= 0 and idx < len(self.documents[col]):
                    doc = self.documents[col][idx].copy()
                    doc['distance'] = float(distances[0][i])
                    doc['collection'] = col
                    all_results.append(doc)

        # Trier par distance (plus proche d'abord)
        all_results.sort(key=lambda x: x.get('distance', 999))
        return all_results[:n_results]

    def get_context_for_query(self, query: str, n_results: int = 3) -> str:
        """Génère un contexte à partir des documents similaires"""
        results = self.search(query, n_results=n_results)

        if not results:
            return "Aucun document similaire trouvé dans les datasets."

        context = "DOCUMENTS PERTINENTS TROUVÉS:\n\n"
        for i, doc in enumerate(results):
            context += f"--- Document {i+1} (Collection: {doc.get('collection', 'inconnu')}) ---\n"
            context += f"Source: {doc.get('source', doc.get('title', 'Document'))}\n"
            context += f"Type: {doc.get('type', 'document')}\n"
            if doc.get('subject'):
                context += f"Sujet: {doc.get('subject', '')}\n"
            if doc.get('title'):
                context += f"Titre: {doc.get('title', '')}\n"
            context += f"Contenu: {doc.get('text', doc.get('content', ''))[:500]}...\n"
            if doc.get('distance'):
                context += f"Pertinence: {1 - doc['distance']:.2f}\n"
            context += "\n"

        return context

    def _save_collection(self, collection_name: str):
        """Sauvegarde une collection"""
        index_file = os.path.join(self.store_path, f"{collection_name}_index.faiss")
        doc_file = os.path.join(self.store_path, f"{collection_name}_docs.pkl")

        faiss.write_index(self.indexes[collection_name], index_file)
        with open(doc_file, 'wb') as f:
            pickle.dump(self.documents[collection_name], f)

    def get_stats(self) -> Dict:
        """Statistiques des collections"""
        stats = {}
        for col, docs in self.documents.items():
            stats[col] = {
                'documents': len(docs),
                'index_size': self.indexes[col].ntotal
            }
        return stats

    def clear_collection(self, collection_name: str):
        """Vide une collection"""
        self.indexes[collection_name] = faiss.IndexFlatL2(self.embedding_dim)
        self.documents[collection_name] = []
        self._save_collection(collection_name)
        print(f"🗑️  Collection {collection_name} vidée")


# ===================================================================
# CHARGEUR DE DATASETS RÉELS
# ===================================================================
class DatasetLoader:
    """Charge les VRAIS datasets pour RAG"""

    @staticmethod
    def load_enron(limit: int = 3000) -> tuple:
        """Charge les emails Enron"""
        import pandas as pd
        dataset_path = Config.ENRON_PATH
        csv_file = os.path.join(dataset_path, "emails.csv")

        documents = []
        texts = []

        if os.path.exists(csv_file):
            try:
                print(f"📊 Chargement Enron ({limit} emails)...")
                df = pd.read_csv(csv_file, nrows=limit, encoding='latin-1')
                for _, row in df.iterrows():
                    content = ""
                    for col in ['message', 'content', 'body', 'text']:
                        if col in row and pd.notna(row[col]):
                            content = str(row[col])
                            break
                    if len(content) > 100:
                        documents.append({
                            'source': 'enron',
                            'type': 'email',
                            'subject': str(row.get('subject', '')),
                            'from': str(row.get('from', '')),
                            'text': content[:5000]
                        })
                        texts.append(content[:5000])
                print(f"✅ {len(documents)} emails Enron chargés")
            except Exception as e:
                print(f"⚠️ Erreur Enron: {e}")
        else:
            print(f"⚠️ Fichier Enron non trouvé: {csv_file}")

        return documents, texts

    @staticmethod
    def load_cuad(limit: int = 500) -> tuple:
        """Charge les contrats CUAD"""
        import json
        dataset_path = Config.CUAD_PATH

        documents = []
        texts = []

        if os.path.exists(dataset_path):
            try:
                print(f"📊 Chargement CUAD...")
                # Chercher les fichiers JSON
                for file in os.listdir(dataset_path):
                    if file.endswith('.json') and 'readme' not in file.lower():
                        with open(os.path.join(dataset_path, file), 'r', encoding='utf-8') as f:
                            data = json.load(f)
                            if isinstance(data, list):
                                for contract in data[:limit]:
                                    text = contract.get('text', contract.get('content', ''))
                                    if len(text) > 200:
                                        documents.append({
                                            'source': 'cuad',
                                            'type': 'contract',
                                            'title': contract.get('title', 'Contract'),
                                            'parties': contract.get('parties', []),
                                            'text': text[:8000]
                                        })
                                        texts.append(text[:8000])
                print(f"✅ {len(documents)} contrats CUAD chargés")
            except Exception as e:
                print(f"⚠️ Erreur CUAD: {e}")
        else:
            print(f"⚠️ Dossier CUAD non trouvé: {dataset_path}")
            # Générer des données de test
            print("   🔧 Génération de données de test pour CUAD...")
            for i in range(200):
                documents.append({
                    'source': 'cuad',
                    'type': 'contract',
                    'title': f'Contract_{i}',
                    'text': f"Contract {i}: This is a sample contract agreement with terms including confidentiality, payment, and termination clauses."
                })
                texts.append(f"Contract {i}: This is a sample contract agreement...")

        return documents, texts

    @staticmethod
    def load_customer_support(limit: int = 1500) -> tuple:
        """Charge les tickets support"""
        import pandas as pd
        dataset_path = Config.SUPPORT_PATH
        csv_file = os.path.join(dataset_path, "customer_support_tickets.csv")

        documents = []
        texts = []

        if os.path.exists(csv_file):
            try:
                print(f"📊 Chargement Support ({limit} tickets)...")
                df = pd.read_csv(csv_file, nrows=limit)
                for _, row in df.iterrows():
                    content = str(row.get('description', row.get('text', row.get('body', ''))))
                    if len(content) > 100:
                        documents.append({
                            'source': 'support',
                            'type': 'ticket',
                            'title': str(row.get('title', row.get('subject', ''))),
                            'text': content[:5000]
                        })
                        texts.append(content[:5000])
                print(f"✅ {len(documents)} tickets support chargés")
            except Exception as e:
                print(f"⚠️ Erreur Support: {e}")
        else:
            print(f"⚠️ Fichier Support non trouvé: {csv_file}")

        return documents, texts

    @staticmethod
    def load_multiwoz(limit: int = 500) -> tuple:
        """Charge les dialogues MultiWOZ"""
        import json
        dataset_path = Config.MULTIWOZ_PATH

        documents = []
        texts = []
        count = 0

        if os.path.exists(dataset_path):
            try:
                print(f"📊 Chargement MultiWOZ...")
                for root, dirs, files in os.walk(dataset_path):
                    for file in files:
                        if file.endswith('.json') and count < limit:
                            with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                                data = json.load(f)
                                if isinstance(data, list):
                                    for dialogue in data:
                                        if 'log' in dialogue:
                                            for turn in dialogue['log']:
                                                if 'text' in turn and turn['text'] and count < limit:
                                                    text = turn['text'][:2000]
                                                    documents.append({
                                                        'source': 'multiwoz',
                                                        'type': 'dialogue',
                                                        'speaker': turn.get('speaker', ''),
                                                        'text': text
                                                    })
                                                    texts.append(text)
                                                    count += 1
                print(f"✅ {len(documents)} dialogues MultiWOZ chargés")
            except Exception as e:
                print(f"⚠️ Erreur MultiWOZ: {e}")
        else:
            print(f"⚠️ Dossier MultiWOZ non trouvé: {dataset_path}")

        return documents, texts

    @staticmethod
    def load_all_datasets() -> Dict[str, tuple]:
        """Charge tous les datasets"""
        print("\n" + "="*60)
        print("📊 CHARGEMENT DE TOUS LES DATASETS")
        print("="*60)

        datasets = {
            'enron': DatasetLoader.load_enron(limit=3000),
            'cuad': DatasetLoader.load_cuad(limit=300),
            'support': DatasetLoader.load_customer_support(limit=1500),
            'multiwoz': DatasetLoader.load_multiwoz(limit=300)
        }

        total = 0
        for name, (docs, _) in datasets.items():
            total += len(docs)
            print(f"   {name.upper():15} {len(docs):5} documents")

        print(f"   {'TOTAL':15} {total:5} documents")
        print("="*60 + "\n")

        return datasets
