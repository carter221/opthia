FROM apache/airflow:2.7.3-python3.10

# Installer uniquement les dépendances système essentielles
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsm6 \
    libxext6 \
    libxrender-dev \
    netcat-openbsd \
    bash \
    && rm -rf /var/lib/apt/lists/*

USER airflow

# Copier requirements.txt
COPY requirements.txt .

# Installer les dépendances Python sans cache
RUN pip install --no-cache-dir -r requirements.txt

# Répertoire de travail
WORKDIR /opt/airflow
