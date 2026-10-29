"""
gmail_oauth.py - Authentification OAuth2 pour Gmail
"""
import os
import pickle
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify'
]

class GmailOAuth:
    def __init__(self, credentials_file='credentials.json', token_file='token.pickle'):
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.service = None
        self._authenticate()
    
    def _authenticate(self):
        """Authentifie et crée le service Gmail"""
        creds = None
        
        # Charge les tokens existants
        if os.path.exists(self.token_file):
            with open(self.token_file, 'rb') as token:
                creds = pickle.load(token)
        
        # Si pas de token valide, demande l'authentification
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(self.credentials_file):
                    raise FileNotFoundError(
                        "Fichier credentials.json manquant. "
                        "Téléchargez-le depuis Google Cloud Console."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, SCOPES
                )
                creds = flow.run_local_server(port=0)
            
            # Sauvegarde le token
            with open(self.token_file, 'wb') as token:
                pickle.dump(creds, token)
        
        self.service = build('gmail', 'v1', credentials=creds)
        print("✅ Authentification OAuth2 réussie")
        return self.service
    
    def send_email(self, to: str, subject: str, body: str) -> bool:
        """Envoie un email via l'API Gmail"""
        try:
            message = MIMEText(body, 'plain', 'utf-8')
            message['to'] = to
            message['subject'] = subject
            message['from'] = 'me'
            
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
            
            send_message = self.service.users().messages().send(
                userId='me',
                body={'raw': raw_message}
            ).execute()
            
            print(f"✅ Email envoyé à {to} - ID: {send_message['id']}")
            return True
            
        except HttpError as error:
            print(f"❌ Erreur envoi email: {error}")
            return False
        except Exception as e:
            print(f"❌ Erreur: {e}")
            return False
    
    def get_unread_messages(self, max_results=10):
        """Récupère les emails non lus"""
        try:
            results = self.service.users().messages().list(
                userId='me',
                labelIds=['INBOX'],
                q='is:unread',
                maxResults=max_results
            ).execute()
            
            messages = results.get('messages', [])
            email_list = []
            
            for msg in messages:
                msg_data = self.service.users().messages().get(
                    userId='me',
                    id=msg['id'],
                    format='full'
                ).execute()
                
                headers = msg_data['payload']['headers']
                subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Sans sujet')
                from_email = next((h['value'] for h in headers if h['name'] == 'From'), 'Inconnu')
                
                # Extraction du corps du message
                body = ""
                if 'parts' in msg_data['payload']:
                    for part in msg_data['payload']['parts']:
                        if part['mimeType'] == 'text/plain':
                            body = base64.urlsafe_b64decode(
                                part['body']['data']
                            ).decode('utf-8', errors='ignore')
                            break
                elif 'body' in msg_data['payload'] and 'data' in msg_data['payload']['body']:
                    body = base64.urlsafe_b64decode(
                        msg_data['payload']['body']['data']
                    ).decode('utf-8', errors='ignore')
                
                email_list.append({
                    'id': msg['id'],
                    'from': from_email,
                    'subject': subject,
                    'body': body,
                    'received_at': msg_data.get('internalDate', '')
                })
            
            return email_list
            
        except HttpError as error:
            print(f"❌ Erreur lecture emails: {error}")
            return []
    
    def mark_as_read(self, message_id: str):
        """Marque un email comme lu"""
        try:
            self.service.users().messages().modify(
                userId='me',
                id=message_id,
                body={'removeLabelIds': ['UNREAD']}
            ).execute()
            return True
        except Exception as e:
            print(f"Erreur marquage lu: {e}")
            return False


# Test rapide
if __name__ == "__main__":
    gmail = GmailOAuth()
    print("Test d'envoi...")
    gmail.send_email("test@example.com", "Test OAuth2", "Ceci est un test")