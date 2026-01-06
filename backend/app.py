import os
from flask import Flask, request, jsonify
from PIL import Image
import io
import torch
import torchvision.transforms as transforms
import albumentations as A
from albumentations.pytorch import ToTensorV2
import numpy as np
import torch.nn as nn
import torchvision.models as models
from pymongo import MongoClient
from datetime import datetime
import cv2
import json
import pika
import uuid
from gradcam import GradCAM, apply_heatmap_to_image, encode_image_to_base64

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
    if rabbitmq_channel:
        try:
            rabbitmq_channel.basic_publish(
                exchange='',
                routing_key=queue_name,
                body=json.dumps(task_data),
                properties=pika.BasicProperties(delivery_mode=2)  # Persistent
            )
            return True
        except Exception as e:
            print(f"Erreur publication RabbitMQ: {e}")
            return False
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

@app.route('/predict', methods=['POST'])
def predict():
    if 'file' not in request.files:
        return jsonify({'error': 'Aucun fichier fourni'}), 400

    file = request.files['file']
    model_type = request.form.get('model_type')

    if not model_type or model_type not in models_cache:
        return jsonify({'error': f"Type de modèle '{model_type}' non valide ou non chargé"}), 400

    try:
        image_bytes = file.read()
        image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        image_np = np.array(image)
        
        # TRAITEMENT DIFFÉRENCIÉ PAR MODÈLE
        if model_type == 'rd':
            # ========== PIPELINE RÉTINOPATHIE DIABÉTIQUE ==========
            # Traitement MINIMAL pour ne pas dégrader la qualité
            # Note: Le modèle a été entraîné avec un pipeline spécifique
            # Crop très léger pour enlever juste les bordures extrêmes
            image_np_processed = crop_image_from_gray(image_np, tol=10)
            
            # PAS de CLAHE - le modèle préfère les images brutes
            # (Peut augmenter l'accuracy en étant conservateur)
            
            # Resize à 512x512
            image_np_processed = cv2.resize(image_np_processed, (512, 512))
            image_np = image_np_processed
            
        elif model_type == 'glaucoma':
            # ========== PIPELINE GLAUCOME ==========
            # Pas de crop ni CLAHE pour le glaucome
            # Juste convertir en PIL pour les transformations torchvision
            image = Image.fromarray(image_np)
        
        # Récupérer le modèle et device
        model = models_cache[model_type]
        device = models_cache['device']
        
        # Appliquer les transformations
        transforms_pipeline = get_prediction_transforms(model_type)
        
        if model_type == 'rd':
            # Albumentations pour RD
            transformed = transforms_pipeline(image=image_np)
            processed_image = transformed['image']
        else:
            # Torchvision pour Glaucome
            processed_image = transforms_pipeline(image)
        
        # Ajouter dimension batch
        if processed_image.dim() == 3:
            input_tensor = processed_image.unsqueeze(0).to(device)
        else:
            input_tensor = processed_image.to(device)

        # Prédiction avec le modèle
        with torch.no_grad():
            output = model(input_tensor)
            
            if model_type == 'rd':
                # ===== BINAIRE pour RD (transformation multiclasse -> binaire) =====
                # Classe 0: Pas de RD (négatif)
                # Classes 1-4: RD détectée (positif)
                probabilities = torch.softmax(output, dim=1)
                prediction_multiclass = torch.argmax(probabilities, dim=1).item()
                
                # Convertir en binaire: 0 = pas de RD, 1 = RD détectée
                prediction_binary = 0 if prediction_multiclass == 0 else 1
                probability_rd = float(probabilities[0, 0].item())  # Probabilité de classe 0 (pas de RD)
                
                # Recommandation
                if prediction_binary == 1:
                    recommendation = "⚠️ RÉTINOPATHIE DIABÉTIQUE DÉTECTÉE - Veuillez consulter un médecin pour un examen complet"
                else:
                    recommendation = "✅ Aucune rétinopathie diabétique détectée"
                
                diagnostic_record = {
                    'model_used': model_type,
                    'prediction_class': int(prediction_binary),  # 0=pas de RD, 1=RD détectée
                    'prediction_multiclass': int(prediction_multiclass),  # 0-4 pour info
                    'probability': probability_rd,  # Confiance de "pas de RD"
                    'all_probabilities': {
                        'class_0_aucune': float(probabilities[0, 0].item()),
                        'class_1_legere': float(probabilities[0, 1].item()),
                        'class_2_moderee': float(probabilities[0, 2].item()),
                        'class_3_severe': float(probabilities[0, 3].item()),
                        'class_4_proliferative': float(probabilities[0, 4].item())
                    },
                    'recommendation': recommendation,
                    'image_filename': file.filename,
                    'timestamp': datetime.utcnow()
                }
            else:
                # ===== BINAIRE pour Glaucome =====
                # 0=normal, 1=glaucome
                probability = torch.sigmoid(output).item()
                prediction = 1 if probability > 0.5 else 0
                
                # Recommandation
                if prediction == 1:
                    recommendation = "⚠️ GLAUCOME DÉTECTÉ - Veuillez consulter un médecin pour un examen complet"
                else:
                    recommendation = "✅ Aucun glaucome détecté"
                
                diagnostic_record = {
                    'model_used': model_type,
                    'prediction_class': int(prediction),  # 0 ou 1
                    'probability': probability,
                    'recommendation': recommendation,
                    'image_filename': file.filename,
                    'timestamp': datetime.utcnow()
                }

        # Sauvegarde dans MongoDB
        if db is not None:
            db.diagnostics.insert_one(diagnostic_record)
        
        # Le résultat JSON ne contient pas l'ID de la base de données pour le frontend
        result_for_frontend = diagnostic_record.copy()
        del result_for_frontend['_id'] # L'objet ObjectId n'est pas sérialisable en JSON
        
        return jsonify(result_for_frontend)

    except Exception as e:
        return jsonify({'error': f"Erreur lors de la prédiction : {str(e)}"}), 500

