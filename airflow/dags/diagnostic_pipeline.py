"""
DAG Airflow pour le pipeline de diagnostic ophtalmologique asynchrone.
- Traite les diagnostics RD et Glaucome
- Sauvegarde les résultats dans MongoDB
"""

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago
from datetime import timedelta
import torch
import torchvision.models as models
import torch.nn as nn
import numpy as np
import cv2
import io
from PIL import Image
import base64
from pymongo import MongoClient
import os
from collections import OrderedDict
import torchvision.transforms as transforms

# Configuration
default_args = {
    'owner': 'ophtia',
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
    'execution_timeout': timedelta(minutes=30),
}

dag = DAG(
    'diagnostic_pipeline',
    default_args=default_args,
    description='Pipeline de diagnostic RD et Glaucome asynchrone',
    schedule_interval=None,  # Déclenché manuellement via API
    start_date=days_ago(1),
    tags=['diagnostic', 'ophtia'],
)

# Cache global des modèles
models_cache = {}
db = None


def init_models_and_db():
    """Initialise les modèles et la connexion MongoDB."""
    global models_cache, db

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    models_cache['device'] = device
    print(f"[*] Device: {device}")

    # Chargement RD
    rd_model_path = '/app/models/best_dr_model.pth'
    if os.path.exists(rd_model_path):
        try:
            rd_model = models.resnet50(weights=None)
            num_features = rd_model.fc.in_features
            rd_model.fc = nn.Sequential(
                nn.Linear(num_features, 512),
                nn.ReLU(),
                nn.Dropout(0.5),
                nn.Linear(512, 5)
            )
            checkpoint = torch.load(rd_model_path, map_location=device)
            state_dict = checkpoint.get('model_state_dict', checkpoint)

            new_state_dict = OrderedDict()
            for k, v in state_dict.items():
                if k.startswith('backbone.'):
                    new_state_dict[k[9:]] = v
                else:
                    new_state_dict[k] = v

            rd_model.load_state_dict(new_state_dict, strict=False)
            rd_model.to(device)
            rd_model.eval()
            models_cache['rd'] = rd_model
            print("[✓] Modèle RD chargé")
        except Exception as e:
            print(f"[✗] Erreur RD: {e}")
            raise

    # Chargement Glaucome
    glaucoma_model_path = '/app/models/best_efficientnet_glaucoma.pth'
    if os.path.exists(glaucoma_model_path):
        try:
            glaucoma_model = models.efficientnet_b0(weights=None)
            num_features = glaucoma_model.classifier[1].in_features
            glaucoma_model.classifier = nn.Sequential(
                nn.Dropout(p=0.4),
                nn.Linear(num_features, 512),
                nn.BatchNorm1d(512),
                nn.ReLU(),
                nn.Dropout(p=0.3),
                nn.Linear(512, 256),
                nn.BatchNorm1d(256),
                nn.ReLU(),
                nn.Dropout(p=0.2),
                nn.Linear(256, 1)
            )
            checkpoint = torch.load(glaucoma_model_path, map_location=device)
            state_dict = checkpoint.get('model_state_dict', checkpoint)
            glaucoma_model.load_state_dict(state_dict)
            glaucoma_model.to(device)
            glaucoma_model.eval()
            models_cache['glaucoma'] = glaucoma_model
            print("[✓] Modèle Glaucome chargé")
        except Exception as e:
            print(f"[✗] Erreur Glaucome: {e}")
            raise

    # Connexion MongoDB
    mongo_uri = os.environ.get('MONGO_URI',
                               'mongodb://mongo:27017/ophtia')
    try:
        db_client = MongoClient(mongo_uri)
        db = db_client.get_default_database()
        db.command('ping')
        print("[✓] Connexion MongoDB réussie")
    except Exception as e:
        print(f"[✗] Erreur MongoDB: {e}")
        raise


def crop_image_from_gray(img, tol=10):
    """Enlève les bords noirs."""
    if img.ndim == 3:
        gray_img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        mask = gray_img > tol
        check_shape = img[:, :, 0][np.ix_(mask.any(1), mask.any(0))].shape[0]
        if check_shape == 0:
            return img
        img1 = img[:, :, 0][np.ix_(mask.any(1), mask.any(0))]
        img2 = img[:, :, 1][np.ix_(mask.any(1), mask.any(0))]
        img3 = img[:, :, 2][np.ix_(mask.any(1), mask.any(0))]
        return np.stack([img1, img2, img3], axis=-1)
    return img


