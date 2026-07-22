import os
import io
import json
import uuid
import base64
import zipfile
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from pymongo import MongoClient
import pika
import google.generativeai as genai
import bcrypt
import jwt
from functools import wraps
import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid
import requests
from PIL import Image

app = Flask(__name__)
# Autoriser les requêtes cross-origin depuis notre futur front-end React
CORS(app)
JWT_SECRET = os.environ.get('JWT_SECRET', 'opthia_secret_key_3124976')

# --- CONFIGURATION ET CONNEXIONS ---
db_client = None
db = None

# --- MIDDLEWARE & SECURE AUTHENTICATION ---
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            try:
                token = auth_header.split(" ")[1]
            except IndexError:
                return jsonify({'error': 'Jeton de session mal formé'}), 401
        
        if not token:
            return jsonify({'error': 'Authentification requise'}), 401
        
        try:
            data = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            if db is None:
                return jsonify({'error': 'MongoDB indisponible'}), 503
            current_user = db.users.find_one({'email': data['email']})
            if not current_user:
                return jsonify({'error': 'Utilisateur introuvable'}), 401
            current_user.pop('password', None)
            current_user.pop('_id', None)
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Session expirée'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Session invalide'}), 401
        
        return f(current_user, *args, **kwargs)
    return decorated

# Configurer Gemini
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    print("Gemini API configurée avec succès.")
else:
    print("AVERTISSEMENT: GEMINI_API_KEY non fournie. Le RAG fonctionnera en mode simulation.")


def init_connections():
    """Initialise la connexion à MongoDB."""
    global db_client, db

    mongo_uri = os.environ.get('MONGO_URI', 'mongodb://mongo:27017/ophtia')
    try:
        db_client = MongoClient(mongo_uri)
        db = db_client.get_default_database()
        db.command('ping')
        print("Connexion à MongoDB réussie.")
    except Exception as e:
        print(f"ERREUR: Impossible de se connecter à MongoDB: {e}")
        db = None


def publish_task(queue_name, task_data):
    """Publie une tâche de diagnostic dans RabbitMQ."""
    try:
        rabbitmq_url = os.environ.get('RABBITMQ_URL', 'amqp://guest:guest@rabbitmq:5672/%2F')
        connection = pika.BlockingConnection(pika.URLParameters(rabbitmq_url))
        channel = connection.channel()
        channel.queue_declare(queue=queue_name, durable=True)

        channel.basic_publish(
            exchange='',
            routing_key=queue_name,
            body=json.dumps(task_data),
            properties=pika.BasicProperties(delivery_mode=2)  # Message persistant
        )
        connection.close()
        return True
    except Exception as e:
        print(f"[✗] Erreur publication RabbitMQ: {e}")
        return False


# --- ENDPOINTS DE L'API ---

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "status": "online",
        "service": "Opthia V2 Backend API",
        "gemini_enabled": bool(GEMINI_API_KEY)
    })


@app.route('/predict_with_gradcam', methods=['POST'])
def predict_with_gradcam():
    """
    Endpoint de diagnostic unitaire.
    Accepte du JSON contenant { image_base64, model_type, filename, patient_name }.
    """
    task_id = str(uuid.uuid4())
    try:
        if not request.is_json:
            return jsonify({'error': 'Format de requête invalide, JSON requis'}), 400

        data = request.get_json()
        image_base64 = data.get('image_base64')
        model_type = data.get('model_type', 'rd')
        filename = data.get('filename', 'image.png')
        patient_name = data.get('patient_name', 'Patient Anonyme')

        if not image_base64:
            return jsonify({'error': 'image_base64 manquant'}), 400

        if model_type not in ['rd', 'glaucoma']:
            return jsonify({'error': 'model_type invalide'}), 400

        if not image_base64.startswith('data:'):
            image_base64 = f"data:image/png;base64,{image_base64}"

        task_data = {
            'task_id': task_id,
            'image_base64': image_base64,
            'model_type': model_type,
            'filename': filename,
            'patient_name': patient_name,
            'timestamp': datetime.utcnow().isoformat()
        }

        # Publier au worker
        if publish_task('diagnostic_tasks', task_data):
            return jsonify({
                'status': 'submitted',
                'task_id': task_id,
                'poll_url': f'/result/{task_id}'
            }), 202
        else:
            return jsonify({'error': 'Impossible de soumettre la tâche'}), 500

    except Exception as e:
        return jsonify({'error': f"Erreur serveur : {str(e)}"}), 500


