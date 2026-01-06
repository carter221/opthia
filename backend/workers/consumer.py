"""
RabbitMQ Consumer Worker pour traiter les tâches de diagnostic
Consomme depuis les queues 'diagnostic_tasks' et 'gradcam_tasks'
"""

import os
import json
import pika
import torch
import torchvision.models as models
import torch.nn as nn
import numpy as np
import cv2
import io
from PIL import Image
import base64
from pymongo import MongoClient
import logging
from datetime import datetime
import albumentations as A
from albumentations.pytorch import ToTensorV2
import torchvision.transforms as transforms
import signal
import sys

# Configuration logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Singleton pour les modèles et DB
models_cache = {}
db = None
rabbitmq_connection = None
rabbitmq_channel = None


def init_models():
    """Initialise les modèles PyTorch."""
    global models_cache
    
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    models_cache['device'] = device
    logger.info(f"Device: {device}")
    
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
                nn.Linear(512, 5)
            )
            
            checkpoint = torch.load(rd_model_path, map_location=device)
            state_dict = checkpoint.get('model_state_dict', checkpoint)
            
            from collections import OrderedDict
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
            logger.info("✓ Modèle RD chargé")
        except Exception as e:
            logger.error(f"✗ Erreur RD: {e}")
            raise
    
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
            state_dict = checkpoint.get('model_state_dict', checkpoint)
            glaucoma_model.load_state_dict(state_dict)
            glaucoma_model.to(device)
            glaucoma_model.eval()
            models_cache['glaucoma'] = glaucoma_model
            logger.info("✓ Modèle Glaucome chargé")
        except Exception as e:
            logger.error(f"✗ Erreur Glaucome: {e}")
            raise


def init_mongodb():
    """Initialise la connexion à MongoDB."""
    global db
    
    mongo_uri = os.environ.get('MONGO_URI', 'mongodb://mongo:27017/ophtia')
    try:
        db_client = MongoClient(mongo_uri)
        db = db_client.get_default_database()
        db.command('ping')
        logger.info("✓ Connexion MongoDB réussie")
    except Exception as e:
        logger.error(f"✗ Erreur MongoDB: {e}")
        db = None


def init_rabbitmq():
    """Initialise la connexion à RabbitMQ."""
    global rabbitmq_connection, rabbitmq_channel
    
    rabbitmq_url = os.environ.get('RABBITMQ_URL', 'amqp://guest:guest@rabbitmq:5672/%2F')
    try:
        rabbitmq_connection = pika.BlockingConnection(pika.URLParameters(rabbitmq_url))
        rabbitmq_channel = rabbitmq_connection.channel()
        
        # Déclarer les queues
        rabbitmq_channel.queue_declare(queue='diagnostic_tasks', durable=True)
        rabbitmq_channel.queue_declare(queue='gradcam_tasks', durable=True)
        
        # Limiter à 1 message par worker à la fois
        rabbitmq_channel.basic_qos(prefetch_count=1)
        
        logger.info("✓ Connexion à RabbitMQ réussie")
    except Exception as e:
        logger.error(f"✗ Erreur RabbitMQ: {e}")
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
    """Retourne le pipeline de transformation."""
    if model_type == 'rd':
        return A.Compose([
            A.Resize(512, 512),
            A.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            A.pytorch.ToTensorV2()
        ])
    else:
        return transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
        ])


