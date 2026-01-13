import torch
import torch.nn.functional as F
import cv2
import numpy as np


class GradCAM:
    """
    Grad-CAM pour l'interprétabilité des modèles CNN.
    Génère une heatmap montrant les zones de l'image utilisées par le modèle.
    """

    def __init__(self, model, target_layer):
        """
        Args:
            model: Le modèle PyTorch à analyser
            target_layer: La couche cible (ex: model.layer4 pour ResNet)
        """
        self.model = model
        self.target_layer = target_layer
        self.gradients = None
        self.activations = None

        # Attacher les hooks
        self.target_layer.register_forward_hook(self.save_activation)
        self.target_layer.register_backward_hook(self.save_gradient)

    def save_activation(self, module, input, output):
        """Hook pour sauvegarder les activations de la couche cible."""
        self.activations = output.detach()

    def save_gradient(self, module, grad_input, grad_output):
        """Hook pour sauvegarder les gradients de la couche cible."""
        self.gradients = grad_output[0].detach()

    def generate_cam(self, input_tensor, class_idx=None):
        """
        Génère la heatmap Grad-CAM.

        Args:
            input_tensor: Tenseur d'entrée (batch_size, C, H, W)
            class_idx: Classe cible (si None, utilise la classe prédite)

        Returns:
            cam: Heatmap normalisée (H, W)
        """
        batch_size, _, h, w = input_tensor.size()

        # Prédiction forward
        logits = self.model(input_tensor)

        if class_idx is None:
            class_idx = logits.argmax(dim=1).item()

        # Backward pour obtenir les gradients
        self.model.zero_grad()
        target = logits[:, class_idx]
        target.sum().backward()

        # Calcul de Grad-CAM
        gradients = self.gradients[0]  # (C, H_feat, W_feat)
        activations = self.activations[0]  # (C, H_feat, W_feat)

        # Weighted sum des activations
        weights = gradients.mean(dim=(1, 2))  # (C,)
        cam = (weights[:, None, None] * activations).sum(dim=0)  # (H_feat, W_feat)

        # Normalisation ReLU + Min-Max
        cam = F.relu(cam)
        cam_min = cam.min()
        cam_max = cam.max()
        if cam_max - cam_min > 0:
            cam = (cam - cam_min) / (cam_max - cam_min)
        else:
            cam = torch.zeros_like(cam)

        return cam.cpu().numpy()


def apply_heatmap_to_image(image_np, cam, alpha=0.5, resize_to=None):
    """
    Superpose la heatmap Grad-CAM sur l'image originale.

    Args:
        image_np: Image originale (H, W, 3) en RGB
        cam: Heatmap Grad-CAM (H_feat, W_feat)
        alpha: Transparence de la heatmap (0-1)
        resize_to: Dimension cible (width, height) pour réduire l'image.
                   None = pas de redimensionnement

    Returns:
        result_image: Image avec heatmap superposée
    """
    # Redimensionner la CAM à la taille de l'image
    h, w = image_np.shape[:2]
    cam_resized = cv2.resize(cam, (w, h))

    # Convertir en colormap (Jet ou Viridis)
    heatmap = cv2.applyColorMap((cam_resized * 255).astype(np.uint8), cv2.COLORMAP_JET)
    heatmap_rgb = cv2.cvtColor(heatmap, cv2.COLOR_BGR2RGB)

    # Fusion avec image originale
    result = cv2.addWeighted(image_np, 1 - alpha, heatmap_rgb, alpha, 0)

    # Réduire l'image si demandé
    if resize_to is not None:
        result = cv2.resize(result, resize_to)

    return result.astype(np.uint8)


def encode_image_to_base64(image_np, quality=70):
    """Encode une image numpy en base64 pour transmission JSON avec compression JPEG.

    Args:
        image_np: Image numpy (RGB)
        quality: Qualité JPEG (1-100). Par défaut 70 pour bon équilibre taille/qualité
    """
    import base64

    # Encoder en JPEG avec compression (plus léger que PNG)
    img_bgr = cv2.cvtColor(image_np, cv2.COLOR_RGB2BGR)
    _, buffer = cv2.imencode('.jpg', img_bgr,
                             [cv2.IMWRITE_JPEG_QUALITY, quality])
    img_base64 = base64.b64encode(buffer).decode('utf-8')

    return f"data:image/jpeg;base64,{img_base64}"