@app.route('/result/<task_id>', methods=['GET'])
def get_result(task_id):
    """Récupère le résultat d'un diagnostic."""
    try:
        if db is None:
            return jsonify({'error': 'MongoDB non disponible'}), 503

        result = db.diagnostic_results.find_one({'task_id': task_id})
        if result:
            result.pop('_id', None)
            return jsonify(result), 200
        else:
            return jsonify({'status': 'pending', 'message': 'Diagnostic en cours'}), 202
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- SYSTEME DE BATCH (TRAITEMENT PAR LOTS) ---

@app.route('/api/batch/submit', methods=['POST'])
@token_required
def submit_batch(current_user):
    """
    Soumet un lot d'images (jusqu'à 100).
    Format attendu : { batch_name, patients: [ { patient_name, image_base64, model_type, filename } ] }
    """
    try:
        if not request.is_json:
            return jsonify({'error': 'Format de requête invalide, JSON requis'}), 400

        data = request.get_json()
        batch_id = str(uuid.uuid4())
        batch_name = data.get('batch_name', f"Lot du {datetime.now().strftime('%d/%m/%Y %H:%M')}")
        patients = data.get('patients', [])

        if not patients:
            return jsonify({'error': 'Aucun patient fourni'}), 400

        if len(patients) > 100:
            return jsonify({'error': 'La taille du lot ne peut pas dépasser 100 patients'}), 400

        # Enregistrer l'initialisation du batch en DB
        batch_info = {
            'batch_id': batch_id,
            'batch_name': batch_name,
            'total_count': len(patients),
            'status': 'processing',
            'user_email': current_user['email'],
            'created_at': datetime.utcnow().isoformat(),
            'tasks': []
        }

        for patient in patients:
            task_id = str(uuid.uuid4())
            image_base64 = patient.get('image_base64')
            model_type = patient.get('model_type', 'rd')
            filename = patient.get('filename', 'image.png')
            patient_name = patient.get('patient_name', 'Patient')

            # Nettoyer base64 pour la conversion DICOM
            pure_base64 = image_base64
            if pure_base64.startswith('data:'):
                pure_base64 = pure_base64.split(',')[1]

            # Conversion et envoi DICOM asynchrone / automatique sur Orthanc
            orthanc_id = None
            extracted_patient_name = patient_name
            extracted_patient_id = str(uuid.uuid4())[:8]
            extracted_birth_date = '19800101'

            try:
                img_data = base64.b64decode(pure_base64)
                
                # Vérifier si l'image importée est un DICOM natif (en-tête DICOM commence par DICM à l'octet 128)
                is_native_dicom = False
                if len(img_data) > 132 and img_data[128:132] == b"DICM":
                    is_native_dicom = True

                if is_native_dicom:
                    # Lire le DICOM existant
                    ds_existing = pydicom.dread_file(io.BytesIO(img_data))
                    
                    # Extraire les métadonnées existantes
                    if getattr(ds_existing, 'PatientName', None):
                        extracted_patient_name = str(ds_existing.PatientName).replace('^', ' ')
                    if getattr(ds_existing, 'PatientID', None):
                        extracted_patient_id = str(ds_existing.PatientID)
                    if getattr(ds_existing, 'PatientBirthDate', None):
                        extracted_birth_date = str(ds_existing.PatientBirthDate)

                    # Pour l'IA (modèle de vision), extraire le pixel_array
                    if hasattr(ds_existing, 'pixel_array'):
                        pixel_arr = ds_existing.pixel_array
                        # Convertir le pixel array en image PIL RGB puis en base64 pour le worker
                        # Normaliser si nécessaire
                        if pixel_arr.max() > 0:
                            pixel_arr = ((pixel_arr - pixel_arr.min()) / (pixel_arr.max() - pixel_arr.min()) * 255).astype(np.uint8)
                        pil_img = Image.fromarray(pixel_arr).convert('RGB')
                        buffered = io.BytesIO()
                        pil_img.save(buffered, format="PNG")
                        image_base64 = f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode('utf-8')}"

                    # Envoyer directement le DICOM brut reçu à Orthanc
                    orthanc_url = os.environ.get('ORTHANC_URL', 'http://orthanc:8042/instances')
                    auth_env = os.environ.get('ORTHANC_AUTH')
                    auth_tuple = tuple(auth_env.split(':')) if auth_env else None
                    res = requests.post(orthanc_url, data=img_data, auth=auth_tuple)
                    if res.status_code in [200, 201]:
                        orthanc_id = res.json().get('ID')
                else:
                    # Ce n'est pas un DICOM natif, on procède à sa création comme avant
                    img = Image.open(io.BytesIO(img_data)).convert('L')
                    img_np = img.tobytes()

                    filename_dcm = f"dicom_{uuid.uuid4()}.dcm"
                    file_meta = Dataset()
                    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
                    file_meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.1.1.7'
                    file_meta.MediaStorageSOPInstanceUID = generate_uid()
                    file_meta.ImplementationClassUID = generate_uid()

                    ds = FileDataset(filename_dcm, {}, file_meta=file_meta, preamble=b"\0" * 128)
                    ds.PatientName = patient_name.replace(' ', '^')
                    ds.PatientID = extracted_patient_id
                    ds.PatientBirthDate = extracted_birth_date
                    ds.PatientSex = 'O'
                    ds.StudyInstanceUID = generate_uid()
                    ds.SeriesInstanceUID = generate_uid()
                    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
                    ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
                    ds.Modality = 'OT'
                    ds.SamplesPerPixel = 1
                    ds.PhotometricInterpretation = "MONOCHROME2"
                    ds.PixelRepresentation = 0
                    ds.HighBit = 7
                    ds.BitsStored = 8
                    ds.BitsAllocated = 8
                    ds.Rows = img.height
                    ds.Columns = img.width
                    ds.PixelData = img_np

                    # Sauvegarder en mémoire sous forme de bytes avec les paramètres de formatage explicites
                    ds.is_little_endian = True
                    ds.is_implicit_VR = False

                    dicom_io = io.BytesIO()
                    pydicom.write_file(dicom_io, ds, write_like_original=False)
                    dicom_bytes = dicom_io.getvalue()

                    orthanc_url = os.environ.get('ORTHANC_URL', 'http://orthanc:8042/instances')
                    auth_env = os.environ.get('ORTHANC_AUTH')
                    auth_tuple = tuple(auth_env.split(':')) if auth_env else None
                    res = requests.post(orthanc_url, data=dicom_bytes, auth=auth_tuple)
                    if res.status_code in [200, 201]:
                        orthanc_id = res.json().get('ID')

            except Exception as e:
                print(f"[DICOM Processing/Conversion error]: {e}")

            task_data = {
                'task_id': task_id,
                'batch_id': batch_id,
                'image_base64': image_base64,
                'model_type': model_type,
                'filename': filename,
                'patient_name': extracted_patient_name,
                'user_email': current_user['email'],
                'orthanc_id': orthanc_id,
                'timestamp': datetime.utcnow().isoformat()
            }

            # Enregistrer la tâche dans le batch
            batch_info['tasks'].append(task_id)

            # Publier la tâche individuelle dans RabbitMQ
            publish_task('diagnostic_tasks', task_data)

        if db is not None:
            db.batches.insert_one(batch_info)

        return jsonify({
            'status': 'submitted',
            'batch_id': batch_id,
            'total_count': len(patients)
        }), 202

    except Exception as e:
        return jsonify({'error': f"Erreur lors de la soumission du lot: {str(e)}"}), 500


