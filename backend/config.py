"""
config.py - Configuration centralisée (version complète)
"""
import os
from dotenv import load_dotenv

# Charge les variables d'environnement si elles existent
load_dotenv()

# ===================================================================
# CONFIGURATION EMAIL (ta configuration existante)
# ===================================================================
EMAIL_CONFIG = {
    "adresse": os.getenv("GMAIL_ADRESSE", ""),
    "mot_de_passe": os.getenv("GMAIL_MOT_DE_PASSE", ""),  # mot de passe d'application Gmail
    "serveur": "smtp.gmail.com",
    "port": 587,
    "nom_expediteur": "Service Automatique - IA Platform"
}

# EMAILS DES RESPONSABLES
RESPONSABLES = [e.strip() for e in os.getenv("RESPONSABLES", "").split(",") if e.strip()]

# ===================================================================
# CLASSE CONFIG POUR RAG_ENGINE ET AUTRES COMPOSANTS (AJOUT CRITIQUE)
# ===================================================================
class Config:
    # GROQ (depuis .env)
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    
    # GMAIL (depuis .env)
    GMAIL_ADRESSE = EMAIL_CONFIG["adresse"]
    GMAIL_MOT_DE_PASSE = EMAIL_CONFIG["mot_de_passe"]
    GMAIL_IMAP_SERVEUR = "imap.gmail.com"
    GMAIL_IMAP_PORT = 993
    GMAIL_SMTP_SERVEUR = EMAIL_CONFIG["serveur"]
    GMAIL_SMTP_PORT = EMAIL_CONFIG["port"]
    
    # DATASETS (chemins par défaut)
    ENRON_PATH = os.getenv("ENRON_DATASET_PATH", "./datasets/enron")
    CUAD_PATH = os.getenv("CUAD_DATASET_PATH", "./datasets/cuad")
    MULTIWOZ_PATH = os.getenv("MULTIWOZ_DATASET_PATH", "./datasets/multiwoz")
    SUPPORT_PATH = os.getenv("SUPPORT_DATASET_PATH", "./datasets/customer_support")
    
    # BASE DE DONNÉES
    DB_PATH = os.getenv("DATABASE_PATH", "./data/clients.db")
    
    # VECTOR STORE (important pour rag_engine.py)
    VECTOR_STORE_PATH = os.getenv("VECTOR_STORE_PATH", "./chroma_db")
    
    # AUTOMATISATION
    ENABLE_EMAIL_SURVEILLANCE = os.getenv("ENABLE_EMAIL_SURVEILLANCE", "true").lower() == "true"
    ENABLE_RULES_ENGINE = os.getenv("ENABLE_RULES_ENGINE", "true").lower() == "true"
    
    # API
    API_HOST = os.getenv("API_HOST", "0.0.0.0")
    API_PORT = int(os.getenv("API_PORT", 8000))
    DEBUG = os.getenv("DEBUG", "True").lower() == "true"
    
    @classmethod
    def validate(cls):
        """Valide la configuration"""
        errors = []
        
        if not cls.GROQ_API_KEY:
            errors.append("GROQ_API_KEY manquante")
        
        if not cls.GMAIL_ADRESSE or not cls.GMAIL_MOT_DE_PASSE:
            errors.append("Configuration Gmail manquante")
        
        if errors:
            for e in errors:
                print(e)
            return False
        
        print("Configuration valide")
        return True

# Instance pour faciliter l'accès
config = Config()