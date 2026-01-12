import os
from flask import Flask, request, jsonify
import torch
import torchvision.transforms as transforms
import albumentations as A
from albumentations.pytorch import ToTensorV2
import numpy as np
import torch.nn as nn
import torchvision.models as models
from pymongo import MongoClient
import cv2
import json
import pika
import uuid

app = Flask(__name__)


def crop_image_from_gray(img, tol=10):
    """
    Enlève les bords noirs autour de l'image de fond d'œil.
    """
    if img.ndim == 2:
        mask = img > tol
        return img[np.ix_(mask.any(1), mask.any(0))]
    elif img.ndim == 3:
        gray_img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        mask = gray_img > tol

        check_shape = img[:, :, 0][np.ix_(mask.any(1), mask.any(0))].shape[0]
        if (check_shape == 0):
            return img
        else:
            img1 = img[:, :, 0][np.ix_(mask.any(1), mask.any(0))]
            img2 = img[:, :, 1][np.ix_(mask.any(1), mask.any(0))]
            img3 = img[:, :, 2][np.ix_(mask.any(1), mask.any(0))]
            img = np.stack([img1, img2, img3], axis=-1)
        return img


# --- CONFIGURATION ET CONNEXIONS ---

# Design Pattern Singleton pour les modèles et la connexion DB
models_cache = {}
db_client = None
db = None
rabbitmq_channel = None


def init_rabbitmq():
    """Initialise la connexion à RabbitMQ."""
    global rabbitmq_channel
    try:
        rabbitmq_url = os.environ.get('RABBITMQ_URL', 'amqp://guest:guest@localhost:5672/%2F')
        connection = pika.BlockingConnection(pika.URLParameters(rabbitmq_url))
        rabbitmq_channel = connection.channel()
        rabbitmq_channel.queue_declare(queue='diagnostic_tasks', durable=True)
        rabbitmq_channel.queue_declare(queue='gradcam_tasks', durable=True)
        print("Connexion à RabbitMQ réussie.")
    except Exception as e:
        print(f"AVERTISSEMENT: RabbitMQ indisponible. {e}")
        rabbitmq_channel = None


def publish_task(queue_name, task_data):
    """Publie une tâche dans RabbitMQ."""
    try:
        rabbitmq_url = os.environ.get('RABBITMQ_URL', 'amqp://guest:guest@rabbitmq:5672/%2F')
        connection = pika.BlockingConnection(pika.URLParameters(rabbitmq_url))
        channel = connection.channel()
        channel.queue_declare(queue=queue_name, durable=True)

        channel.basic_publish(
            exchange='',
            routing_key=queue_name,
            body=json.dumps(task_data),
            properties=pika.BasicProperties(delivery_mode=2)  # Persistent
        )
        connection.close()
        print(f"[✓] Tâche publiée dans {queue_name}")
        return True
    except Exception as e:
        print(f"[✗] Erreur publication RabbitMQ: {e}")
        return False


def init_connections():
    """Initialise la connexion à la base de données et charge les modèles."""
    global db_client, db

    # Connexion à MongoDB
    mongo_uri = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/ophtia')
    try:
        db_client = MongoClient(mongo_uri)
        db = db_client.get_default_database()
        # Test de la connexion
        db.command('ping')
        print("Connexion à MongoDB réussie.")
    except Exception as e:
        print(f"ERREUR: Impossible de se connecter à MongoDB. {e}")
        db = None

    # Chargement des modèles
    load_models()


def get_prediction_transforms(model_type):
    """Retourne le pipeline de transformation EXACT utilisé à l'entraînement pour chaque modèle."""
    if model_type == 'rd':
        # Transformations RD du notebook: crop + CLAHE + resize 512 + normalize
        return A.Compose([
            A.Resize(512, 512),
            A.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ToTensorV2()
        ])
    elif model_type == 'glaucoma':
        # Transformations Glaucome du notebook: resize 224 + normalize
        return transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
        ])
    else:
        raise ValueError("Type de modèle non supporté")


