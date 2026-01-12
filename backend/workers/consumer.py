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


# ========== CLASSE GRADCAM ==========
class GradCAM:
    """Génère les heatmaps Grad-CAM pour l'explicabilité."""
    def __init__(self, model, target_layer):
        self.model = model
        self.target_layer = target_layer
        self.gradients = None
        self.activations = None

        # Register hooks
        target_layer.register_forward_hook(self._forward_hook)
        target_layer.register_backward_hook(self._backward_hook)

    def _forward_hook(self, module, input, output):
        self.activations = output.detach()

    def _backward_hook(self, module, grad_input, grad_output):
        self.gradients = grad_output[0].detach()

    def generate_cam(self, x):
        # Forward pass
        self.model.zero_grad()
        output = self.model(x)

        if isinstance(output, tuple):
            output = output[0]

        # Backward pass
        score = output.max()
        self.model.zero_grad()
        score.backward()

        # Compute CAM
        if self.gradients is None or self.activations is None:
            # Fallback: create a simple heatmap
            return np.ones((512, 512), dtype=np.float32) * 0.5

        gradients = self.gradients[0].cpu().numpy()
        activations = self.activations[0].cpu().numpy()

        weights = gradients.mean(axis=(1, 2))
        cam = np.zeros(activations.shape[1:], dtype=np.float32)

        for i, w in enumerate(weights):
            cam += w * activations[i, :, :]

        cam = np.maximum(cam, 0)
        cam = cv2.resize(cam, (512, 512))
        cam = (cam - cam.min()) / (cam.max() - cam.min() + 1e-8)

        return cam


def apply_heatmap_to_image(image, cam, alpha=0.5, resize_to=None):
    """Applique la heatmap Grad-CAM sur une image."""
    if resize_to:
        image = cv2.resize(image, resize_to)

    heatmap = cv2.applyColorMap((cam * 255).astype(np.uint8), cv2.COLORMAP_JET)
    heatmap = cv2.resize(heatmap, (image.shape[1], image.shape[0]))

    result = cv2.addWeighted(image, 1 - alpha, heatmap, alpha, 0)
    return result


def encode_image_to_base64(image):
    """Encode une image en base64."""
    success, buffer = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    base64_str = base64.b64encode(buffer).decode('utf-8')
    return f"data:image/jpeg;base64,{base64_str}"


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
            # Créer le modèle avec la même architecture que lors de l'entraînement
            class DRClassifier(nn.Module):
                def __init__(self, num_classes=5, pretrained=False):
                    super(DRClassifier, self).__init__()
                    self.backbone = models.resnet50(weights='DEFAULT' if pretrained else None)
                    num_features = self.backbone.fc.in_features
                    self.backbone.fc = nn.Linear(num_features, num_classes)

                def forward(self, x):
                    return self.backbone(x)

            rd_model = DRClassifier(num_classes=5, pretrained=False)

            # Charger les poids
            state_dict = torch.load(rd_model_path, map_location=device)
            rd_model.load_state_dict(state_dict, strict=True)

            rd_model.to(device)
            rd_model.eval()
            models_cache['rd'] = rd_model
            logger.info("✓ Modèle RD chargé avec succès")
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

        # Générer Grad-CAM
        try:
            if model_type == 'rd':
                target_layer = model.backbone.layer4
            else:
                target_layer = model.features[-1]

            gradcam = GradCAM(model, target_layer)
            cam = gradcam.generate_cam(input_tensor)
            heatmap_image = apply_heatmap_to_image(image_np, cam, alpha=0.5, resize_to=(256, 256))
            grad_cam_base64 = encode_image_to_base64(heatmap_image)
        except Exception as e:
            logger.warning(f"Grad-CAM non généré: {e}")
            grad_cam_base64 = None

        with torch.no_grad():
            output = model(input_tensor)

            if model_type == 'rd':
                probabilities = torch.softmax(output, dim=1)
                prediction_multiclass = torch.argmax(probabilities, dim=1).item()
                prediction_binary = 0 if prediction_multiclass == 0 else 1

                # Afficher la probabilité de la classe prédite (pas toujours classe 0)
                if prediction_binary == 0:
                    confidence = float(probabilities[0, 0].item())
                else:
                    confidence = float(probabilities[0, prediction_multiclass].item())

                result_data = {
                    'prediction_class': int(prediction_binary),
                    'prediction_multiclass': int(prediction_multiclass),
                    'probability': confidence,
                    'all_probabilities': {
                        'class_0': float(probabilities[0, 0].item()),
                        'class_1': float(probabilities[0, 1].item()),
                        'class_2': float(probabilities[0, 2].item()),
                        'class_3': float(probabilities[0, 3].item()),
                        'class_4': float(probabilities[0, 4].item()),
                    },
                    'recommendation': "⚠️ RÉTINOPATHIE DÉTECTÉE" if prediction_binary == 1 else "✅ Aucune RD",
                    'grad_cam': grad_cam_base64,
                }

                result = {
                    'task_id': task_id,
                    'result': result_data,
                    'model_type': model_type,
                    'status': 'completed',
                    'filename': filename,
                    'timestamp': datetime.utcnow()
                }
            else:
                probability = torch.sigmoid(output).item()
                prediction = 1 if probability > 0.5 else 0

                result_data = {
                    'prediction_class': int(prediction),
                    'probability': probability,
                    'recommendation': "⚠️ GLAUCOME DÉTECTÉ" if prediction == 1 else "✅ Aucun glaucome",
                    'grad_cam': grad_cam_base64,
                }

                result = {
                    'task_id': task_id,
                    'result': result_data,
                    'model_type': model_type,
                    'status': 'completed',
                    'filename': filename,
                    'timestamp': datetime.utcnow()
                }

        # Sauvegarde MongoDB
        if db is not None:
            db.diagnostic_results.insert_one(result)
            logger.info(f"✓ Diagnostic sauvegardé: {task_id}")

        return result

    except Exception as e:
        logger.error(f"✗ Erreur diagnostic: {e}")
        if db is not None:
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
