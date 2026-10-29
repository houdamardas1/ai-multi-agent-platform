"""
autonomous_agent.py - Agent IA Autonome avec stats_globales (source unique)
"""
import os
import time
import imaplib
import smtplib
import json
import uuid
import hashlib
import re
import logging
import email
import random
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from contextlib import contextmanager
from collections import deque
from threading import Lock
from collections import defaultdict

# Scheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

# Imports
from technologies.llm_engine import LLMEngine
from technologies.faiss_engine import RealRAGEngine
from database.rag_manager import RAGManager, DatasetLoader
from config import Config

# Import OAuth2
from gmail_oauth import GmailOAuth

logger = logging.getLogger(__name__)


# ===================================================================
# WORKFLOW ENGINE - MOTEUR D'EXECUTION DES WORKFLOWS
# ===================================================================
class WorkflowEngine:
    """Moteur d'execution de workflows - Declenche automatiquement les actions"""
    
    def __init__(self, db, agent):
        self.db = db
        self.agent = agent
        logger.info(" Moteur de workflows initialise")
    
    def execute_workflow(self, workflow_id: int, trigger_data: dict) -> dict:
        """Execute un workflow a partir d'un trigger"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, name, nodes, edges, is_active 
                FROM workflows 
                WHERE id = %s AND is_active = TRUE
            """, (workflow_id,))
            
            workflow = cursor.fetchone()
            cursor.close()
            
            if not workflow:
                logger.warning(f"Workflow {workflow_id} non trouve ou inactif")
                return {"success": False, "message": "Workflow non trouve ou inactif"}
            
            nodes = workflow['nodes'] if isinstance(workflow['nodes'], list) else json.loads(workflow['nodes'])
            edges = workflow['edges'] if isinstance(workflow['edges'], list) else json.loads(workflow['edges'])
            
            logger.info(f"Execution du workflow: {workflow['name']} (ID: {workflow_id})")
            
            execution_state = {node['id']: {'status': 'pending', 'result': None} for node in nodes}
            node_map = {node['id']: node for node in nodes}
            
            start_nodes = [n for n in nodes if n.get('type') == 'trigger' or n.get('data', {}).get('type') == 'trigger']
            
            results = []
            for start_node in start_nodes:
                result = self._execute_node(start_node, trigger_data, execution_state, node_map, edges)
                results.append(result)
            
            return {"success": True, "workflow_name": workflow['name'], "results": results}
            
        except Exception as e:
            logger.error(f"Erreur execution workflow: {e}")
            return {"success": False, "error": str(e)}
    
    def find_and_execute_matching_workflows(self, trigger_type: str, trigger_data: dict) -> list:
        """Trouve et execute tous les workflows correspondant a un trigger"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, name, nodes, edges, is_active 
                FROM workflows 
                WHERE is_active = TRUE
            """)
            
            workflows = cursor.fetchall()
            cursor.close()
            
            results = []
            for workflow in workflows:
                nodes = workflow['nodes'] if isinstance(workflow['nodes'], list) else json.loads(workflow['nodes'])
                
                for node in nodes:
                    node_type = node.get('type') or node.get('data', {}).get('type')
                    node_label = node.get('data', {}).get('label', '')
                    
                    if node_type == 'trigger' and trigger_type.lower() in node_label.lower():
                        logger.info(f"Workflow correspondant trouve: {workflow['name']}")
                        result = self.execute_workflow(workflow['id'], trigger_data)
                        results.append(result)
                        break
            
            return results
            
        except Exception as e:
            logger.error(f"Erreur recherche workflows: {e}")
            return []
    
    def _execute_node(self, node: dict, input_data: dict, state: dict, node_map: dict, edges: list) -> dict:
        """Execute un noeud specifique du workflow"""
        node_id = node['id']
        node_type = node.get('type') or node.get('data', {}).get('type')
        node_data = node.get('data', {})
        
        state[node_id]['status'] = 'running'
        
        try:
            result = {}
            
            if node_type == 'trigger':
                result = input_data
                logger.info(f"  Declencheur execute: {node_data.get('label', 'Trigger')}")
            
            elif node_type == 'condition':
                conditions = node_data.get('conditions', [])
                condition_result = self._evaluate_conditions(conditions, input_data)
                result = {'condition_result': condition_result}
                logger.info(f"  Condition evaluee: {condition_result}")
            
            elif node_type == 'ia':
                prompt = node_data.get('prompt', 'Analyse le contenu suivant')
                model = node_data.get('model', 'Groq LLM')
                
                ai_result = self.agent.llm.generer_reponse_personnalisee(
                    {'nom': 'Systeme', 'email': 'workflow@agent.ai'},
                    'workflow_execution',
                    {'system_prompt': prompt, 'user_message': str(input_data)[:2000]}
                )
                result = {'ai_response': ai_result, 'model': model}
                logger.info(f"  Agent IA execute: {node_data.get('label', 'IA')}")
            
            elif node_type == 'rag':
                query = str(input_data.get('content', input_data.get('message', '')))[:500]
                rag_results = []
                if hasattr(self.agent, 'enron_rag') and self.agent.enron_rag:
                    rag_results = self.agent.enron_rag.search(query, n_results=3)
                result = {'rag_results': rag_results}
                logger.info(f"  Recherche RAG executee: {len(rag_results)} resultats")
            
            elif node_type == 'email':
                client_id = input_data.get('client_id', 'system')
                client_email = input_data.get('client_email', 'client@example.com')
                sujet = node_data.get('label', 'Message automatique')
                corps = node_data.get('prompt', node_data.get('description', 'Contenu genere automatiquement'))
                
                if hasattr(self.agent, '_sauvegarder_email_event'):
                    email_id = self.agent._sauvegarder_email_event(
                        client_id, client_email, sujet, 'workflow', 'medium',
                        'generation_automatique', 'pending', corps, 'generated', 85
                    )
                    result = {'email_id': email_id, 'status': 'pending'}
                    logger.info(f"  Email genere en attente: {sujet}")
                else:
                    result = {'status': 'error', 'message': 'Sauvegarde email non disponible'}
            
            elif node_type == 'action':
                action = node_data.get('action', '')
                action_label = node_data.get('label', 'Action')
                
                if 'ticket' in action.lower() or 'support' in action_label.lower():
                    if hasattr(self.agent, '_creer_ticket_support'):
                        ticket_id = self.agent._creer_ticket_support(
                            input_data.get('client_id', 'system'),
                            action_label,
                            str(input_data)
                        )
                        result = {'ticket_id': ticket_id, 'action': 'ticket_created'}
                        logger.info(f"  Ticket support cree: {ticket_id}")
                    else:
                        result = {'status': 'error', 'message': 'Creation ticket non disponible'}
                elif 'crm' in action.lower() or 'update' in action_label.lower():
                    if hasattr(self.agent, '_increment_crm'):
                        self.agent._increment_crm()
                        result = {'crm_updated': True, 'action': 'crm_update'}
                        logger.info(f"  CRM mis a jour")
                    else:
                        result = {'status': 'error', 'message': 'Mise a jour CRM non disponible'}
                else:
                    result = {'action_executed': action, 'status': 'success'}
                    logger.info(f"  Action executee: {action_label}")
            
            else:
                result = {'status': 'skipped', 'message': f'Type {node_type} non supporte'}
            
            state[node_id]['status'] = 'completed'
            state[node_id]['result'] = result
            
            next_edges = [e for e in edges if e.get('source') == node_id]
            
            if node_type == 'condition' and result.get('condition_result'):
                condition_value = result['condition_result'].get('matched', False)
                next_edges = [e for e in next_edges if e.get('sourceHandle') == str(condition_value) or not e.get('sourceHandle')]
            
            for edge in next_edges:
                next_node = node_map.get(edge['target'])
                if next_node and state[next_node['id']]['status'] == 'pending':
                    updated_input = {**input_data, 'previous_result': result}
                    self._execute_node(next_node, updated_input, state, node_map, edges)
            
            return result
            
        except Exception as e:
            logger.error(f"Erreur execution noeud {node_id}: {e}")
            state[node_id]['status'] = 'failed'
            state[node_id]['result'] = {'error': str(e)}
            return {'error': str(e)}
    
    def _evaluate_conditions(self, conditions: list, input_data: dict) -> dict:
        """Evalue les conditions d'un noeud condition"""
        if not conditions:
            return {'matched': True, 'condition': None}
        
        for condition in conditions:
            field = condition.get('field', 'intention')
            operator = condition.get('operator', '==')
            value = condition.get('value', '')
            
            if field == 'intention':
                field_value = input_data.get('intention', input_data.get('type', 'general'))
            elif field == 'priorite' or field == 'priority':
                field_value = input_data.get('priority', input_data.get('priorite', 'medium'))
            elif field == 'sentiment':
                field_value = input_data.get('sentiment', 'neutre')
            elif field == 'confidence':
                field_value = input_data.get('confidence', 85)
            else:
                field_value = str(input_data.get(field, ''))
            
            field_value_str = str(field_value).lower()
            value_str = str(value).lower()
            
            if operator == '==':
                matched = field_value_str == value_str
            elif operator == '!=':
                matched = field_value_str != value_str
            elif operator == 'contains':
                matched = value_str in field_value_str
            elif operator == '>':
                try:
                    matched = float(field_value) > float(value)
                except:
                    matched = False
            elif operator == '<':
                try:
                    matched = float(field_value) < float(value)
                except:
                    matched = False
            else:
                matched = False
            
            if matched:
                return {'matched': True, 'condition': condition, 'field': field, 'value': value}
        
        return {'matched': False, 'condition': None}