def process_diagnostic_task(task_data):
    """Traite une tâche de diagnostic."""
    task_id = task_data.get('task_id')
    image_base64 = task_data.get('image_base64')
    model_type = task_data.get('model_type')
    filename = task_data.get('filename', 'unknown')
    
    logger.info(f"[Worker] Traitement: task_id={task_id}, model={model_type}")
    
    try:
        # Décodage image
        if image_base64.startswith('data:'):
            image_base64 = image_base64.split(',')[1]
        
        image_bytes = base64.b64decode(image_base64)
        image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
        image_np = np.array(image)
        
        # Traitement différencié
        if model_type == 'rd':
            image_np = crop_image_from_gray(image_np, tol=10)
            image_np = cv2.resize(image_np, (512, 512))
        
        # Transformations
        transforms_pipeline = get_prediction_transforms(model_type)
        
        if model_type == 'rd':
            transformed = transforms_pipeline(image=image_np)
            processed_image = transformed['image']
        else:
            processed_image = transforms_pipeline(Image.fromarray(image_np))
        
        # Batch
        if processed_image.dim() == 3:
            input_tensor = processed_image.unsqueeze(0).to(models_cache['device'])
        else:
            input_tensor = processed_image.to(models_cache['device'])
        
        # Prédiction
        model = models_cache[model_type]
        with torch.no_grad():
            output = model(input_tensor)
            
            if model_type == 'rd':
                probabilities = torch.softmax(output, dim=1)
                prediction_multiclass = torch.argmax(probabilities, dim=1).item()
                prediction_binary = 0 if prediction_multiclass == 0 else 1
                
                result = {
                    'task_id': task_id,
                    'prediction_class': int(prediction_binary),
                    'prediction_multiclass': int(prediction_multiclass),
                    'probability': float(probabilities[0, 0].item()),
                    'all_probabilities': {
                        'class_0': float(probabilities[0, 0].item()),
                        'class_1': float(probabilities[0, 1].item()),
                        'class_2': float(probabilities[0, 2].item()),
                        'class_3': float(probabilities[0, 3].item()),
                        'class_4': float(probabilities[0, 4].item()),
                    },
                    'recommendation': "⚠️ RÉTINOPATHIE DÉTECTÉE" if prediction_binary == 1 else "✅ Aucune RD",
                    'model_type': model_type,
                    'status': 'completed',
                    'filename': filename,
                    'timestamp': datetime.utcnow()
                }
            else:
                probability = torch.sigmoid(output).item()
                prediction = 1 if probability > 0.5 else 0
                
                result = {
                    'task_id': task_id,
                    'prediction_class': int(prediction),
                    'probability': probability,
                    'recommendation': "⚠️ GLAUCOME DÉTECTÉ" if prediction == 1 else "✅ Aucun glaucome",
                    'model_type': model_type,
                    'status': 'completed',
                    'filename': filename,
                    'timestamp': datetime.utcnow()
                }
        
        # Sauvegarde MongoDB
        if db:
            db.diagnostic_results.insert_one(result)
            logger.info(f"✓ Diagnostic sauvegardé: {task_id}")
        
        return result
        
    except Exception as e:
        logger.error(f"✗ Erreur diagnostic: {e}")
        if db:
            db.diagnostic_results.insert_one({
                'task_id': task_id,
                'status': 'failed',
                'error': str(e),
                'timestamp': datetime.utcnow()
            })
        raise


def callback_diagnostic(ch, method, properties, body):
    """Callback pour consommer les tâches de diagnostic."""
    try:
        task_data = json.loads(body)
        logger.info(f"[Queue: diagnostic_tasks] Message reçu: {task_data.get('task_id')}")
        
        process_diagnostic_task(task_data)
        
        # Acknowledge le message
        ch.basic_ack(delivery_tag=method.delivery_tag)
        logger.info(f"[ACK] Message traité avec succès")
        
    except Exception as e:
        logger.error(f"[NACK] Erreur: {e}")
        # NACK et requeue pour retry
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


def callback_gradcam(ch, method, properties, body):
    """Callback pour consommer les tâches Grad-CAM."""
    try:
        task_data = json.loads(body)
        logger.info(f"[Queue: gradcam_tasks] Message reçu: {task_data.get('task_id')}")
        
        # Pour l'instant, même traitement que diagnostic
        process_diagnostic_task(task_data)
        
        ch.basic_ack(delivery_tag=method.delivery_tag)
        logger.info(f"[ACK] Grad-CAM traité avec succès")
        
    except Exception as e:
        logger.error(f"[NACK] Erreur Grad-CAM: {e}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


def start_worker():
    """Démarre le worker en écoutant les queues."""
    global rabbitmq_channel
    
    logger.info("════════════════════════════════════════")
    logger.info("   RabbitMQ Worker - Ophtia")
    logger.info("════════════════════════════════════════")
    
    init_models()
    init_mongodb()
    init_rabbitmq()
    
    # Enregistrer les consumers
    rabbitmq_channel.basic_consume(
        queue='diagnostic_tasks',
        on_message_callback=callback_diagnostic
    )
    
    rabbitmq_channel.basic_consume(
        queue='gradcam_tasks',
        on_message_callback=callback_gradcam
    )
    
    logger.info("Worker en écoute sur les queues...")
    logger.info("  - diagnostic_tasks")
    logger.info("  - gradcam_tasks")
    logger.info("════════════════════════════════════════")
    
    # Graceful shutdown
    def signal_handler(sig, frame):
        logger.info("\n[SHUTDOWN] Arrêt du worker...")
        rabbitmq_channel.stop_consuming()
        rabbitmq_connection.close()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        rabbitmq_channel.start_consuming()
    except KeyboardInterrupt:
        logger.info("Worker arrêté.")


if __name__ == '__main__':
    start_worker()
