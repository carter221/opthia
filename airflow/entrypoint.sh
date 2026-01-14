#!/bin/bash

# Attendre que PostgreSQL soit prêt
echo "Attente de PostgreSQL..."
while ! nc -z postgres 5432; do
  sleep 1
done
echo "PostgreSQL est prêt !"

# Initialiser la base de données Airflow
echo "Initialisation de la base de données Airflow..."
airflow db init

# Créer un utilisateur admin par défaut si nécessaire
airflow users create \
    --username admin \
    --firstname Admin \
    --lastname User \
    --role Admin \
    --email admin@ophtia.local \
    --password admin || true

# Exécuter la commande passée
exec "$@"