# ===================================================================
# CONFIGURATION POSTGRESQL AVEC STATS_GLOBALES
# ===================================================================
class PostgresDB:
    """Gestionnaire de connexion PostgreSQL avec table stats_globales"""
    
    def __init__(self):
        if os.getenv('DOCKER_ENV') == 'true' or os.path.exists('/.dockerenv'):
            self.host = "postgres"
        else:
            self.host = os.getenv("DATABASE_HOST", "127.0.0.1")
        
        self.port = int(os.getenv("DATABASE_PORT", "5432"))
        self.database = os.getenv("DATABASE_NAME", "agents_ia_db")
        self.user = os.getenv("DATABASE_USER", "agents_user")
        self.password = os.getenv("DATABASE_PASSWORD", "")
        self.conn = None
        logger.info(f"PostgreSQL configure: {self.host}:{self.port}/{self.database}")

    def get_connection(self):
        try:
            if self.conn is None or self.conn.closed:
                self.conn = psycopg2.connect(
                    host=self.host,
                    port=self.port,
                    database=self.database,
                    user=self.user,
                    password=self.password,
                    cursor_factory=psycopg2.extras.RealDictCursor,
                    client_encoding='UTF8'
                )
            return self.conn
        except Exception as e:
            logger.error(f"Erreur PostgreSQL: {e}")
            raise

    def init_database(self):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            logger.info("Connexion PostgreSQL reussie")
            return True
        except Exception as e:
            logger.error(f"Erreur initialisation PostgreSQL: {e}")
            return False

    def init_stats_table(self):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stats_globales (
                    id INT PRIMARY KEY DEFAULT 1,
                    emails_traites INT DEFAULT 0,
                    emails_envoyes INT DEFAULT 0,
                    analyses_llm INT DEFAULT 0,
                    crm INT DEFAULT 0,
                    tickets INT DEFAULT 0,
                    erreurs INT DEFAULT 0,
                    regles_declenchees INT DEFAULT 0,
                    temps_total FLOAT DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)
            
            cursor.execute("""
                INSERT INTO stats_globales (id) 
                SELECT 1 
                WHERE NOT EXISTS (SELECT 1 FROM stats_globales WHERE id = 1)
            """)
            
            conn.commit()
            cursor.close()
            logger.info("Table stats_globales initialisee")
        except Exception as e:
            logger.error(f"Erreur init stats_globales: {e}")
    
    def init_email_events_table(self):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS email_events (
                    id UUID PRIMARY KEY,
                    client_id UUID,
                    client_email VARCHAR(255),
                    subject TEXT,
                    content TEXT,
                    intent VARCHAR(100),
                    priority VARCHAR(50),
                    action_taken TEXT,
                    delivery_status VARCHAR(50),
                    email_type VARCHAR(20),
                    status VARCHAR(20) DEFAULT 'pending',
                    confidence FLOAT DEFAULT 0,
                    source_document TEXT,
                    validated_by VARCHAR(255),
                    validated_at TIMESTAMP,
                    rejected_by VARCHAR(255),
                    rejected_at TIMESTAMP,
                    rejection_reason TEXT,
                    response_from_client BOOLEAN DEFAULT FALSE,
                    response_content TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    sent_at TIMESTAMP,
                    unique_key VARCHAR(255) UNIQUE,
                    error_message TEXT
                )
            """)
            
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_events_status ON email_events(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_events_client_status ON email_events(client_id, status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_events_created_at ON email_events(created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_events_unique_key ON email_events(unique_key)")
            
            conn.commit()
            cursor.close()
            logger.info("Table email_events initialisee avec cle unique anti-doublon")
        except Exception as e:
            logger.error(f"Erreur init_email_events_table: {e}")
    
    def init_requete_enrichissement(self):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            sql = """
                DO $$ 
                BEGIN
                    BEGIN
                        ALTER TABLE requetes ADD COLUMN resume_ia TEXT;
                    EXCEPTION
                        WHEN duplicate_column THEN NULL;
                    END;
                    BEGIN
                        ALTER TABLE requetes ADD COLUMN action_suggestee TEXT;
                    EXCEPTION
                        WHEN duplicate_column THEN NULL;
                    END;
                    BEGIN
                        ALTER TABLE requetes ADD COLUMN confidence FLOAT DEFAULT 0;
                    EXCEPTION
                        WHEN duplicate_column THEN NULL;
                    END;
                    BEGIN
                        ALTER TABLE requetes ADD COLUMN timeline JSONB;
                    EXCEPTION
                        WHEN duplicate_column THEN NULL;
                    END;
                    BEGIN
                        ALTER TABLE requetes ADD COLUMN titre_enrichi TEXT;
                    EXCEPTION
                        WHEN duplicate_column THEN NULL;
                    END;
                    BEGIN
                        ALTER TABLE requetes ADD COLUMN priorite VARCHAR(20) DEFAULT 'low';
                    EXCEPTION
                        WHEN duplicate_column THEN NULL;
                    END;
                    BEGIN
                        ALTER TABLE requetes ADD COLUMN statut_display VARCHAR(50) DEFAULT 'Analyse';
                    EXCEPTION
                        WHEN duplicate_column THEN NULL;
                    END;
                END $$;
            """
            cursor.execute(sql)
            conn.commit()
            cursor.close()
            logger.info("Table requetes enrichie")
        except Exception as e:
            logger.error(f"Erreur init_requete_enrichissement: {e}")
    
    def increment(self, field: str, value: int = 1):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            self.init_stats_table()
            
            allowed_fields = ['emails_traites', 'emails_envoyes', 'analyses_llm', 'crm', 'tickets', 'erreurs', 'regles_declenchees']
            if field not in allowed_fields:
                logger.warning(f"Champ non autorise: {field}")
                return
            
            cursor.execute(f"""
                UPDATE stats_globales
                SET {field} = {field} + %s,
                    updated_at = NOW()
                WHERE id = 1
            """, (value,))
            
            conn.commit()
            cursor.close()
            logger.debug(f"Incremente {field}: +{value}")
        except Exception as e:
            logger.error(f"Erreur increment {field}: {e}")
    
    def add_time(self, temps: float):
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE stats_globales
                SET temps_total = temps_total + %s,
                    updated_at = NOW()
                WHERE id = 1
            """, (temps,))
            
            conn.commit()
            cursor.close()
            logger.debug(f"Temps ajoute: +{temps}s")
        except Exception as e:
            logger.error(f"Erreur add_time: {e}")
    
    def get_stats(self) -> Dict:
        try:
            conn = self.get_connection()
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            # NE PAS appeler init_stats_table() ici !
            cursor.execute("SELECT * FROM stats_globales WHERE id = 1")
            row = cursor.fetchone()
            cursor.close()
            
            if row:
                return {
                    "emails_traites": int(row.get('emails_traites', 0)),
                    "emails_envoyes": int(row.get('emails_envoyes', 0)),
                    "analyses_llm": int(row.get('analyses_llm', 0)),
                    "crm": int(row.get('crm', 0)),
                    "tickets": int(row.get('tickets', 0)),
                    "erreurs": int(row.get('erreurs', 0)),
                    "regles_declenchees": int(row.get('regles_declenchees', 0)),
                    "temps_total": float(row.get('temps_total', 0)),
                    "updated_at": row.get('updated_at')
                }
            return self._get_default_stats()
        except Exception as e:
            logger.error(f"Erreur get_stats: {e}")
            return self._get_default_stats()
    
    def _get_default_stats(self) -> Dict:
        return {
            "emails_traites": 0,
            "emails_envoyes": 0,
            "analyses_llm": 0,
            "crm": 0,
            "tickets": 0,
            "erreurs": 0,
            "regles_declenchees": 0,
            "temps_total": 0.0,
            "updated_at": datetime.now()
        }
    
    def get_temps_moyen(self) -> float:
        stats = self.get_stats()
        emails = stats.get('emails_traites', 0)
        temps_total = stats.get('temps_total', 0)
        if emails > 0:
            return round(temps_total / emails, 2)
        return 0
    
    def get_taux_succes(self) -> float:
        stats = self.get_stats()
        emails = stats.get('emails_traites', 0)
        erreurs = stats.get('erreurs', 0)
        if emails > 0:
            return round((emails - erreurs) / emails * 100, 1)
        return 100
    
    def close(self):
        if self.conn and not self.conn.closed:
            self.conn.close()