def load_models():
    """Charge les modèles PyTorch au démarrage."""
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    models_cache['device'] = device

    # --- Chargement du modèle Glaucome ---
    glaucoma_model_path = 'models/best_efficientnet_glaucoma.pth'
    if os.path.exists(glaucoma_model_path):
        try:
            glaucoma_model = models.efficientnet_b0(weights=None)
            num_features = glaucoma_model.classifier[1].in_features
            glaucoma_model.classifier = nn.Sequential(
                nn.Dropout(p=0.4), nn.Linear(num_features, 512), nn.BatchNorm1d(512), nn.ReLU(),
                nn.Dropout(p=0.3), nn.Linear(512, 256), nn.BatchNorm1d(256), nn.ReLU(),
                nn.Dropout(p=0.2), nn.Linear(256, 1)
            )
            checkpoint = torch.load(glaucoma_model_path, map_location=device)
            # Gestion des cas où le checkpoint est le state_dict ou un dictionnaire le contenant
            state_dict = checkpoint.get('model_state_dict', checkpoint)
            glaucoma_model.load_state_dict(state_dict)
            glaucoma_model.to(device)
            glaucoma_model.eval()
            models_cache['glaucoma'] = glaucoma_model
            print("Modèle Glaucome chargé.")
        except Exception as e:
            print(f"ERREUR lors du chargement du modèle Glaucome: {e}")
    else:
        print(f"AVERTISSEMENT: Le fichier du modèle {glaucoma_model_path} n'a pas été trouvé.")

    # --- Chargement du modèle Rétinopathie Diabétique ---
    rd_model_path = 'models/best_dr_model.pth'
    if os.path.exists(rd_model_path):
        try:
            # RD est MULTICLASSE (5 classes: 0, 1, 2, 3, 4)
            rd_model = models.resnet50(weights=None)
            num_features = rd_model.fc.in_features
            rd_model.fc = nn.Sequential(
                nn.Linear(num_features, 512),
                nn.ReLU(),
                nn.Dropout(0.5),
                nn.Linear(512, 5)  # 5 classes pour RD
            )

            checkpoint = torch.load(rd_model_path, map_location=device)
            state_dict = checkpoint.get('model_state_dict', checkpoint)

            # Correction pour les clés préfixées par 'backbone.'
            from collections import OrderedDict
            new_state_dict = OrderedDict()
            for k, v in state_dict.items():
                if k.startswith('backbone.'):
                    name = k[9:]  # Supprimer le préfixe 'backbone.'
                    new_state_dict[name] = v
                else:
                    new_state_dict[k] = v

            rd_model.load_state_dict(new_state_dict, strict=False)
            rd_model.to(device)
            rd_model.eval()
            models_cache['rd'] = rd_model
            print("Modèle Rétinopathie Diabétique chargé.")
        except Exception as e:
            print(f"ERREUR lors du chargement du modèle Rétinopathie Diabétique: {e}")
    else:
        print(f"AVERTISSEMENT: Le fichier du modèle {rd_model_path} n'a pas été trouvé.")


# --- ENDPOINTS DE L'API ---


@app.route('/', methods=['GET'])
def index():
    return "Backend de diagnostic ophtalmologique est en cours d'exécution!"


@app.route('/predict_with_gradcam', methods=['POST'])
def predict_with_gradcam():
    """
    Endpoint qui envoie la tâche de diagnostic au worker RabbitMQ.
    Accepte JSON ou multipart/form-data.
    Retourne une task_id pour polling des résultats.
    """
    task_id = str(uuid.uuid4())

    try:
        # Supporter à la fois JSON et multipart/form-data
        if request.is_json:
            # Format JSON (depuis Streamlit corrigé)
            data = request.get_json()
            image_base64 = data.get('image_base64')
            model_type = data.get('model_type', 'rd')
            filename = 'image.png'

            if not image_base64:
                return jsonify({'error': 'image_base64 manquant'}), 400

            # Vérifier si le base64 a le préfixe data:
            if not image_base64.startswith('data:'):
                image_base64 = f"data:image/png;base64,{image_base64}"
        else:
            # Format multipart/form-data (ancien format)
            if 'file' not in request.files:
                return jsonify({'error': 'Aucun fichier fourni'}), 400

            file = request.files['file']
            model_type = request.form.get('model_type', 'rd')
            filename = file.filename

            # Encodage image en base64
            image_bytes = file.read()
            b64_module = __import__('base64')
            encoded = b64_module.b64encode(image_bytes)
            image_base64 = "data:image/png;base64," + encoded.decode('utf-8')

        if model_type not in ['rd', 'glaucoma']:
            return jsonify({'error': 'model_type doit être "rd" ou "glaucoma"'}), 400

        # Préparer la tâche
        task_data = {
            'task_id': task_id,
            'image_base64': image_base64,
            'model_type': model_type,
            'filename': filename
        }

        # Envoyer à RabbitMQ
        queue = 'diagnostic_tasks'
        published = publish_task(queue, task_data)

        if published:
            return jsonify({
                'status': 'submitted',
                'task_id': task_id,
                'message': 'Diagnostic en cours de traitement avec Grad-CAM...',
                'poll_url': f'/result/{task_id}'
            }), 202
        else:
            return jsonify({'error': 'Impossible de soumettre la tâche'}), 500

    except Exception as e:
        print(f"[✗] Erreur endpoint /predict_with_gradcam: {e}")
        return jsonify({'error': f"Erreur : {str(e)}"}), 500


@app.route('/result/<task_id>', methods=['GET'])
def get_result(task_id):
    """Récupère le résultat d'une tâche Airflow via MongoDB."""
    try:
        if db is None:
            return jsonify({'error': 'Connexion MongoDB non disponible'}), 503

        result = db.diagnostic_results.find_one({'task_id': task_id})

        if result:
            result.pop('_id', None)  # Supprimer l'ID MongoDB
            return jsonify(result), 200
        else:
            return jsonify({'status': 'pending', 'message': 'Résultat pas encore disponible'}), 202

    except Exception as e:
        return jsonify({'error': f"Erreur : {str(e)}"}), 500


@app.route('/health', methods=['GET'])
def health_check():
    """Endpoint de santé pour Docker."""
    return jsonify({
        'status': 'healthy',
        'models_loaded': len([m for m in models_cache if m != 'device']) > 0
    })


if __name__ == '__main__':
    print("Démarrage du serveur Flask...")
    init_rabbitmq()  # Initialiser RabbitMQ (optionnel)
    init_connections()
    app.run(host='0.0.0.0', port=5000)