def get_prediction_transforms(model_type):
    """Retourne le pipeline de transformation pour le modèle."""
    if model_type == 'rd':
        import albumentations as A
        return A.Compose([
            A.Resize(512, 512),
            A.Normalize(mean=[0.485, 0.456, 0.406],
                        std=[0.229, 0.224, 0.225]),
            A.pytorch.ToTensorV2()
        ])
    else:
        return transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                 std=[0.229, 0.224, 0.225])
        ])


def process_diagnostic(**context):
    """Traite le diagnostic RD ou Glaucome."""
    ti = context['task_instance']
    task_data = ti.xcom_pull(task_ids='prepare_task')

    task_id = task_data['task_id']
    image_base64 = task_data['image_base64']
    model_type = task_data['model_type']

    print(f"[*] Traitement diagnostic: task_id={task_id}, model={model_type}")

    global models_cache, db

    try:
        # Décodage image
        image_bytes = base64.b64decode(image_base64.split(',')[1])
        image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        image_np = np.array(image)

        # Traitement différencié
        if model_type == 'rd':
            image_np_processed = crop_image_from_gray(image_np, tol=10)
            image_np_processed = cv2.resize(image_np_processed, (512, 512))
            image_np = image_np_processed

        # Transformations
        transforms_pipeline = get_prediction_transforms(model_type)

        if model_type == 'rd':
            transformed = transforms_pipeline(image=image_np)
            processed_image = transformed['image']
        else:
            processed_image = transforms_pipeline(Image.fromarray(image_np))

        # Batch
        if processed_image.dim() == 3:
            input_tensor = processed_image.unsqueeze(0).to(
                models_cache['device'])
        else:
            input_tensor = processed_image.to(models_cache['device'])

        # Prédiction
        model = models_cache[model_type]
        with torch.no_grad():
            output = model(input_tensor)

            if model_type == 'rd':
                probabilities = torch.softmax(output, dim=1)
                prediction_multiclass = torch.argmax(
                    probabilities, dim=1).item()
                prediction_binary = 0 if prediction_multiclass == 0 else 1
                probability = float(probabilities[0, 0].item())

                if prediction_binary == 1:
                    recommendation = \
                        "⚠️ RÉTINOPATHIE DÉTECTÉE - Consulter un médecin"
                else:
                    recommendation = "✅ Aucune rétinopathie détectée"

                result = {
                    'task_id': task_id,
                    'prediction_class': int(prediction_binary),
                    'prediction_multiclass': int(prediction_multiclass),
                    'probability': probability,
                    'recommendation': recommendation,
                    'all_probabilities': {
                        'class_0': float(probabilities[0, 0].item()),
                        'class_1': float(probabilities[0, 1].item()),
                        'class_2': float(probabilities[0, 2].item()),
                        'class_3': float(probabilities[0, 3].item()),
                        'class_4': float(probabilities[0, 4].item()),
                    }
                }
            else:
                probability = torch.sigmoid(output).item()
                prediction = 1 if probability > 0.5 else 0
                if prediction == 1:
                    recommendation = \
                        "⚠️ GLAUCOME DÉTECTÉ - Consulter un médecin"
                else:
                    recommendation = "✅ Aucun glaucome détecté"

                result = {
                    'task_id': task_id,
                    'prediction_class': int(prediction),
                    'probability': probability,
                    'recommendation': recommendation,
                }

        # Sauvegarde MongoDB
        if db:
            db.diagnostic_results.insert_one({
                **result,
                'model_type': model_type,
                'status': 'completed',
                'timestamp': context['execution_date'],
            })

        print(f"[✓] Diagnostic complété: {task_id}")
        return result

    except Exception as e:
        print(f"[✗] Erreur diagnostic: {e}")
        if db:
            db.diagnostic_results.insert_one({
                'task_id': task_id,
                'status': 'failed',
                'error': str(e),
                'timestamp': context['execution_date'],
            })
        raise


# Tâches du DAG
init_task = PythonOperator(
    task_id='init_models',
    python_callable=init_models_and_db,
    dag=dag,
)

prepare_task = PythonOperator(
    task_id='prepare_task',
    python_callable=lambda **context: context['dag_run'].conf,
    dag=dag,
)

diagnostic_task = PythonOperator(
    task_id='process_diagnostic',
    python_callable=process_diagnostic,
    provide_context=True,
    dag=dag,
)

# Ordre d'exécution
init_task >> prepare_task >> diagnostic_task