@app.route('/api/batch/status/<batch_id>', methods=['GET'])
def get_batch_status(batch_id):
    """Renvoie le statut et l'avancement d'un batch de diagnostics."""
    try:
        if db is None:
            return jsonify({'error': 'MongoDB non disponible'}), 503

        batch = db.batches.find_one({'batch_id': batch_id})
        if not batch:
            return jsonify({'error': 'Lot non trouvé'}), 404

        # Compter les résultats terminés pour ce batch
        completed_results = list(db.diagnostic_results.find({'batch_id': batch_id}, {'task_id': 1, 'status': 1, 'result.prediction_class': 1}))
        
        completed_count = len(completed_results)
        failed_count = sum(1 for r in completed_results if r.get('status') == 'failed')
        success_count = completed_count - failed_count

        status = 'processing'
        if completed_count >= batch['total_count']:
            status = 'completed'
            db.batches.update_one({'batch_id': batch_id}, {'$set': {'status': 'completed'}})

        return jsonify({
            'batch_id': batch_id,
            'batch_name': batch['batch_name'],
            'total': batch['total_count'],
            'completed': completed_count,
            'success': success_count,
            'failed': failed_count,
            'status': status
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/batch/results/<batch_id>', methods=['GET'])
def get_batch_results(batch_id):
    """Récupère l'ensemble des résultats d'un batch."""
    try:
        if db is None:
            return jsonify({'error': 'MongoDB non disponible'}), 503

        results = list(db.diagnostic_results.find({'batch_id': batch_id}))
        for r in results:
            r.pop('_id', None)

        return jsonify(results), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/diagnostics', methods=['GET'])
@token_required
def get_all_diagnostics(current_user):
    """Récupère l'ensemble de tous les diagnostics de la base de données pour l'utilisateur connecté."""
    try:
        if db is None:
            return jsonify({'error': 'MongoDB non disponible'}), 503

        # Si l'utilisateur est admin, il a accès à tout (pour le réentraînement et validation globale)
        # Sinon, on filtre par son propre email OU les anciens examens n'ayant pas de user_email (non-associés)
        query = {}
        if current_user.get('role') != 'admin':
            query = {
                '$or': [
                    {'user_email': current_user['email']},
                    {'user_email': {'$exists': False}}
                ]
            }

        results = list(db.diagnostic_results.find(query).sort('timestamp', -1))
        for r in results:
            r.pop('_id', None)

        return jsonify(results), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/diagnostic/delete/<task_id>', methods=['DELETE'])
@token_required
def delete_diagnostic(current_user, task_id):
    """Supprime définitivement un diagnostic si l'utilisateur en est le propriétaire."""
    try:
        if db is None:
            return jsonify({'error': 'MongoDB non disponible'}), 503

        # Vérifier si l'utilisateur est le propriétaire ou s'il est admin
        diag = db.diagnostic_results.find_one({'task_id': task_id})
        if not diag:
            return jsonify({'error': 'Diagnostic introuvable'}), 404

        if current_user.get('role') != 'admin' and diag.get('user_email') != current_user['email']:
            return jsonify({'error': 'Non autorisé à supprimer cet examen'}), 403

        res = db.diagnostic_results.delete_one({'task_id': task_id})
        if res.deleted_count > 0:
            return jsonify({'status': 'success', 'message': 'Diagnostic supprime avec succes'}), 200
        else:
            return jsonify({'error': 'Diagnostic introuvable'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- INTÉGRATION DICOM & PACS (ORTHANC) ---

@app.route('/api/dicom/upload', methods=['POST'])
@token_required
def upload_dicom_image(current_user):
    """
    Reçoit une image du PhoneCapture et crée un fichier DICOM stocké sur Orthanc PACS.
    JSON attendu : { patient_name, patient_id, image_base64, birth_date }
    """
    try:
        data = request.get_json()
        patient_name = data.get('patient_name', 'Patient Anonyme')
        patient_id = data.get('patient_id', str(uuid.uuid4())[:8])
        image_base64 = data.get('image_base64')
        birth_date = data.get('birth_date')  # Format attendu : JJMMAAAA
        formatted_dicom_birth_date = '19800101'
        
        # Validation de la date de naissance au format JJMMAAAA
        if birth_date:
            # Vérifier la longueur
            if len(birth_date) != 8 or not birth_date.isdigit():
                return jsonify({'error': 'La date de naissance doit être au format JJMMAAAA (8 chiffres)'}), 400
            
            # Valider l'existence de la date et sa cohérence par rapport au jour actuel
            try:
                parsed_birth_date = datetime.strptime(birth_date, '%d%m%Y')
                if parsed_birth_date > datetime.now():
                    return jsonify({'error': 'La date de naissance ne peut pas être dans le futur'}), 400
                # Reconvertir au format DICOM standard (AAAAMMJJ)
                formatted_dicom_birth_date = parsed_birth_date.strftime('%Y%m%d')
            except ValueError:
                return jsonify({'error': 'Date de naissance invalide (vérifiez les jours et mois)'}), 400

        if not image_base64:
            return jsonify({'error': 'Image base64 manquante'}), 400

        # Décoder l'image base64
        if image_base64.startswith('data:'):
            image_base64 = image_base64.split(',')[1]
        image_bytes = base64.b64decode(image_base64)
        
        # Charger avec PIL et convertir en niveaux de gris/RGB 8-bit pour DICOM
        img = Image.open(io.BytesIO(image_bytes)).convert('L') # niveaux de gris standard
        img_np = img.tobytes()

        # Configurer le dataset DICOM
        filename = f"dicom_{uuid.uuid4()}.dcm"
        file_meta = Dataset()
        file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        file_meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.1.1.7' # Secondary Capture Image Storage
        file_meta.MediaStorageSOPInstanceUID = generate_uid()
        file_meta.ImplementationClassUID = generate_uid()

        ds = FileDataset(filename, {}, file_meta=file_meta, preamble=b"\0" * 128)
        
        # Tags patients requis DICOM
        ds.PatientName = patient_name.replace(' ', '^')
        ds.PatientID = patient_id
        ds.PatientBirthDate = formatted_dicom_birth_date
        ds.PatientSex = 'O' # Other / Unknown
        ds.StudyInstanceUID = generate_uid()
        ds.SeriesInstanceUID = generate_uid()
        ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
        ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
        
        # Tags de l'image
        ds.Modality = 'OT' # Other
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.PixelRepresentation = 0
        ds.HighBit = 7
        ds.BitsStored = 8
        ds.BitsAllocated = 8
        ds.Rows = img.height
        ds.Columns = img.width
        ds.PixelData = img_np

        # Sauvegarder en mémoire sous forme de bytes avec les paramètres de formatage explicites
        ds.is_little_endian = True
        ds.is_implicit_VR = False
        
        dicom_io = io.BytesIO()
        pydicom.write_file(dicom_io, ds, write_like_original=False)
        dicom_bytes = dicom_io.getvalue()

        # Envoyer le fichier DICOM brut à Orthanc par REST
        orthanc_url = os.environ.get('ORTHANC_URL', 'http://orthanc:8042/instances')
        auth_env = os.environ.get('ORTHANC_AUTH')
        auth_tuple = tuple(auth_env.split(':')) if auth_env else None
        res = requests.post(orthanc_url, data=dicom_bytes, auth=auth_tuple)
        
        if res.status_code in [200, 201]:
            orthanc_data = res.json()
            orthanc_id = orthanc_data.get('ID')

            # Publier la tâche de diagnostic à RabbitMQ pour traitement IA et stockage MongoDB
            task_id = str(uuid.uuid4())
            
            # Recomposer une URI base64 valide pour le visualiseur du frontend
            full_base64 = image_base64
            if not full_base64.startswith('data:'):
                full_base64 = f"data:image/png;base64,{full_base64}"

            task_data = {
                'task_id': task_id,
                'image_base64': full_base64,
                'model_type': 'rd',  # valeur par défaut pour phonecapture, peut être ajustée
                'filename': filename,
                'patient_name': patient_name,
                'user_email': current_user['email'],
                'orthanc_id': orthanc_id,
                'timestamp': datetime.utcnow().isoformat()
            }

            from app import publish_task
            publish_task('diagnostic_tasks', task_data)

            return jsonify({
                'status': 'success',
                'message': 'DICOM stocké avec succès dans le PACS Orthanc et soumis à l\'analyse',
                'orthanc_id': orthanc_id,
                'task_id': task_id
            }), 201
        else:
            return jsonify({'error': f"Erreur Orthanc: {res.text}"}), res.status_code

    except Exception as e:
        return jsonify({'error': f"Erreur de conversion DICOM : {str(e)}"}), 500


# --- SYSTEME DE DISCUSSION & RAG MEDICAL ---

@app.route('/api/chat/<task_id>', methods=['GET'])
@token_required
def get_chat_history(current_user, task_id):
    """Récupère l'historique des discussions pour un diagnostic donné."""
    try:
        if db is None:
            return jsonify({'error': 'MongoDB non disponible'}), 503

        chat_record = db.chats.find_one({'task_id': task_id})
        if chat_record and 'messages' in chat_record:
            # Nettoyer l'historique
            messages = chat_record['messages']
            return jsonify(messages), 200
        
        return jsonify([]), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/chat', methods=['POST'])
@token_required
def chat_assistant(current_user):
    """
    Endpoint conversationnel basé sur un diagnostic patient spécifique.
    JSON attendu : { task_id, message }
    """
    try:
        if not GEMINI_API_KEY:
            return jsonify({
                'response': "[Mode Démo] Le service d'IA Gemini n'est pas configuré. Veuillez définir GEMINI_API_KEY dans votre fichier d'environnement. Diagnostic simulé correct : l'image ne présente pas de signe sévère."
            }), 200

        data = request.get_json()
        task_id = data.get('task_id')
        user_message = data.get('message')

        if not user_message:
            return jsonify({'error': 'Message manquant'}), 400

        # Récupérer les informations du diagnostic pour le RAG
        diagnostic = None
        if task_id and db is not None:
            diagnostic = db.diagnostic_results.find_one({'task_id': task_id})

        # Construire le prompt système contextualisé (allégé pour éviter les répétitions inutiles)
        context = ""
        if diagnostic:
            res = diagnostic.get('result', {})
            pathology = "Rétinopathie Diabétique" if diagnostic.get('model_type') == 'rd' else "Glaucome"
            pred = "Positif" if res.get('prediction_class') == 1 else "Négatif"
            recommandation = res.get('recommendation', '')
            
            context = f"""
            CONTEXTE PATIENT :
            - Pathologie recherchée : {pathology}
            - Diagnostic : {pred}
            - Recommandation initiale : {recommandation}
            """

        system_instruction = f"""
        Tu es l'assistant IA médical d'Opthia, spécialisé en ophtalmologie et en médecine générale.
        Ton rôle est de répondre aux questions de l'utilisateur concernant l'œil, les examens de fond d'œil, les pathologies oculaires et les calculs ou concepts médicaux (comme la rotation oculaire, l'optométrie, etc.).
        
        Consignes importantes :
        1. Limite-toi STRICTEMENT au domaine médical (ophtalmologie, optométrie, médecine générale).
        2. Si l'utilisateur pose une question de mathématiques pures (ex: 2+2) ou une question générale hors-sujet qui n'a aucun lien avec la médecine ou l'ophtalmologie, réponds poliment que ton rôle est strictement limité à l'ophtalmologie et aux questions médicales.
        3. En revanche, si la question concerne un calcul médical, optométrique, physique ou anatomique lié à l'œil (comme l'angle de déviation, la rotation oculaire), réponds-y précisément.
        4. Ne répète pas inutilement le diagnostic ou le taux de confiance à chaque message.
        5. Reste concis et clair, sans produire de trop longs pavés répétitifs.
        6. Pour les questions cliniques de diagnostic, rappelle de manière fluide et simple qu'une consultation avec un ophtalmologue physique reste indispensable.
        7. Les formules simples de politesse (ex: Bonjour, ça va ?) sont acceptées et doivent recevoir une réponse brève et courtoise.
        {context}
        """

        # Charger l'historique des messages précédents depuis MongoDB pour le contexte conversationnel (RAG persistant)
        chat_history_str = ""
        if task_id and db is not None:
            chat_record = db.chats.find_one({'task_id': task_id})
            if chat_record and 'messages' in chat_record:
                for msg in chat_record['messages'][-6:]:  # Prendre les 6 derniers messages pour le contexte
                    role_name = "Utilisateur" if msg['role'] == 'user' else "IA Assistant"
                    chat_history_str += f"{role_name} : {msg['text']}\n"

        prompt = f"{system_instruction}\n\nHistorique récent de la discussion :\n{chat_history_str}\nNouvelle question : {user_message}"
        
        model = genai.GenerativeModel(
            model_name='gemini-2.5-flash'
        )

        response = model.generate_content(prompt)
        ai_response = response.text

        # Sauvegarder la nouvelle paire de messages dans MongoDB
        if task_id and db is not None:
            db.chats.update_one(
                {'task_id': task_id},
                {
                    '$push': {
                        'messages': {
                            '$each': [
                                {'role': 'user', 'text': user_message, 'timestamp': datetime.utcnow().isoformat()},
                                {'role': 'assistant', 'text': ai_response, 'timestamp': datetime.utcnow().isoformat()}
                            ]
                        }
                    },
                    '$set': {
                        'last_updated': datetime.utcnow().isoformat()
                    }
                },
                upsert=True
            )

        return jsonify({'response': ai_response}), 200

    except Exception as e:
        return jsonify({'error': f"Erreur Gemini: {str(e)}"}), 500


# --- ENDPOINTS D'ADMINISTRATION & EXPORT (TRAINING INTERNE) ---

@app.route('/api/diagnostic/confirm', methods=['POST'])
def confirm_diagnostic():
    """
    Permet à l'admin de confirmer ou de corriger manuellement le diagnostic d'une image.
    Format : { task_id, confirmed_class, comments }
    """
    try:
        if db is None:
            return jsonify({'error': 'MongoDB non disponible'}), 503

        data = request.get_json()
        task_id = data.get('task_id')
        confirmed_class = data.get('confirmed_class')  # 0 ou 1
        comments = data.get('comments', '')

        if task_id is None or confirmed_class is None:
            return jsonify({'error': 'Paramètres task_id ou confirmed_class manquants'}), 400

        db.diagnostic_results.update_one(
            {'task_id': task_id},
            {'$set': {
                'validated_by_admin': True,
                'confirmed_class': int(confirmed_class),
                'admin_comments': comments,
                'validation_timestamp': datetime.utcnow().isoformat()
            }}
        )

        return jsonify({'status': 'success', 'message': 'Diagnostic labellisé et validé.'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/admin/export', methods=['GET'])
def export_validated_data():
    """
    Génère et télécharge une archive ZIP contenant les images validées
    et un fichier JSON d'annotations pour le réentraînement en interne.
    """
    try:
        if db is None:
            return jsonify({'error': 'MongoDB non disponible'}), 503

        # Récupérer tous les diagnostics validés par l'admin
        validated_records = list(db.diagnostic_results.find({'validated_by_admin': True}))

        if not validated_records:
            return jsonify({'error': 'Aucune donnée validée disponible pour l\'export'}), 400

        # Créer un fichier ZIP en mémoire
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            annotations = []

            for index, record in enumerate(validated_records):
                task_id = record.get('task_id')
                model_type = record.get('model_type')
                confirmed_class = record.get('confirmed_class')
                image_base64 = record.get('image_base64') or record.get('result', {}).get('image_base64')

                if not image_base64:
                    # Tenter de retrouver dans les logs de la tâche d'origine
                    continue

                # Extraire le format et les bytes de l'image base64
                try:
                    header, encoded = image_base64.split(",", 1)
                    image_data = base64.b64decode(encoded)
                    file_ext = "png" if "png" in header else "jpg"
                    img_filename = f"image_{task_id}.{file_ext}"

                    # Écrire l'image dans le ZIP
                    zip_file.writestr(f"images/{img_filename}", image_data)

                    # Ajouter aux annotations
                    annotations.append({
                        'task_id': task_id,
                        'image_path': f"images/{img_filename}",
                        'model_type': model_type,
                        'confirmed_class': confirmed_class,
                        'admin_comments': record.get('admin_comments', ''),
                        'validated_at': record.get('validation_timestamp')
                    })
                except Exception as ex:
                    print(f"Erreur d'export pour {task_id}: {ex}")
                    continue

            # Écrire le fichier d'annotations JSON
            zip_file.writestr("annotations.json", json.dumps(annotations, indent=2))

        memory_file.seek(0)
        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f"opthia_dataset_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
        )

    except Exception as e:
        return jsonify({'error': f"Erreur lors de la génération du zip: {str(e)}"}), 500


# --- AUTHENTICATION ROUTES ---


@app.route('/api/auth/register', methods=['POST'])
def register_user():
    """Crée un nouveau compte utilisateur / clinicien."""
    try:
        if db is None:
            return jsonify({'error': 'MongoDB non disponible'}), 503

        data = request.get_json()
        name = data.get('name')
        email = data.get('email')
        password = data.get('password')
        role = data.get('role', 'doctor')  # doctor ou admin

        if not name or not email or not password:
            return jsonify({'error': 'Paramètres name, email et password requis'}), 400

        # Vérifier si l'utilisateur existe déjà
        existing_user = db.users.find_one({'email': email})
        if existing_user:
            return jsonify({'error': 'Un utilisateur avec cette adresse email existe déjà'}), 400

        # Hacher le mot de passe de manière sécurisée (bcrypt)
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

        user_doc = {
            'name': name,
            'email': email,
            'password': hashed_password.decode('utf-8'),
            'role': role,
            'created_at': datetime.utcnow().isoformat()
        }

        db.users.insert_one(user_doc)
        return jsonify({'status': 'success', 'message': 'Compte créé avec succès'}), 201

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/auth/login', methods=['POST'])
def login_user():
    """Authentifie l'utilisateur et génère un jeton JWT de session."""
    try:
        if db is None:
            return jsonify({'error': 'MongoDB non disponible'}), 503

        data = request.get_json()
        email = data.get('email')
        password = data.get('password')

        if not email or not password:
            return jsonify({'error': 'Email et password requis'}), 400

        user = db.users.find_one({'email': email})
        if not user:
            return jsonify({'error': 'Identifiants incorrects'}), 401

        # Vérifier le mot de passe
        if bcrypt.checkpw(password.encode('utf-8'), user['password'].encode('utf-8')):
            # Générer le token JWT
            token = jwt.encode({
                'email': user['email'],
                'role': user['role'],
                'name': user['name']
            }, JWT_SECRET, algorithm="HS256")
            
            return jsonify({
                'token': token,
                'user': {
                    'name': user['name'],
                    'email': user['email'],
                    'role': user['role']
                }
            }), 200
        else:
            return jsonify({'error': 'Identifiants incorrects'}), 401

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/auth/me', methods=['GET'])
@token_required
def get_my_profile(current_user):
    """Récupère les informations du profil connecté."""
    return jsonify(current_user), 200


@app.route('/api/auth/update', methods=['POST'])
@token_required
def update_profile(current_user):
    """Permet de mettre à jour son mot de passe ou son nom."""
    try:
        if db is None:
            return jsonify({'error': 'MongoDB non disponible'}), 503

        data = request.get_json()
        new_name = data.get('name')
        new_password = data.get('password')

        updates = {}
        if new_name:
            updates['name'] = new_name
        
        if new_password:
            # Hacher le nouveau mot de passe
            hashed = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
            updates['password'] = hashed.decode('utf-8')

        if not updates:
            return jsonify({'error': 'Aucune modification spécifiée'}), 400

        db.users.update_one({'email': current_user['email']}, {'$set': updates})
        return jsonify({'status': 'success', 'message': 'Profil mis à jour avec succès'}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'mongodb_connected': db is not None,
        'gemini_configured': bool(GEMINI_API_KEY)
    })


if __name__ == '__main__':
    print("Démarrage du serveur Flask Opthia V2...")
    init_connections()
    app.run(host='0.0.0.0', port=5000)
