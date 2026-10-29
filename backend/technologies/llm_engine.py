# technologies/llm_engine.py
import os
from typing import List, Dict
import requests
import json
import re

class LLMEngine:
    """Moteur LLM utilisant Groq API (gratuit) - MODÈLES À JOUR 2026"""

    def __init__(self):
        self.api_key = os.getenv("GROQ_API_KEY")

        # ✅ MODÈLES ACTIFS EN FÉVRIER 2026
        self.model = "llama-3.3-70b-versatile"
        self.base_url = "https://api.groq.com/openai/v1"

        print(f"✅ LLM Engine initialisé avec Groq - Modèle: {self.model}")

        if not self.api_key:
            print("⚠️  Attention: GROQ_API_KEY non trouvée")
        else:
            print("🔑 Clé API configurée")

    def generate_chat(self, prompt: str, system_message: str = None) -> str:
        """Génère une réponse avec Groq API"""
        try:
            messages = []

            if system_message:
                messages.append({"role": "system", "content": system_message})
            else:
                messages.append({
                    "role": "system",
                    "content": "Tu es un assistant IA expert qui répond en français de manière précise et détaillée, basé sur les documents fournis."
                })

            messages.append({"role": "user", "content": prompt})

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            data = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 2048,
                "top_p": 0.9,
                "stream": False
            }

            print(f"📤 Appel à Groq API avec modèle {self.model}...")

            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=data,
                timeout=30
            )

            if response.status_code == 200:
                result = response.json()
                answer = result['choices'][0]['message']['content']
                print(f"✅ Réponse reçue ({len(answer)} caractères)")
                return answer
            else:
                error_msg = f"Erreur API Groq ({response.status_code}): {response.text}"
                print(f"❌ {error_msg}")

                if response.status_code == 401:
                    return "**Erreur d'authentification**\n\nVotre clé API Groq est invalide."
                else:
                    return f"**Erreur de génération**\n\n{error_msg}"

        except requests.exceptions.Timeout:
            print("❌ Timeout")
            return "**Erreur**\n\nDélai d'attente dépassé."
        except requests.exceptions.ConnectionError:
            print("❌ Erreur de connexion")
            return "**Erreur**\n\nImpossible de se connecter à l'API Groq."
        except Exception as e:
            print(f"❌ Exception: {e}")
            return f"**Erreur inattendue**\n\n{str(e)}"

    def comprendre_intention(self, texte: str) -> Dict:
        """
        Analyse l'intention d'un message
        """
        try:
            prompt = f"""Analyse le message suivant et retourne UNIQUEMENT un JSON avec:
- intention: (support|facture|contrat|resiliation|general|question|reclamation)
- sentiment: (positif|negatif|neutre)
- urgence: (1-5, 1=faible, 5=urgent)

Message: {texte[:1000]}

Réponse JSON:"""

            response = self.generate_chat(prompt)

            # Essayer de parser le JSON
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
            else:
                result = {}

            return {
                "intention": result.get("intention", "general"),
                "sentiment": result.get("sentiment", "neutre"),
                "urgence": result.get("urgence", 3),
                "entities": result.get("entities", {}),
                "confidence": 0.8
            }

        except Exception as e:
            print(f"❌ Erreur compréhension: {e}")
            return {
                "intention": "general",
                "sentiment": "neutre",
                "urgence": 3,
                "entities": {},
                "confidence": 0.5
            }

    def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Génère des embeddings avec sentence-transformers (local)"""
        try:
            from sentence_transformers import SentenceTransformer

            print("📦 Chargement du modèle d'embeddings...")
            model = SentenceTransformer('all-MiniLM-L6-v2')

            print(f"📊 Génération de {len(texts)} embeddings...")
            embeddings = model.encode(texts, show_progress_bar=False)

            return embeddings.tolist()

        except ImportError:
            print("⚠️ sentence-transformers non installé")
            return [[0.0] * 384 for _ in texts]
        except Exception as e:
            print(f"❌ Erreur embeddings: {e}")
            return [[0.0] * 384 for _ in texts]

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

    def generer_reponse_personnalisee(self, client: Dict, intention: str, contexte: Dict) -> str:
        """Génère une réponse personnalisée pour le client"""
        try:
            nom_client = client.get('nom', 'Client')

            # Prompt système personnalisé (cas du chatbot)
            system_prompt = contexte.get('system_prompt', None)
            user_message = contexte.get('user_message', '')

            if system_prompt and user_message:
                print("📝 Utilisation du system_prompt personnalisé pour le chatbot")
                response = self.generate_chat(
                    prompt=user_message,
                    system_message=system_prompt
                )
                return response

            # Sinon, comportement par défaut pour les emails
            prompt = f"""
Tu es un assistant client expert. Rédige une réponse professionnelle et personnalisée.

INFORMATIONS CLIENT:
- Nom: {nom_client}
- Email: {client.get('email', '')}

INTENTION DÉTECTÉE: {intention}

CONTEXTE (documents similaires trouvés):
{json.dumps(contexte, ensure_ascii=False, indent=2)[:500]}

INSTRUCTIONS:
1. Sois courtois et professionnel
2. Adresse-toi au client par son prénom
3. Propose une solution adaptée à l'intention détectée
4. Termine par une formule de politesse

RÉPONSE:
"""
            response = self.generate_chat(prompt)
            return response

        except Exception as e:
            print(f"❌ Erreur génération réponse: {e}")
            return f"Bonjour {client.get('nom', 'Client')},\n\nNous avons bien reçu votre message concernant {intention}. Notre équipe va traiter votre demande dans les plus brefs délais.\n\nCordialement,\nService Client Automatique"
