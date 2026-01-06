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

app = Flask(__name__)

# --- CONFIGURATION ET CONNEXIONS ---

# Design Pattern Singleton pour les modèles et la connexion DB
models_cache = {}
db_client = None
db = None

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
    """Retourne le pipeline de transformation pour un type de modèle."""
    if model_type == 'rd':
        return A.Compose([
            A.Resize(512, 512),
            A.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ToTensorV2()
        ])
    elif model_type == 'glaucoma':
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
            rd_model = models.resnet50(weights=None)
            num_features = rd_model.fc.in_features
            rd_model.fc = nn.Sequential(
                nn.Linear(num_features, 512),
                nn.ReLU(),
                nn.Dropout(0.5),
                nn.Linear(512, 1)
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
        
        model = models_cache[model_type]
        device = models_cache['device']
        
        transforms_pipeline = get_prediction_transforms(model_type)
        processed_image = transforms_pipeline(image=np.array(image))['image'] if isinstance(transforms_pipeline, A.Compose) else transforms_pipeline(image)
        
        input_tensor = processed_image.unsqueeze(0).to(device)

        with torch.no_grad():
            output = model(input_tensor)
            probability = torch.sigmoid(output).item()
            prediction = 1 if probability > 0.5 else 0

        # Création du document pour l'auditabilité
        diagnostic_record = {
            'model_used': model_type,
            'prediction_class': prediction,
            'probability': probability,
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

if __name__ == '__main__':
    print("Démarrage du serveur Flask...")
    init_connections()
    app.run(host='0.0.0.0', port=5000)
