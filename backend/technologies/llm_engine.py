# technologies/llm_engine.py
import os
from typing import List
import requests

class LLMEngine:
    """Moteur LLM utilisant Groq API (gratuit) - MODÈLES À JOUR 2026"""
    
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")  # Votre clé Groq (gsk_...)
        
        # ✅ MODÈLES ACTIFS EN FÉVRIER 2026
        # Choix 1 : Llama 3.3 (recommandé - très performant)
        self.model = "llama-3.3-70b-versatile"
        
        # Alternatives si vous préférez :
        # self.model = "llama-3.1-8b-instant"  # Plus rapide, moins puissant
        # self.model = "mixtral-8x7b-instruct-v0.1"  # Bon compromis
        # self.model = "gemma2-9b-it"  # Bon modèle Google
        
        self.base_url = "https://api.groq.com/openai/v1"
        
        print(f"✅ LLM Engine initialisé avec Groq - Modèle: {self.model}")
        
        if not self.api_key:
            print("⚠️  Attention: OPENAI_API_KEY non trouvée")
        else:
            print("🔑 Clé API configurée")
    
    def generate_chat(self, prompt: str, system_message: str = None) -> str:
        """Génère une réponse avec Groq API"""
        try:
            messages = []
            
            # Message système personnalisé
            if system_message:
                messages.append({"role": "system", "content": system_message})
            else:
                messages.append({
                    "role": "system", 
                    "content": "Tu es un assistant IA expert qui répond en français de manière précise et détaillée, basé sur les documents fournis. Cite tes sources quand c'est pertinent et structure ta réponse de façon claire."
                })
            
            # Message utilisateur
            messages.append({"role": "user", "content": prompt})
            
            # Configuration de l'appel API
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            data = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.1,  # Plus bas = plus précis
                "max_tokens": 2048,
                "top_p": 0.9,
                "stream": False
            }
            
            print(f"📤 Appel à Groq API avec modèle {self.model}...")
            
            # Appel à l'API Groq
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=data,
                timeout=30
            )
            
            # Traitement de la réponse
            if response.status_code == 200:
                result = response.json()
                answer = result['choices'][0]['message']['content']
                print(f"✅ Réponse reçue ({len(answer)} caractères)")
                return answer
            else:
                error_msg = f"Erreur API Groq ({response.status_code}): {response.text}"
                print(f"❌ {error_msg}")
                
                # Suggestions en cas d'erreur
                if response.status_code == 401:
                    return "**Erreur d'authentification**\n\nVotre clé API Groq est invalide. Vérifiez votre fichier .env"
                elif response.status_code == 400 and "decommissioned" in response.text:
                    return "**Erreur de modèle**\n\nLe modèle demandé n'est plus disponible. Utilisez 'llama-3.3-70b-versatile' à la place."
                else:
                    return f"**Erreur de génération**\n\n{error_msg}"
                
        except requests.exceptions.Timeout:
            print("❌ Timeout - Délai d'attente dépassé")
            return "**Erreur**\n\nDélai d'attente dépassé. Veuillez réessayer."
        except requests.exceptions.ConnectionError:
            print("❌ Erreur de connexion")
            return "**Erreur**\n\nImpossible de se connecter à l'API Groq. Vérifiez votre connexion internet."
        except Exception as e:
            print(f"❌ Exception inattendue: {e}")
            return f"**Erreur inattendue**\n\n{str(e)}"
    
    def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Génère des embeddings avec sentence-transformers (local)"""
        try:
            # Import conditionnel pour éviter les erreurs si non installé
            from sentence_transformers import SentenceTransformer
            
            print("📦 Chargement du modèle d'embeddings (all-MiniLM-L6-v2)...")
            model = SentenceTransformer('all-MiniLM-L6-v2')
            
            print(f"📊 Génération de {len(texts)} embeddings...")
            embeddings = model.encode(texts, show_progress_bar=False)
            
            print(f"✅ Embeddings générés: {len(embeddings)} vecteurs de dimension {len(embeddings[0])}")
            return embeddings.tolist()
            
        except ImportError:
            print("⚠️ sentence-transformers non installé. Installation suggérée:")
            print("   pip install sentence-transformers")
            # Fallback: embeddings factices
            return [[0.0] * 384 for _ in texts]
            
        except Exception as e:
            print(f"❌ Erreur lors de la génération des embeddings: {e}")
            return [[0.0] * 384 for _ in texts]

    # Méthode de test pour vérifier la connexion
    def test_connection(self):
        """Teste la connexion à l'API Groq"""
        try:
            test_messages = [
                {"role": "system", "content": "Réponds en un mot."},
                {"role": "user", "content": "Dis bonjour"}
            ]
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            data = {
                "model": self.model,
                "messages": test_messages,
                "max_tokens": 10
            }
            
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=data,
                timeout=10
            )
            
            if response.status_code == 200:
                return True, "Connexion OK"
            else:
                return False, f"Erreur {response.status_code}: {response.text}"
                
        except Exception as e:
            return False, str(e)