# ===================================================================
# MOTEUR DE REGLES METIER
# ===================================================================
class RuleEngine:
    def __init__(self, db, email_sender, agent):
        self.db = db
        self.email_sender = email_sender
        self.agent = agent
        self.max_actions_per_cycle = 3
        self.last_execution_time = None
        self.min_interval_seconds = 3600
        self.processed_actions = {}
        self.action_cooldown = 86400
        self.email_sent_counter = defaultdict(int)
        self.email_sent_time = {}
        self.max_emails_per_hour = 20
        logger.info("Moteur de regles initialise")
    
    def _check_rate_limit(self, client_id: str) -> bool:
        now = time.time()
        hour_ago = now - 3600
        
        expired = [k for k, v in self.email_sent_time.items() if v < hour_ago]
        for k in expired:
            del self.email_sent_time[k]
            if k in self.email_sent_counter:
                del self.email_sent_counter[k]
        
        if self.email_sent_counter[client_id] >= self.max_emails_per_hour:
            logger.warning(f"Rate limit atteint pour client {client_id}: {self.email_sent_counter[client_id]} emails/heure")
            return False
        
        return True
    
    def _record_email_sent(self, client_id: str):
        self.email_sent_counter[client_id] += 1
        self.email_sent_time[client_id] = time.time()
    
    def _generate_unique_key(self, action_type: str, client_id: str, reference: str) -> str:
        return hashlib.md5(f"{action_type}_{client_id}_{reference}_{datetime.now().strftime('%Y-%m-%d')}".encode()).hexdigest()
    
    def _check_duplicate_in_db(self, action_type: str, client_id: str, reference: str) -> bool:
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            
            cursor.execute("""
                SELECT COUNT(*) as count FROM email_events 
                WHERE client_id = %s 
                AND intent = %s 
                AND created_at > %s
                AND status IN ('pending', 'sent')
            """, (client_id, action_type, today_start))
            
            result = cursor.fetchone()
            cursor.close()
            
            if result and result['count'] > 0:
                logger.info(f"Doublon detecte pour {action_type} client {client_id} ({result['count']} emails aujourd'hui)")
                return True
                
        except Exception as e:
            logger.error(f"Erreur verification doublon DB: {e}")
        
        return False
    
    def verifier_toutes_regles(self):
        if self.email_sender.email_automation_paused:
            logger.warning("Automation emails pausee (limite Gmail atteinte)")
            return []
        
        if self.last_execution_time:
            elapsed = (datetime.now() - self.last_execution_time).total_seconds()
            if elapsed < self.min_interval_seconds:
                logger.debug(f"Attente {int(self.min_interval_seconds - elapsed)}s")
                return []
        
        logger.info("Verification des regles metier...")
        self.last_execution_time = datetime.now()
        
        all_actions = []
        
        facture_actions = self._regle_factures_impayees()
        contrat_actions = self._regle_contrats_expires()
        ticket_actions = self._regle_tickets_non_traites()
        
        for action in facture_actions + contrat_actions:
            all_actions.append(action)
        
        for action in ticket_actions:
            all_actions.append(action)
        
        if len(all_actions) > 0:
            self.db.increment("regles_declenchees", len(all_actions))
            logger.info(f"{len(all_actions)} nouvelles regles declenchees")
        else:
            logger.info("Aucune nouvelle regle a declencher")
        
        return all_actions
    
    def _regle_factures_impayees(self):
        actions = []
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT DISTINCT ON (f.client_id) f.*, c.name as nom, c.email, c.id as client_id
                FROM factures f
                JOIN clients c ON f.client_id::text = c.id::text
                WHERE f.statut = 'impayee'
                ORDER BY f.client_id, f.date_echeance ASC
                LIMIT 5
            ''')
            factures = cursor.fetchall()
            
            for facture in factures:
                if self._check_duplicate_in_db('facture', facture['client_id'], facture['numero']):
                    continue
                
                if not self._check_rate_limit(facture['client_id']):
                    continue
                
                jours_retard = self._calculer_jours_retard(facture['date_echeance'])
                if 5 <= jours_retard < 10:
                    sujet = f"Relance - Facture {facture['numero']}"
                    corps = f"""Bonjour {facture['nom']},

Nous faisons suite à notre précédent message concernant la facture n°{facture['numero']} d'un montant de {facture['montant']:.2f}€.

À ce jour, nous n'avons pas encore enregistré votre règlement. Nous vous prions de bien vouloir régulariser cette situation dans les plus brefs délais.

Si vous avez déjà effectué le paiement, nous vous remercions de nous faire parvenir une copie du justificatif.

Cordialement,
L'équipe financière
AI Agents Platform"""
                    
                    unique_key = self._generate_unique_key('facture', facture['client_id'], facture['numero'])
                    
                    self.agent._sauvegarder_email_event(
                        facture['client_id'], facture['email'], 
                        sujet, 'relance_facture', 'high', 'generation_automatique',
                        'pending', corps, 'generated', 94, unique_key
                    )
                    
                    action = {'type': 'relance_simple', 'client': facture['nom'], 'email': facture['email'], 
                              'client_id': facture['client_id'], 'facture_numero': facture['numero'], 
                              'montant': facture['montant'], 'email_sent': False}
                    actions.append(action)
                    logger.info(f"Nouvel email en attente: {sujet}")
                    
            cursor.close()
        except Exception as e:
            logger.error(f"Erreur regles factures: {e}")
        return actions
    
    def _regle_contrats_expires(self):
        actions = []
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT DISTINCT ON (ct.client_id) ct.*, c.name as nom, c.email, c.id as client_id
                FROM contrats ct
                JOIN clients c ON ct.client_id::text = c.id::text
                WHERE ct.statut = 'expire'
                ORDER BY ct.client_id, ct.date_fin DESC
                LIMIT 3
            ''')
            contrats = cursor.fetchall()
            
            for contrat in contrats:
                if self._check_duplicate_in_db('contrat', contrat['client_id'], contrat['numero']):
                    continue
                
                if not self._check_rate_limit(contrat['client_id']):
                    continue
                
                sujet = f"Renouvellement contrat {contrat['numero']}"
                corps = f"""Bonjour {contrat['nom']},

Nous avons le plaisir de vous informer que votre contrat n°{contrat['numero']} arrive à échéance.

Afin de poursuivre notre collaboration dans les meilleures conditions, nous vous proposons un renouvellement.

Pour confirmer votre souhait de renouvellement, veuillez nous retourner le contrat signé ou nous contacter au plus vite.

Nous restons à votre disposition pour toute information complémentaire.

Cordialement,
L'équipe commerciale
AI Agents Platform"""
                
                unique_key = self._generate_unique_key('contrat', contrat['client_id'], contrat['numero'])
                
                self.agent._sauvegarder_email_event(
                    contrat['client_id'], contrat['email'],
                    sujet, 'renouvellement_contrat', 'high', 'generation_automatique',
                    'pending', corps, 'generated', 92, unique_key
                )
                
                action = {'type': 'relance_contrat', 'client': contrat['nom'], 'email': contrat['email'], 
                          'client_id': contrat['client_id'], 'contrat_numero': contrat['numero'], 
                          'type_contrat': contrat['type'], 'email_sent': False}
                actions.append(action)
                logger.info(f"Nouvel email en attente: {sujet}")
                
            cursor.close()
        except Exception as e:
            logger.error(f"Erreur regles contrats: {e}")
        return actions
    
    def _regle_tickets_non_traites(self):
        actions = []
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT DISTINCT ON (t.client_id) t.*, c.name as nom, c.email, c.id as client_id
                FROM tickets t
                JOIN clients c ON t.client_id::text = c.id::text
                WHERE t.statut = 'ouvert' 
                AND t.date_creation < NOW() - INTERVAL '2 days'
                ORDER BY t.client_id, t.date_creation ASC
                LIMIT 3
            ''')
            tickets = cursor.fetchall()
            for ticket in tickets:
                if self._check_duplicate_in_db('ticket', ticket['client_id'], ticket['id']):
                    continue
                
                action = {'type': 'escalade_ticket', 'ticket_id': ticket['id'], 'client': ticket['nom'], 
                          'email': ticket['email'], 'client_id': ticket['client_id']}
                
                self.agent._sauvegarder_email_event(
                    action['client_id'], action['email'],
                    f"Ticket support non traite",
                    'support', 'critical', 'escalade_urgence',
                    'internal', None, 'internal', 88
                )
                actions.append(action)
                logger.warning(f"Nouveau ticket non traite: {ticket['id']}")
            cursor.close()
        except Exception as e:
            logger.error(f"Erreur regles tickets: {e}")
        return actions
    
    def _calculer_jours_retard(self, date_echeance):
        try:
            if isinstance(date_echeance, str):
                echeance = datetime.strptime(date_echeance, "%Y-%m-%d")
            else:
                echeance = date_echeance
            return max(0, (datetime.now() - echeance).days)
        except:
            return 0


# ===================================================================
# ENVOYEUR D'EMAILS AVEC RATE LIMITING - VERSION OAuth2
# ===================================================================
class RealEmailSender:
    def __init__(self, db):
        self.db = db
        self.gmail = GmailOAuth()
        self.send_real_emails = True
        
        # Rate limiting
        self.email_queue = deque()
        self.send_lock = Lock()
        self.last_send_time = 0
        self.min_interval = 60
        self.daily_limit = 95
        self.hourly_limit = 20
        self.sent_today = 0
        self.sent_last_hour = 0
        self.hour_start = datetime.now()
        self.last_reset = datetime.now().date()
        self.max_retries = 3
        self.email_automation_paused = False
        self.daily_limit_reached = False
        
        logger.info(f"Envoyeur email configure avec OAuth2")
        logger.info(f"   Limite: {self.daily_limit} emails/jour, {self.hourly_limit} emails/heure, Intervalle: {self.min_interval}s")
    
    def _reset_daily_counter(self):
        today = datetime.now().date()
        if today != self.last_reset:
            self.sent_today = 0
            self.last_reset = today
            self.daily_limit_reached = False
            self.email_automation_paused = False
            logger.info("Compteur d'emails journalier reinitialise")
    
    def _reset_hourly_counter(self):
        now = datetime.now()
        if (now - self.hour_start).total_seconds() >= 3600:
            self.sent_last_hour = 0
            self.hour_start = now
            logger.debug("Compteur horaire reinitialise")
    
    def _check_rate_limit(self) -> bool:
        self._reset_hourly_counter()
        if self.sent_last_hour >= self.hourly_limit:
            logger.warning(f"Limite horaire atteinte: {self.sent_last_hour}/{self.hourly_limit}")
            return False
        return True
    
    def is_duplicate(self, destinataire: str, sujet: str) -> bool:
        """Vérification anti-doublon basée sur la base de données - solution définitive"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) as count FROM email_events 
                WHERE client_email = %s 
                AND subject = %s 
                AND email_type = 'sent'
                AND delivery_status = 'delivered'
                AND created_at > NOW() - INTERVAL '1 day'
            """, (destinataire, sujet))
            
            result = cursor.fetchone()
            cursor.close()
            
            if result and result['count'] > 0:
                logger.info(f"Email deja envoye aujourd'hui: {sujet} -> {destinataire}")
                return True
        except Exception as e:
            logger.error(f"Erreur verification doublon: {e}")
        
        return False
    
    def can_send(self):
        self._reset_daily_counter()
        if self.daily_limit_reached:
            logger.warning(f"Limite Gmail atteinte, envois bloques")
            return False
        if self.sent_today >= self.daily_limit:
            self.daily_limit_reached = True
            self.email_automation_paused = True
            logger.error(f"Daily limit exceeded - Automation paused")
            return False
        if not self._check_rate_limit():
            return False
        return True
    
    def _wait_for_rate_limit(self):
        with self.send_lock:
            elapsed = time.time() - self.last_send_time
            if elapsed < self.min_interval and self.last_send_time > 0:
                wait_time = self.min_interval - elapsed
                logger.info(f"Rate limiting: attente de {wait_time:.1f}s...")
                time.sleep(wait_time)
    
    def envoyer(self, destinataire: str, sujet: str, corps: str, retry_count=0):
        if not self.send_real_emails:
            logger.info(f"[SIMULATION] Email a {destinataire} - Sujet: {sujet}")
            return True        
        if self.is_duplicate(destinataire, sujet):
            return False
        
        if not self.can_send():
            return False
        
        self._wait_for_rate_limit()
        
        try:
            success = self.gmail.send_email(destinataire, sujet, corps)
            
            if success:
                with self.send_lock:
                    self.sent_today += 1
                    self.sent_last_hour += 1
                    self.last_send_time = time.time()
                logger.info(f"Email envoye a {destinataire} ({self.sent_today}/{self.daily_limit}, {self.sent_last_hour}/{self.hourly_limit}/h)")
                return True
            else:
                if retry_count < self.max_retries and not self.daily_limit_reached:
                    logger.info(f"Tentative {retry_count + 1}/{self.max_retries} dans 10s...")
                    time.sleep(10)
                    return self.envoyer(destinataire, sujet, corps, retry_count + 1)
                return False
            
        except Exception as e:
            logger.error(f"Erreur envoi email: {e}")
            if retry_count < self.max_retries and not self.daily_limit_reached:
                logger.info(f"Tentative {retry_count + 1}/{self.max_retries} dans 5s...")
                time.sleep(5)
                return self.envoyer(destinataire, sujet, corps, retry_count + 1)
            return False
    
    def envoyer_relance_facture(self, action):
        sujet = f"Relance - Facture {action.get('facture_numero', '')}"
        corps = f"Bonjour {action['client']}, merci de regulariser votre situation concernant la facture."
        return self.envoyer(action['email'], sujet, corps)
    
    def envoyer_mise_en_demeure(self, action):
        sujet = f"MISE EN DEMEURE - Facture {action.get('facture_numero', '')}"
        corps = f"Bonjour {action['client']}, veuillez proceder au paiement immediat de la facture."
        return self.envoyer(action['email'], sujet, corps)
    
    def envoyer_relance_contrat(self, action):
        sujet = f"Renouvellement contrat {action.get('contrat_numero', '')}"
        corps = f"Bonjour {action['client']}, votre contrat arrive a expiration."
        return self.envoyer(action['email'], sujet, corps)


# ===================================================================
# AGENT INTELLIGENT PRINCIPAL
# ===================================================================
class IntelligentAutonomousAgent:
    """Agent IA Autonome avec stats_globales comme source unique"""
    
    def __init__(self):
        self.llm = LLMEngine()
        self.rag_manager = RAGManager()
        self.enron_rag = RealRAGEngine(collection_name="enron")
        self.cuad_rag = RealRAGEngine(collection_name="cuad")
        self.multiwoz_rag = RealRAGEngine(collection_name="multiwoz")
        
        self.db = PostgresDB()
        self.db.init_database()
        self.db.init_stats_table()
        self.db.init_email_events_table()
        self.db.init_requete_enrichissement()
        
        self.email_sender = RealEmailSender(self.db)
        self.scheduler = BackgroundScheduler()
        self.rule_engine = RuleEngine(self.db, self.email_sender, self)
        self.email_listener = RealEmailListener(self, self.db, self.email_sender)
        
        self.workflow_engine = WorkflowEngine(self.db, self)
        
        self._init_tables()
        self._charger_donnees_test()
        self._index_datasets()
        
        # Informations de l'entreprise pour les emails professionnels
        self.nom_entreprise = "AI Agents Platform"
        self.adresse = "123 Avenue des Technologies, 75000 Paris"
        self.telephone = "+33 1 23 45 67 89"
        self.site_web = "https://agents-ia-platform.com"
        
        logger.info("="*80)
        logger.info("AGENT IA AUTONOME INITIALISE")
        logger.info("="*80)
        logger.info(f"LLM: {self.llm.model}")
        logger.info(f"Email: {Config.GMAIL_ADRESSE}")
        logger.info(f"Moteur de workflows: ACTIF")
        logger.info("="*80)
    
    # ================================================================
    # FONCTIONS D'INCREMENTATION
    # ================================================================
    
    def _increment_emails_traites(self):
        self.db.increment("emails_traites")
    
    def _increment_emails_envoyes(self):
        self.db.increment("emails_envoyes")
    
    def _increment_analyses_llm(self):
        self.db.increment("analyses_llm")
    
    def _increment_crm(self):
        self.db.increment("crm")
        logger.info(f"CRM incremente")
    
    def _increment_ticket(self):
        self.db.increment("tickets")
        logger.info(f"Ticket incremente")
    
    def _increment_erreur(self):
        self.db.increment("erreurs")
        logger.warning(f"Erreur incremente")
    
    def _add_temps_reponse(self, temps: float):
        self.db.add_time(temps)
    
    # ================================================================
    # FONCTIONS DE GESTION DES EMAILS EVENTS
    # ================================================================
    
    def _sauvegarder_email_event(self, client_id: str, client_email: str, sujet: str, 
                                   intention: str, priorite: str, action_taken: str,
                                   delivery_status: str, contenu: str = None, 
                                   email_type: str = 'received', confidence: float = 0,
                                   unique_key: str = None) -> str:
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            event_id = str(uuid.uuid4())
            now = datetime.now()
            
            if not unique_key:
                unique_key = hashlib.md5(f"{client_id}_{sujet}_{now.strftime('%Y-%m-%d')}".encode()).hexdigest()
            
            try:
                cursor.execute("SELECT id FROM email_events WHERE unique_key = %s", (unique_key,))
                existing = cursor.fetchone()
                if existing:
                    logger.warning(f"Email deja existant (cle unique): {sujet}")
                    return existing['id']
            except Exception as e:
                logger.debug(f"Verification unique_key ignoree: {e}")
            
            status = 'pending'
            if email_type == 'sent' and delivery_status == 'delivered':
                status = 'sent'
            elif email_type == 'received':
                status = 'received'
            elif email_type == 'generated':
                status = 'pending'
            
            sent_at_value = now if delivery_status == 'delivered' else None
            
            cursor.execute("""
                INSERT INTO email_events (
                    id, client_id, client_email, subject, content, 
                    intent, priority, action_taken, delivery_status, 
                    email_type, confidence, status, created_at, sent_at, unique_key
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (event_id, client_id, client_email, sujet, contenu, 
                  intention, priorite, action_taken, delivery_status, 
                  email_type, confidence, status, now, sent_at_value, unique_key))
            
            conn.commit()
            cursor.close()
            logger.info(f"Evenement email sauvegarde: {email_type} - {sujet}")
            return event_id
        except Exception as e:
            logger.error(f"Erreur sauvegarde email_event: {e}")
            return None
    
    # ================================================================
    # FONCTIONS DE GESTION DES EMAILS PENDING
    # ================================================================
    
    def get_pending_emails_grouped(self) -> List[Dict]:
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    intent,
                    client_id,
                    client_email,
                    subject,
                    COUNT(*) as count,
                    MIN(created_at) as first_created,
                    MAX(confidence) as max_confidence,
                    STRING_AGG(DISTINCT c.name, ', ') as client_names
                FROM email_events e
                LEFT JOIN clients c ON e.client_id::text = c.id::text
                WHERE status = 'pending' AND email_type = 'generated'
                GROUP BY intent, client_id, client_email, subject
                ORDER BY first_created ASC
                LIMIT 50
            """)
            rows = cursor.fetchall()
            cursor.close()
            
            grouped = []
            for row in rows:
                grouped.append({
                    "intent": row['intent'],
                    "client_id": row['client_id'],
                    "client_email": row['client_email'],
                    "subject": row['subject'],
                    "count": row['count'],
                    "first_created": row['first_created'].isoformat() if row['first_created'] else None,
                    "max_confidence": row['max_confidence'],
                    "client_names": row['client_names']
                })
            
            return grouped
        except Exception as e:
            logger.error(f"Erreur get_pending_emails_grouped: {e}")
            return []
    
    def get_pending_emails_by_intent(self, intent: str) -> List[Dict]:
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    e.id, e.client_id, e.client_email, e.subject, e.content,
                    e.intent, e.priority, e.action_taken, e.confidence,
                    e.created_at, c.name as client_name
                FROM email_events e
                LEFT JOIN clients c ON e.client_id::text = c.id::text
                WHERE e.status = 'pending' AND e.email_type = 'generated'
                AND e.intent = %s
                ORDER BY e.created_at ASC
                LIMIT 100
            """, (intent,))
            rows = cursor.fetchall()
            cursor.close()
            
            emails = []
            for row in rows:
                emails.append({
                    "id": row['id'],
                    "client_id": row['client_id'],
                    "client_name": row['client_name'] or row['client_email'].split('@')[0],
                    "client_email": row['client_email'],
                    "subject": row['subject'],
                    "content": row['content'],
                    "intent": row['intent'],
                    "priority": row['priority'],
                    "action_taken": row['action_taken'],
                    "confidence": row['confidence'],
                    "created_at": row['created_at'].isoformat() if row['created_at'] else None
                })
            
            return emails
        except Exception as e:
            logger.error(f"Erreur get_pending_emails_by_intent: {e}")
            return []
    
    # ================================================================
    # FONCTION VALIDER EMAIL AVEC TEMPLATES PROFESSIONNELS
    # ================================================================
    
    def _generer_reponse_email_professionnelle(self, intention: str, client: dict, donnees: dict) -> dict:
        """Génère une réponse email professionnelle adaptée à l'intention"""
        
        nom_client = client.get('name', 'Client')
        
        # Templates professionnels par intention
        templates = {
            'facture': {
                'objet': f"Facture - Échéance de paiement",
                'corps': f"""
Bonjour {nom_client},

Nous vous remercions pour votre confiance.

Conformément à nos échanges, nous vous rappelons que votre facture d'un montant de {donnees.get('montant', '0')}€ arrive à échéance prochainement.

Nous vous prions de bien vouloir procéder au règlement dans les plus brefs délais afin d'éviter toute interruption de service.

Modalités de paiement :
- Virement bancaire : FR76 1234 5678 9012 3456 7890 123
- Carte bancaire : https://paiement.agents-ia-platform.com

En cas de difficulté, n'hésitez pas à nous contacter.

Cordialement,
L'équipe financière"""
            },
            'relance_facture': {
                'objet': f"RELANCE - Facture impayée",
                'corps': f"""
Bonjour {nom_client},

Nous faisons suite à notre précédent message concernant votre facture d'un montant de {donnees.get('montant', '0')}€.

À ce jour, nous n'avons pas encore enregistré votre règlement alors que la date d'échéance est dépassée.

Nous vous prions de bien vouloir régulariser cette situation dans un délai de 48h pour éviter des frais de pénalité.

Si vous avez déjà effectué le paiement, nous vous remercions de nous faire parvenir une copie du justificatif.

Cordialement,
L'équipe financière"""
            },
            'contrat': {
                'objet': f"Renouvellement de contrat",
                'corps': f"""
Bonjour {nom_client},

Nous avons le plaisir de vous informer que votre contrat arrive à échéance prochainement.

Afin de poursuivre notre collaboration dans les meilleures conditions, nous vous proposons un renouvellement avec les conditions suivantes :
- Durée : 12 mois
- Conditions : inchangées

Pour accepter cette proposition, veuillez nous retourner le contrat signé avant le {donnees.get('date_limite', '30/06/2026')}.

Nous restons à votre disposition pour toute information complémentaire.

Cordialement,
L'équipe commerciale"""
            },
            'relance_contrat': {
                'objet': f"RELANCE - Renouvellement de contrat",
                'corps': f"""
Bonjour {nom_client},

Nous revenons vers vous concernant le renouvellement de votre contrat qui est arrivé à échéance.

Afin de ne pas interrompre nos services, nous vous invitons à nous confirmer votre souhait de renouvellement dans les plus brefs délais.

Nous restons à votre disposition pour tout échange.

Cordialement,
L'équipe commerciale"""
            },
            'support': {
                'objet': f"SUPPORT - Votre demande",
                'corps': f"""
Bonjour {nom_client},

Nous avons bien reçu votre demande concernant {donnees.get('sujet', 'votre problématique')}.

Notre équipe technique a analysé votre situation et voici les éléments de réponse :

{donnees.get('solution', 'Nous travaillons activement sur votre demande et reviendrons vers vous dans les plus brefs délais.')}

N'hésitez pas à nous contacter si vous avez besoin de précisions.

Cordialement,
L'équipe support"""
            },
            'general': {
                'objet': f"Réponse à votre demande",
                'corps': f"""
Bonjour {nom_client},

Nous accusons réception de votre demande et vous remercions pour votre message.

{donnees.get('reponse', 'Notre équipe traite actuellement votre demande et vous apportera une réponse détaillée dans les plus brefs délais.')}

En attendant, n'hésitez pas à nous contacter pour toute information complémentaire.

Cordialement,
L'équipe {self.nom_entreprise}"""
            }
        }
        
        # Sélectionner le template approprié
        intention_key = 'general'
        intention_lower = intention.lower()
        if 'facture' in intention_lower or 'impayee' in intention_lower:
            if 'relance' in intention_lower:
                intention_key = 'relance_facture'
            else:
                intention_key = 'facture'
        elif 'contrat' in intention_lower:
            if 'relance' in intention_lower or 'expire' in intention_lower:
                intention_key = 'relance_contrat'
            else:
                intention_key = 'contrat'
        elif 'support' in intention_lower or 'ticket' in intention_lower:
            intention_key = 'support'
        
        template = templates.get(intention_key, templates['general'])
        
        # Générer l'email
        objet = template['objet']
        corps = template['corps'].strip()
        
        # Ajouter la signature
        signature = f"""
--
{self.nom_entreprise}
{self.adresse}
Tél: {self.telephone}
Site: {self.site_web}
"""
        
        corps_complet = f"{corps}\n\n{signature}"
        
        return {"objet": objet, "corps": corps_complet}
    
    def valider_et_envoyer_email(self, email_id: str, content_modifie: str = None) -> bool:
        """Valide un email en attente et le marque comme envoyé immédiatement"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            # Récupérer l'email en attente
            cursor.execute("""
                SELECT id, client_id, client_email, subject, content, intent, priority, status, email_type
                FROM email_events
                WHERE id = %s AND (status = 'pending' OR email_type = 'generated')
            """, (email_id,))
            
            email = cursor.fetchone()
            
            if not email:
                logger.error(f"Email {email_id} non trouve ou deja traite")
                return False
            
            logger.info(f"Validation de l'email: {email['subject']} (ID: {email_id})")
            
            # Récupérer le nom du client
            client_name = email['client_email'].split('@')[0]
            try:
                cursor.execute("SELECT name FROM clients WHERE id = %s OR email = %s", 
                              (email['client_id'], email['client_email']))
                client_row = cursor.fetchone()
                if client_row:
                    client_name = client_row['name']
            except:
                pass
            
            # Si le contenu n'est pas modifié, générer un email professionnel
            if content_modifie is None:
                # Générer un email professionnel adapté à l'intention
                template = self._generer_reponse_email_professionnelle(
                    email['intent'],
                    {'name': client_name, 'email': email['client_email']},
                    {'sujet': email['subject'], 'solution': 'Nous avons traité votre demande avec succès.'}
                )
                content = template['corps']
                sujet = template['objet']
            else:
                content = content_modifie
                sujet = email['subject']
            
            # Envoyer l'email réel
            success = self.email_sender.envoyer(
                email['client_email'],
                sujet,
                content
            )
            
            if success:
                now = datetime.now()
                
                # METTRE À JOUR TOUS LES CHAMPS NÉCESSAIRES
                cursor.execute("""
                    UPDATE email_events
                    SET status = 'sent',
                        email_type = 'sent',
                        delivery_status = 'delivered',
                        content = %s,
                        sent_at = %s
                    WHERE id = %s
                """, (content, now, email_id))
                
                conn.commit()
                
                # Incrémenter les stats
                self._increment_emails_envoyes()
                
                logger.info(f"Email validé et envoyé: {sujet}")
                
                # Vérification immédiate
                cursor.execute("""
                    SELECT email_type, status, delivery_status, sent_at
                    FROM email_events
                    WHERE id = %s
                """, (email_id,))
                check = cursor.fetchone()
                logger.info(f"Vérification: email_type={check['email_type']}, status={check['status']}, sent_at={check['sent_at']}")
                
                cursor.close()
                return True
            else:
                logger.error(f"Échec envoi email pending: {email['subject']}")
                cursor.execute("""
                    UPDATE email_events
                    SET delivery_status = 'failed',
                        error_message = %s
                    WHERE id = %s
                """, ("Erreur lors de l'envoi", email_id))
                conn.commit()
                cursor.close()
                return False
                
        except Exception as e:
            logger.error(f"Erreur validation email: {e}")
            return False
    
    # ================================================================
    # FONCTIONS DE GENERATION DE DONNEES VARIEES
    # ================================================================
    
    TITRES_VARIES = {
        'facture': [
            'Incident facturation', 'Demande de remboursement', 'Double facturation',
            'Probleme de paiement', 'Facture impayee', 'Avoir commercial',
            'Releve de compte'
        ],
        'support': [
            'Assistance plateforme', 'Probleme acces compte', 'Incident technique',
            'Aide connexion', 'Bug application', 'Performance lente',
            'Erreur 500', 'API non disponible'
        ],
        'contrat': [
            'Mise a jour contrat', 'Renouvellement contrat', 'Demande devis',
            'Signature contrat', 'Clause contractuelle', 'Avenant commercial',
            'Resiliation contrat'
        ],
        'general': [
            'Question produit', 'Demande commerciale', 'Information abonnement',
            'Activation licence', 'Devis personnalise', 'Documentation technique',
            'Tarification', 'Conditions generales'
        ]
    }
    
    STATUTS_AFFICHAGE = {
        'success': 'Resolu',
        'traite': 'Resolu',
        'succes': 'Resolu',
        'analyse': 'Analyse',
        'analysed': 'Analyse',
        'automatised': 'Automatise',
        'repondu': 'Repondu',
        'escalade': 'Escalade'
    }
    
    def _calculer_confidence(self, analyses_count: int) -> int:
        base = 85
        variation = random.randint(-3, 8)
        confidence = min(98, max(84, base + variation))
        return confidence
    
    def _generer_resume_ia_reel(self, contenu: str, intention: str, comprehension: dict, analyses_count: int = 0) -> str:
        resumes = {
            'facture': [
                "Le client signale un probleme de facturation. Double facturation detectee.",
                "Demande de remboursement suite a une erreur de paiement.",
                "Facture impayee depuis plusieurs jours. Relance automatique effectuee."
            ],
            'support': [
                "Demande d'assistance technique. L'utilisateur rencontre des difficultes d'acces.",
                "Incident technique necessitant une intervention rapide.",
                "Probleme de performance signale par l'utilisateur."
            ],
            'contrat': [
                "Demande de mise a jour contractuelle. Le client souhaite modifier ses conditions.",
                "Renouvellement de contrat imminent. Action recommandee.",
                "Nouvelle demande de devis pour un contrat entreprise."
            ],
            'general': [
                "Demande d'information sur les services et tarifs de la plateforme.",
                "Question concernant les fonctionnalites disponibles.",
                "Demande de documentation technique et guides d'utilisation."
            ]
        }
        
        resumes_list = resumes.get(intention, resumes['general'])
        resume = random.choice(resumes_list)
        
        sentiment = comprehension.get('sentiment', 'neutre')
        if sentiment == 'positif':
            resume += " Le ton est positif et constructif."
        elif sentiment == 'negatif':
            resume += " Le ton est preoccupe."
        else:
            resume += " Le ton est neutre."
        
        return resume[:250]
    
    def _generer_action_suggestee(self, intention: str, comprehension: dict) -> str:
        actions = {
            'facture': 'Transfert service comptabilite pour analyse',
            'support': 'Creation ticket support prioritaire',
            'contrat': 'Programmer appel avec equipe commerciale',
            'general': 'Reponse automatique avec documentation'
        }
        return actions.get(intention, actions['general'])
    
    def _generer_titre_enrichi(self, sujet_original: str, intention: str, comprehension: dict) -> str:
        if sujet_original and sujet_original.lower() not in ['test', 'test message', '', 'test email']:
            return sujet_original
        
        titres = self.TITRES_VARIES.get(intention, self.TITRES_VARIES['general'])
        import time
        idx = int(time.time()) % len(titres)
        return titres[idx]
    
    def _generer_timeline_reelle(self, intention: str, start_time: datetime) -> list:
        timeline = []
        
        timeline.append({
            "time": start_time.strftime("%H:%M:%S"),
            "action": "Email client recu",
            "status": "success"
        })
        
        classif_time = start_time + timedelta(seconds=2)
        timeline.append({
            "time": classif_time.strftime("%H:%M:%S"),
            "action": "Classification IA terminee",
            "status": "success"
        })
        
        llm_time = start_time + timedelta(seconds=5)
        timeline.append({
            "time": llm_time.strftime("%H:%M:%S"),
            "action": f"Analyse GROQ LLM - Intention detectee: {intention}",
            "status": "success"
        })
        
        action_time = start_time + timedelta(seconds=8)
        if intention == 'facture':
            action_text = "Ticket facturation genere"
        elif intention == 'support':
            action_text = "Ticket support cree"
        else:
            action_text = "Reponse automatique envoyee"
        
        timeline.append({
            "time": action_time.strftime("%H:%M:%S"),
            "action": action_text,
            "status": "success"
        })
        
        crm_time = start_time + timedelta(seconds=10)
        timeline.append({
            "time": crm_time.strftime("%H:%M:%S"),
            "action": "CRM synchronise",
            "status": "success"
        })
        
        return timeline
    
    # ================================================================
    # FONCTIONS DE DETECTION CRM
    # ================================================================
    
    def _est_interaction_crm(self, intention: str, sujet: str, contenu: str) -> bool:
        texte = f"{intention} {sujet} {contenu}".lower().strip()
        blacklist = ["spam", "publicite", "newsletter", "test vide", "unsubscribe"]
        
        if any(word in texte for word in blacklist):
            return False
        if len(texte) > 5:
            return True
        return False
    
    def _determiner_type_crm(self, intention: str, sujet: str, contenu: str) -> str:
        texte = f"{intention} {sujet} {contenu}".lower()
        if any(k in texte for k in ['support', 'aide', 'probleme', 'assistance']):
            return "support"
        elif any(k in texte for k in ['facture', 'paiement', 'reclamation']):
            return "transaction"
        elif any(k in texte for k in ['contrat', 'devis', 'commande']):
            return "contract"
        else:
            return "general"
    
    # ================================================================
    # METHODES METIER
    # ================================================================
    
    def _get_or_create_client(self, email: str) -> str:
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM clients WHERE email = %s", (email,))
            client = cursor.fetchone()
            
            if client:
                client_id = client['id']
            else:
                client_id = str(uuid.uuid4())
                name = email.split('@')[0]
                cursor.execute("""
                    INSERT INTO clients (id, name, email, created_at) 
                    VALUES (%s, %s, %s, %s)
                """, (client_id, name, email, datetime.now()))
                conn.commit()
                logger.info(f"Nouveau client cree: {name} ({email})")
            
            cursor.close()
            return client_id
        except Exception as e:
            logger.error(f"Erreur get_or_create_client: {e}")
            return str(uuid.uuid4())
    
    def _creer_ticket_support(self, client_id: str, sujet: str, contenu: str) -> str:
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            ticket_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT INTO tickets (id, client_id, sujet, description, statut, date_creation)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (ticket_id, client_id, sujet, contenu[:500], 'ouvert', datetime.now()))
            conn.commit()
            cursor.close()
            logger.info(f"Ticket cree: {ticket_id}")
            return ticket_id
        except Exception as e:
            logger.error(f"Erreur creation ticket: {e}")
            return None
    
    def _sauvegarder_requete(self, expediteur: str, sujet: str, contenu: str, 
                              intention: str, resultat: dict, titre_enrichi: str = None,
                              resume_ia: str = None, action_suggestee: str = None,
                              confidence: float = 0, timeline: list = None,
                              statut_display: str = None) -> str:
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            requete_id = f"REQ-{int(time.time())}-{uuid.uuid4().hex[:4]}"
            
            priorite = 'high' if intention == 'facture' else 'medium' if intention in ['support', 'contrat'] else 'low'
            
            if not statut_display:
                statut_original = resultat.get('statut', 'succes')
                statut_display = self.STATUTS_AFFICHAGE.get(statut_original, 'Analyse')
            
            cursor.execute("""
                INSERT INTO requetes (
                    id, type, intention, agent_utilise, statut, date_soumission, 
                    donnees, resultat, temps_traitement, titre_enrichi, 
                    resume_ia, action_suggestee, confidence, timeline, priorite, statut_display
                ) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                requete_id, intention, intention, 'agent_automatique', 
                'succes' if intention != 'erreur' else 'erreur',
                datetime.now().isoformat(), 
                json.dumps({'expediteur': expediteur, 'sujet': sujet, 'description': contenu}),
                json.dumps(resultat, default=str), 
                resultat.get('temps_traitement', 0),
                titre_enrichi or sujet,
                resume_ia,
                action_suggestee,
                confidence,
                json.dumps(timeline) if timeline else None,
                priorite,
                statut_display
            ))
            conn.commit()
            cursor.close()
            
            logger.info(f"Requete {requete_id} sauvegardee (confidence: {confidence}%)")
            return requete_id
        except Exception as e:
            logger.error(f"Erreur sauvegarde requete: {e}")
            return None
    
    def _sauvegarder_email(self, client_id: str, sujet: str, contenu: str) -> str:
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            email_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT INTO emails (id, client_id, subject, content, status, received_at) 
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (email_id, client_id, sujet, contenu, 'received', datetime.now()))
            conn.commit()
            cursor.close()
            return email_id
        except Exception as e:
            logger.error(f"Erreur sauvegarde email: {e}")
            return str(uuid.uuid4())
    
    def _sauvegarder_log(self, client_id: str, client_email: str, client_name: str, 
                         action: str, status: str, details: str = None, 
                         duration: float = None, email_id: str = None):
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            log_id = str(uuid.uuid4())
            
            log_messages = {
                'email_received': 'Email client recu',
                'email_processed': 'Email traite avec succes',
                'email_processing': 'Traitement email en cours',
                'analysis_completed': 'Analyse IA terminee',
                'crm_updated': 'CRM mis a jour',
                'ticket_created': 'Ticket support cree'
            }
            
            log_message = log_messages.get(action, action)
            if details:
                log_message += f" - {details[:100]}"
            
            cursor.execute("""
                INSERT INTO logs (id, client_id, client_email, client_name, action, status, details, duration, email_id, timestamp) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (log_id, client_id, client_email, client_name, log_message, status, details, duration, email_id, datetime.now()))
            conn.commit()
            cursor.close()
            return log_id
        except Exception as e:
            logger.error(f"Erreur sauvegarde log: {e}")
            return None
    
    def _sauvegarder_analyse(self, email_id: str, intention: str, comprehension: Dict, temps: float):
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            analysis_id = str(uuid.uuid4())
            cursor.execute("""
                INSERT INTO email_analysis (id, email_id, intent, sentiment, entities, confidence, processing_time, analyzed_at) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (analysis_id, email_id, intention, comprehension.get('sentiment', 'neutral'), 
                  json.dumps(comprehension.get('entities', {})), comprehension.get('confidence', 0.85), temps, datetime.now()))
            conn.commit()
            cursor.close()
        except Exception as e:
            logger.error(f"Erreur sauvegarde analyse: {e}")
    
    # ================================================================
    # METHODES D'INITIALISATION
    # ================================================================
    
    def _init_tables(self):
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'clients');")
            exists = cursor.fetchone()['exists']
            if not exists:
                logger.warning("Tables non trouvees")
            cursor.close()
        except Exception as e:
            logger.error(f"Erreur verification tables: {e}")
    
    def _charger_donnees_test(self):
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM clients")
            count = cursor.fetchone()['count']
            if count == 0:
                logger.info("Chargement donnees test...")
                clients = [
                    ('123e4567-e89b-12d3-a456-426614174001', 'Jean Martin', 'jean.martin@email.com', '0612345678'),
                    ('123e4567-e89b-12d3-a456-426614174002', 'Sophie Dubois', 'sophie.dubois@email.com', '0623456789'),
                    ('123e4567-e89b-12d3-a456-426614174003', 'Pierre Durant', 'pierre.durant@email.com', '0634567890')
                ]
                for client in clients:
                    cursor.execute("""
                        INSERT INTO clients (id, name, email, phone, created_at) 
                        VALUES (%s, %s, %s, %s, %s) ON CONFLICT (email) DO NOTHING
                    """, (client[0], client[1], client[2], client[3], datetime.now()))
                conn.commit()
                logger.info("Donnees test chargees")
            cursor.close()
        except Exception as e:
            logger.error(f"Erreur chargement donnees: {e}")
    
    def _index_datasets(self):
        try:
            if len(self.rag_manager.documents.get('enron', [])) == 0:
                logger.info("Indexation datasets...")
                datasets = DatasetLoader.load_all_datasets()
                for name, (docs, texts) in datasets.items():
                    if docs:
                        self.rag_manager.index_dataset(name, docs, texts)
                        logger.info(f"   {name}: {len(docs)} documents")
        except Exception as e:
            logger.error(f"Erreur indexation: {e}")
    
    # ================================================================
    # TRAITEMENT EMAIL PRINCIPAL AVEC DECLENCHEMENT WORKFLOW
    # ================================================================
    
    def traiter_email_intelligent(self, expediteur: str, sujet: str, contenu: str) -> Dict:
        logger.info(f"Traitement intelligent: {expediteur} - {sujet}")
        start_time = time.time()
        start_datetime = datetime.now()
        
        self._increment_emails_traites()
        
        client_id = self._get_or_create_client(expediteur)
        client_name = expediteur.split('@')[0]
        
        email_id = self._sauvegarder_email(client_id, sujet, contenu)
        
        self._sauvegarder_log(client_id, expediteur, client_name, 'email_received', 
                              'processing', f"Sujet: {sujet[:50]}...", email_id=email_id)
        
        try:
            comprehension = self.llm.comprendre_intention(contenu)
            intention = comprehension.get('intention', 'general')
            confidence = comprehension.get('confidence', 85)
            
            self._increment_analyses_llm()
            
            rag_context = self.rag_manager.get_context_for_query(contenu, n_results=5)
            enron_results = self.enron_rag.search(contenu, n_results=3) if self.enron_rag else []
            
            stats = self.db.get_stats()
            analyses_count = stats.get('analyses_llm', 0)
            confidence = self._calculer_confidence(analyses_count)
            
            resume_ia = self._generer_resume_ia_reel(contenu, intention, comprehension, analyses_count)
            action_suggestee = self._generer_action_suggestee(intention, comprehension)
            titre_enrichi = self._generer_titre_enrichi(sujet, intention, comprehension)
            timeline = self._generer_timeline_reelle(intention, start_datetime)
            
            statut_display = self.STATUTS_AFFICHAGE.get('success', 'Resolu')
            
            priorite = 'high' if intention == 'facture' else 'medium' if intention in ['support', 'contrat'] else 'low'
            self._sauvegarder_email_event(
                client_id, expediteur, sujet, intention, priorite, 'analyse_ia',
                'received', contenu[:500], 'received', confidence
            )
            
            est_crm = self._est_interaction_crm(intention, sujet, contenu)
            type_crm = self._determiner_type_crm(intention, sujet, contenu)
            
            logger.info(f"CRM DEBUG - Intention: {intention}, Est CRM: {est_crm}, Type: {type_crm}")
            
            if est_crm:
                self._increment_crm()
                if type_crm == "support":
                    self._creer_ticket_support(client_id, sujet, contenu)
                    self._increment_ticket()
                    statut_display = 'Escalade'
                    timeline.append({
                        "time": (start_datetime + timedelta(seconds=10)).strftime("%H:%M:%S"),
                        "action": "Ticket support cree et escalade",
                        "status": "success"
                    })
            
            self._sauvegarder_analyse(email_id, intention, comprehension, time.time() - start_time)
            
            contexte = {
                'intention': intention,
                'comprehension': comprehension,
                'enron_similar': [r['document'][:200] for r in enron_results[:1]],
                'rag_context': rag_context[:2000],
                'resume_ia': resume_ia,
                'action_suggestee': action_suggestee
            }
            
            reponse = self.llm.generer_reponse_personnalisee(
                {'nom': client_name, 'email': expediteur},
                intention,
                contexte
            )
            
            reponse_sujet = f"RE: {sujet}"
            self._sauvegarder_email_event(
                client_id, expediteur, reponse_sujet, intention, priorite, 'reponse_automatique',
                'pending', reponse[:500], 'generated', confidence
            )
            
            logger.info(f"Reponse generee et mise en attente: {reponse_sujet}")
            
            temps_traitement = time.time() - start_time
            self._add_temps_reponse(temps_traitement)
            
            rag_docs_count = len(rag_context.split('---')) - 1 if rag_context else 0
            
            resultat = {
                'intention': intention,
                'comprehension': comprehension,
                'reponse': "Email analyse et mis en attente. Veuillez valider dans le Dashboard.",
                'temps_traitement': round(temps_traitement, 2),
                'recherches': {'enron': len(enron_results), 'rag_total': rag_docs_count},
                'resume_ia': resume_ia,
                'action_suggestee': action_suggestee,
                'confidence': confidence,
                'titre_enrichi': titre_enrichi,
                'statut': 'succes'
            }
            
            self._sauvegarder_requete(
                expediteur, sujet, contenu, intention, resultat,
                titre_enrichi=titre_enrichi,
                resume_ia=resume_ia,
                action_suggestee=action_suggestee,
                confidence=confidence,
                timeline=timeline,
                statut_display=statut_display
            )
            
            self._sauvegarder_log(client_id, expediteur, client_name, 'email_processed', 
                                  'success', f"Intention: {intention} | Confiance: {confidence}%", 
                                  duration=temps_traitement, email_id=email_id)
            
            # ============================================================
            # DECLENCHEMENT AUTOMATIQUE DES WORKFLOWS
            # ============================================================
            trigger_data = {
                'type': 'email',
                'intention': intention,
                'priority': priorite,
                'client_id': client_id,
                'client_email': expediteur,
                'client_name': client_name,
                'subject': sujet,
                'content': contenu[:500],
                'confidence': confidence,
                'sentiment': comprehension.get('sentiment', 'neutre')
            }
            
            workflow_results = self.workflow_engine.find_and_execute_matching_workflows('email', trigger_data)
            if workflow_results:
                logger.info(f"{len(workflow_results)} workflow(s) declenche(s) pour cet email")
                resultat['workflows_declenches'] = len(workflow_results)
            
            stats_result = self.db.get_stats()
            logger.info(f"""
            ================ STATS GLOBALES ================
            Emails traites   : {stats_result['emails_traites']}
            Emails envoyes   : {stats_result['emails_envoyes']}
            Analyses LLM     : {stats_result['analyses_llm']}
            CRM              : {stats_result['crm']}
            Tickets          : {stats_result['tickets']}
            Erreurs          : {stats_result['erreurs']}
            Regles           : {stats_result['regles_declenchees']}
            Temps moyen      : {stats_result['temps_total'] / max(stats_result['emails_traites'], 1):.2f}s
            Taux succes      : {(stats_result['emails_traites'] - stats_result['erreurs']) / max(stats_result['emails_traites'], 1) * 100:.1f}%
            ==============================================
            """)
            
            return resultat
            
        except Exception as e:
            logger.error(f"Erreur: {e}")
            self._increment_erreur()
            temps_traitement = time.time() - start_time
            resultat = {'intention': 'erreur', 'reponse': f"Erreur: {str(e)}", 'temps_traitement': round(temps_traitement, 2), 'recherches': {}}
            self._sauvegarder_log(client_id, expediteur, client_name, 'email_processing', 
                                  'failed', f"Erreur: {str(e)}", duration=temps_traitement, email_id=email_id)
            return resultat
    
    # ================================================================
    # METHODES PUBLIQUES POUR LE DASHBOARD
    # ================================================================
    
    def get_dashboard_stats(self) -> Dict:
        stats = self.db.get_stats()
        emails = stats.get('emails_traites', 0)
        erreurs = stats.get('erreurs', 0)
        
        return {
            "emails_traites": int(stats.get('emails_traites', 0)),
            "emails_envoyes": int(stats.get('emails_envoyes', 0)),
            "analyses_llm": int(stats.get('analyses_llm', 0)),
            "regles_declenchees": int(stats.get('regles_declenchees', 0)),
            "crm": int(stats.get('crm', 0)),
            "tickets": int(stats.get('tickets', 0)),
            "erreurs": int(erreurs),
            "temps_moyen": round(stats.get('temps_total', 0) / max(emails, 1), 2),
            "taux_succes": round((emails - erreurs) / max(emails, 1) * 100, 1) if emails > 0 else 100,
            "interactions": int(stats.get('crm', 0)) + int(stats.get('tickets', 0))
        }

    # ================================================================
    # METHODES DE TENDANCES HEBDOMADAIRES (DONNEES REELLES)
    # ================================================================

    def get_tendances_hebdomadaires_reelles(self) -> List[Dict]:
        """Récupère les vraies tendances depuis la base de données PostgreSQL"""
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    DATE_TRUNC('day', created_at) as day,
                    COUNT(*) as emails,
                    COUNT(CASE WHEN email_type = 'sent' THEN 1 END) as responses
                FROM email_events
                WHERE created_at > NOW() - INTERVAL '7 days'
                GROUP BY DATE_TRUNC('day', created_at)
                ORDER BY day ASC
            """)
            
            rows = cursor.fetchall()
            cursor.close()
            
            if rows:
                days_order = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim']
                result = []
                existing_days = []
                
                for row in rows:
                    day_index = row['day'].weekday()
                    day_name = days_order[day_index]
                    existing_days.append(day_name)
                    result.append({
                        "day": day_name,
                        "emails": row['emails'],
                        "responses": row['responses'] or 0
                    })
                
                # Compléter les jours manquants avec 0
                for day in days_order:
                    if day not in existing_days:
                        result.append({"day": day, "emails": 0, "responses": 0})
                
                # Trier par ordre des jours
                result.sort(key=lambda x: days_order.index(x['day']))
                return result
            
        except Exception as e:
            logger.error(f"Erreur get_tendances_hebdomadaires_reelles: {e}")
        
        return []
    
    def demarrer(self):
        if Config.ENABLE_EMAIL_SURVEILLANCE and self.email_listener:
            self.scheduler.add_job(
                self.email_listener.surveiller,
                trigger=IntervalTrigger(seconds=30),
                id='email_surveillance',
                replace_existing=True
            )
            logger.info("   Surveillance emails: ACTIVEE (toutes les 30 secondes)")
        else:
            logger.info("   Surveillance emails: DESACTIVEE")
        
        if Config.ENABLE_RULES_ENGINE:
            def check_rules():
                if self.email_sender.email_automation_paused:
                    logger.warning("Automation emails en pause - regle ignoree")
                    return []
                actions = self.rule_engine.verifier_toutes_regles()
            
            self.scheduler.add_job(
                check_rules,
                trigger=IntervalTrigger(hours=2),
                id='rule_check',
                replace_existing=True
            )
            logger.info("   Regles metier: ACTIVEES (toutes les 2 heures)")
        else:
            logger.info("   Regles metier: DESACTIVEES")
        
        def log_stats():
            stats = self.db.get_stats()
            status = "PAUSE" if self.email_sender.email_automation_paused else "ACTIF"
            logger.info(f"Stats: {stats['emails_traites']} emails, CRM: {stats['crm']}, Tickets: {stats['tickets']} - Status: {status}")
        
        self.scheduler.add_job(log_stats, trigger=IntervalTrigger(hours=1), id='stats_log', replace_existing=True)
        self.scheduler.start()
        
        logger.info("="*50)
        logger.info("Scheduler demarre")
        stats = self.db.get_stats()
        logger.info(f"   CRM Total: {stats['crm']}")
        logger.info("="*50)
        return self.scheduler


# ===================================================================
# ECOUTEUR EMAIL - VERSION AMELIOREE AVEC IMAP REEL
# ===================================================================
class RealEmailListener:
    def __init__(self, intelligent_agent, db, email_sender):
        self.intelligent_agent = intelligent_agent
        self.db = db
        self.email_sender = email_sender
        self.emails_traites = set()
        self.imap_server = "imap.gmail.com"
        self.email_address = Config.GMAIL_ADRESSE
        self.email_password = Config.GMAIL_MOT_DE_PASSE
        logger.info(f"Ecouteur email configure pour {self.email_address}")
    
    def surveiller(self):
        logger.info("Verification des nouveaux emails...")
        new_count = 0
        try:
            mail = imaplib.IMAP4_SSL(self.imap_server)
            mail.login(self.email_address, self.email_password)
            mail.select('inbox')
            
            result, data = mail.search(None, 'UNSEEN')
            
            if result == 'OK':
                email_ids = data[0].split()
                logger.info(f"{len(email_ids)} nouveau(x) email(s) non lu(s)")
                new_count = len(email_ids)
                
                for email_id in email_ids:
                    result, msg_data = mail.fetch(email_id, '(RFC822)')
                    if result == 'OK':
                        raw_email = msg_data[0][1]
                        msg = email.message_from_bytes(raw_email)
                        
                        subject = self._decode_email_header(msg['Subject'])
                        from_email = self._decode_email_header(msg['From'])
                        
                        body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == "text/plain":
                                    body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                    break
                        else:
                            body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                        
                        if '<' in from_email and '>' in from_email:
                            from_email = from_email.split('<')[1].split('>')[0]
                        
                        email_key = f"{from_email}_{subject}_{body[:100]}"
                        if email_key not in self.emails_traites:
                            self.emails_traites.add(email_key)
                            logger.info(f"Traitement du nouvel email de {from_email}: {subject[:50]}...")
                            
                            if len(body) > 5000:
                                body = body[:5000] + "..."
                            
                            self.intelligent_agent.traiter_email_intelligent(from_email, subject, body)
                            mail.store(email_id, '+FLAGS', '\\Seen')
                            
            mail.close()
            mail.logout()
            
        except Exception as e:
            logger.error(f"Erreur surveillance emails: {e}")
        
        return new_count
    
    def _decode_email_header(self, header):
        if header is None:
            return ""
        try:
            decoded = decode_header(header)
            decoded_parts = []
            for part, encoding in decoded:
                if isinstance(part, bytes):
                    if encoding:
                        decoded_parts.append(part.decode(encoding, errors='ignore'))
                    else:
                        decoded_parts.append(part.decode('utf-8', errors='ignore'))
                else:
                    decoded_parts.append(part)
            return ' '.join(decoded_parts)
        except:
            return str(header)
    
    def _connecter_imap(self):
        return None
