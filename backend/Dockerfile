# Utiliser une image Python légère basée sur Alpine
FROM python:3.10-slim

# Installer uniquement les dépendances système essentielles
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    libglib2.0-0 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Définir le répertoire de travail
WORKDIR /app

# Copier le fichier des dépendances
COPY requirements.txt .

# Installer les dépendances Python sans cache
RUN pip install --no-cache-dir -r requirements.txt

# Copier le reste du code
COPY . .

# Exposer le port
EXPOSE 5000

# Commande pour lancer l'application
CMD ["python3", "app.py"]