@app.route('/predict_with_gradcam', methods=['POST'])
def predict_with_gradcam():
    """Prédiction avec Grad-CAM pour l'explicabilité."""
    if 'file' not in request.files:
        return jsonify({'error': 'Aucun fichier fourni'}), 400

    file = request.files['file']
    model_type = request.form.get('model_type')

    if not model_type or model_type not in models_cache:
        return jsonify({'error': f"Type de modèle '{model_type}' non valide ou non chargé"}), 400

    try:
        image_bytes = file.read()
        image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        image_np = np.array(image)
        original_image = image_np.copy()
        
        # TRAITEMENT DIFFÉRENCIÉ PAR MODÈLE (même logique que /predict)
        if model_type == 'rd':
            image_np_processed = crop_image_from_gray(image_np, tol=10)
            image_np_processed = cv2.resize(image_np_processed, (512, 512))
            image_np = image_np_processed
        
        # Récupérer le modèle et device
        model = models_cache[model_type]
        device = models_cache['device']
        
        # Appliquer les transformations
        transforms_pipeline = get_prediction_transforms(model_type)
        
        if model_type == 'rd':
            transformed = transforms_pipeline(image=image_np)
            processed_image = transformed['image']
        else:
            processed_image = transforms_pipeline(Image.fromarray(image_np))
        
        # Ajouter dimension batch
        if processed_image.dim() == 3:
            input_tensor = processed_image.unsqueeze(0).to(device)
        else:
            input_tensor = processed_image.to(device)
        
        # Sélectionner la couche cible pour Grad-CAM
        if model_type == 'rd':
            target_layer = model.layer4
        else:
            target_layer = model.features[-1]
        
        # Génération Grad-CAM
        gradcam = GradCAM(model, target_layer)
        cam = gradcam.generate_cam(input_tensor)
        
        # Superposition heatmap sur l'image processée (réduite à 50% pour optimiser la taille)
        heatmap_image = apply_heatmap_to_image(image_np, cam, alpha=0.5, resize_to=(256, 256))
        heatmap_base64 = encode_image_to_base64(heatmap_image)
        
        # Prédiction (même logique que /predict)
        with torch.no_grad():
            output = model(input_tensor)
            
            if model_type == 'rd':
                probabilities = torch.softmax(output, dim=1)
                prediction_multiclass = torch.argmax(probabilities, dim=1).item()
                prediction_binary = 0 if prediction_multiclass == 0 else 1
                probability_rd = float(probabilities[0, 0].item())
                
                if prediction_binary == 1:
                    recommendation = "⚠️ RÉTINOPATHIE DIABÉTIQUE DÉTECTÉE - Veuillez consulter un médecin pour un examen complet"
                else:
                    recommendation = "✅ Aucune rétinopathie diabétique détectée"
                
                result = {
                    'prediction_class': int(prediction_binary),
                    'prediction_multiclass': int(prediction_multiclass),
                    'probability': probability_rd,
                    'recommendation': recommendation,
                    'heatmap': heatmap_base64,
                    'all_probabilities': {
                        'class_0_aucune': float(probabilities[0, 0].item()),
                        'class_1_legere': float(probabilities[0, 1].item()),
                        'class_2_moderee': float(probabilities[0, 2].item()),
                        'class_3_severe': float(probabilities[0, 3].item()),
                        'class_4_proliferative': float(probabilities[0, 4].item())
                    }
                }
            else:
                probability = torch.sigmoid(output).item()
                prediction = 1 if probability > 0.5 else 0
                
                if prediction == 1:
                    recommendation = "⚠️ GLAUCOME DÉTECTÉ - Veuillez consulter un médecin pour un examen complet"
                else:
                    recommendation = "✅ Aucun glaucome détecté"
                
                result = {
                    'prediction_class': int(prediction),
                    'probability': probability,
                    'recommendation': recommendation,
                    'heatmap': heatmap_base64
                }
        
        return jsonify(result)

    except Exception as e:
        return jsonify({'error': f"Erreur lors de la prédiction Grad-CAM : {str(e)}"}), 500

