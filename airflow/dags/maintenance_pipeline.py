"""
DAG Airflow pour la maintenance de l'infrastructure Ophtia.
- Purge des fichiers Grad-CAM temporaires
- Archivage et nettoyage de la base de données MongoDB
- Sauvegarde des données critiques
"""

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.utils.dates import days_ago
from datetime import timedelta
import os
import shutil
from pathlib import Path
from pymongo import MongoClient
import logging

# Configuration
default_args = {
    'owner': 'ophtia',
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
    'execution_timeout': timedelta(minutes=15),
}

dag = DAG(
    'maintenance_pipeline',
    default_args=default_args,
    description='Pipeline de maintenance: nettoyage caches et sauvegardes',
    schedule_interval='0 2 * * *',  # Tous les jours à 2h du matin
    start_date=days_ago(1),
    tags=['maintenance', 'ophtia'],
)

logger = logging.getLogger(__name__)


def cleanup_gradcam_cache(**context):
    """Supprime les fichiers Grad-CAM temporaires de plus de 7 jours."""
    logger.info("[Maintenance] Nettoyage du cache Grad-CAM...")
    
    gradcam_cache_dir = '/app/gradcam_cache'
    if not os.path.exists(gradcam_cache_dir):
        logger.info(f"Répertoire {gradcam_cache_dir} n'existe pas")
        return {'status': 'ok', 'deleted_files': 0}
    
    try:
        deleted_count = 0
        now = context['execution_date'].timestamp()
        max_age_seconds = 7 * 24 * 3600  # 7 jours
        
        for filename in os.listdir(gradcam_cache_dir):
            file_path = os.path.join(gradcam_cache_dir, filename)
            
            if os.path.isfile(file_path):
                file_age = now - os.path.getmtime(file_path)
                
                if file_age > max_age_seconds:
                    try:
                        os.remove(file_path)
                        deleted_count += 1
                        logger.info(f"Supprimé: {filename}")
                    except Exception as e:
                        logger.warning(f"Impossible de supprimer {filename}: {e}")
        
        logger.info(f"✓ Nettoyage Grad-CAM: {deleted_count} fichiers supprimés")
        return {'status': 'ok', 'deleted_files': deleted_count}
        
    except Exception as e:
        logger.error(f"✗ Erreur nettoyage cache: {e}")
        raise


def cleanup_mongodb(**context):
    """Nettoie la base MongoDB: supprime les résultats de diagnostic de plus de 30 jours."""
    logger.info("[Maintenance] Nettoyage MongoDB...")
    
    mongo_uri = os.environ.get('MONGO_URI', 'mongodb://mongo:27017/ophtia')
    
    try:
        db_client = MongoClient(mongo_uri)
        db = db_client.get_default_database()
        
        # Récupérer la date limite (30 jours en arrière)
        from datetime import datetime
        cutoff_date = datetime.utcnow() - timedelta(days=30)
        
        # Supprimer les anciens résultats
        result = db.diagnostic_results.delete_many({
            'timestamp': {'$lt': cutoff_date},
            'status': 'completed'
        })
        
        logger.info(f"✓ MongoDB nettoyé: {result.deleted_count} documents supprimés")
        
        # Optimiser les indices
        db.diagnostic_results.create_index('task_id')
        db.diagnostic_results.create_index('timestamp')
        
        logger.info("✓ Indices MongoDB optimisés")
        
        return {'status': 'ok', 'deleted_documents': result.deleted_count}
        
    except Exception as e:
        logger.error(f"✗ Erreur MongoDB: {e}")
        raise
    finally:
        try:
            db_client.close()
        except:
            pass


def backup_mongodb(**context):
    """Crée une sauvegarde de la base MongoDB."""
    logger.info("[Maintenance] Sauvegarde MongoDB...")
    
    mongo_uri = os.environ.get('MONGO_URI', 'mongodb://mongo:27017/ophtia')
    backup_dir = '/app/backups'
    
    try:
        # Créer le répertoire de backup s'il n'existe pas
        os.makedirs(backup_dir, exist_ok=True)
        
        db_client = MongoClient(mongo_uri)
        db = db_client.get_default_database()
        
        # Créer un dump JSON de la collection diagnostic_results
        from datetime import datetime
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        backup_file = os.path.join(backup_dir, f'diagnostic_results_backup_{timestamp}.json')
        
        import json
        from bson import json_util
        
        diagnostics = list(db.diagnostic_results.find())
        
        with open(backup_file, 'w') as f:
            json.dump(diagnostics, f, default=json_util.default, indent=2)
        
        file_size = os.path.getsize(backup_file) / (1024 * 1024)  # En MB
        logger.info(f"✓ Sauvegarde créée: {backup_file} ({file_size:.2f} MB)")
        
        # Garder seulement les 7 dernières sauvegardes
        backup_files = sorted(
            [f for f in os.listdir(backup_dir) if f.startswith('diagnostic_results_backup_')],
            reverse=True
        )
        
        if len(backup_files) > 7:
            for old_backup in backup_files[7:]:
                old_path = os.path.join(backup_dir, old_backup)
                try:
                    os.remove(old_path)
                    logger.info(f"Ancien backup supprimé: {old_backup}")
                except Exception as e:
                    logger.warning(f"Impossible de supprimer {old_backup}: {e}")
        
        return {'status': 'ok', 'backup_file': backup_file, 'size_mb': file_size}
        
    except Exception as e:
        logger.error(f"✗ Erreur sauvegarde: {e}")
        raise
    finally:
        try:
            db_client.close()
        except:
            pass


def log_maintenance_summary(**context):
    """Récapitulatif des opérations de maintenance."""
    logger.info("════════════════════════════════════════")
    logger.info("   RÉSUMÉ MAINTENANCE")
    logger.info("════════════════════════════════════════")
    
    ti = context['task_instance']
    
    try:
        gradcam_result = ti.xcom_pull(task_ids='cleanup_gradcam')
        logger.info(f"✓ Grad-CAM: {gradcam_result['deleted_files']} fichiers supprimés")
    except:
        logger.warning("! Grad-CAM: résultat indisponible")
    
    try:
        mongodb_result = ti.xcom_pull(task_ids='cleanup_mongodb')
        logger.info(f"✓ MongoDB: {mongodb_result['deleted_documents']} documents supprimés")
    except:
        logger.warning("! MongoDB cleanup: résultat indisponible")
    
    try:
        backup_result = ti.xcom_pull(task_ids='backup_mongodb')
        logger.info(f"✓ Backup: {backup_result['size_mb']:.2f} MB sauvegardés")
    except:
        logger.warning("! Backup: résultat indisponible")
    
    logger.info("════════════════════════════════════════")


# Tâches du DAG
cleanup_gradcam_task = PythonOperator(
    task_id='cleanup_gradcam',
    python_callable=cleanup_gradcam_cache,
    provide_context=True,
    dag=dag,
)

cleanup_mongodb_task = PythonOperator(
    task_id='cleanup_mongodb',
    python_callable=cleanup_mongodb,
    provide_context=True,
    dag=dag,
)

backup_mongodb_task = PythonOperator(
    task_id='backup_mongodb',
    python_callable=backup_mongodb,
    provide_context=True,
    dag=dag,
)

summary_task = PythonOperator(
    task_id='maintenance_summary',
    python_callable=log_maintenance_summary,
    provide_context=True,
    dag=dag,
)

# Ordre d'exécution (cleanup en parallèle, backup, puis résumé)
[cleanup_gradcam_task, cleanup_mongodb_task] >> backup_mongodb_task >> summary_task