@app.route('/predict_async', methods=['POST'])
def predict_async():
    """
    Endpoint asynchrone qui déclenche une tâche Airflow.
    Retourne task_id pour polling.
    """
    if 'file' not in request.files:
        return jsonify({'error': 'Aucun fichier fourni'}), 400

    file = request.files['file']
    model_type = request.form.get('model_type')

    if not model_type or model_type not in models_cache:
        return jsonify({'error': f"Type de modèle '{model_type}' non valide"}), 400

    try:
        # Génération ID unique
        task_id = str(uuid.uuid4())
        
        # Lecture l'image en base64
        image_bytes = file.read()
        image_base64 = "data:image/png;base64," + __import__('base64').b64encode(image_bytes).decode('utf-8')
        
        # Déclencher DAG Airflow
        import requests
        airflow_api = os.environ.get('AIRFLOW_API_URL', 'http://airflow-webserver:8080/api/v1')
        
        dag_run_config = {
            'task_id': task_id,
            'image_base64': image_base64,
            'model_type': model_type,
            'filename': file.filename
        }
        
        response = requests.post(
            f"{airflow_api}/dags/diagnostic_pipeline/dagRuns",
            json={'conf': dag_run_config},
            auth=('airflow', 'airflow'),
            timeout=10
        )
        
        if response.status_code in [200, 201]:
            return jsonify({
                'task_id': task_id,
                'status': 'pending',
                'poll_url': f'/result/{task_id}',
                'message': 'Tâche envoyée à Airflow. Utilisez poll_url pour obtenir le résultat'
            }), 202
        else:
            return jsonify({'error': f'Erreur Airflow: {response.text}'}), 500
    
    except Exception as e:
        return jsonify({'error': f"Erreur lors du déclenchement Airflow : {str(e)}"}), 500

@app.route('/result/<task_id>', methods=['GET'])
def get_result(task_id):
    """Récupère le résultat d'une tâche Airflow via MongoDB."""
    try:
        if not db:
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
