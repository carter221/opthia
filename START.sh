#!/bin/bash

# ╔══════════════════════════════════════════════════════════════════════╗
# ║   INSTALLATION & LANCEMENT - ARCHITECTURE MICROSERVICES COMPLÈTE   ║
# ║              Diagnostic Ophtalmologique avec Grad-CAM               ║
# ╚══════════════════════════════════════════════════════════════════════╝

# Couleurs pour l'output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  🚀 LANCEMENT DE L'ARCHITECTURE MICROSERVICES             ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"

# 1. Se placer dans le répertoire du projet
echo -e "\n${YELLOW}[1/5]${NC} Navigation vers le répertoire du projet..."
cd "/Users/seitchajudicael/Desktop/documents perso/IPSSI/Memoire/ophtia" || exit 1
echo -e "${GREEN}✓${NC} Répertoire: $(pwd)"

# 2. Vérifier Docker
echo -e "\n${YELLOW}[2/5]${NC} Vérification de Docker..."
if ! command -v docker &> /dev/null; then
    echo -e "${RED}✗ Docker n'est pas installé${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} Docker: $(docker --version)"

# 3. Arrêter les services précédents (optionnel)
echo -e "\n${YELLOW}[3/5]${NC} Arrêt des services précédents..."
docker-compose down 2>/dev/null || true
echo -e "${GREEN}✓${NC} Services arrêtés"

# 4. Construire et lancer les services
echo -e "\n${YELLOW}[4/5]${NC} Construction et lancement des services..."
echo "  Services à démarrer:"
echo "    - Backend (Flask) - Port 5001"
echo "    - Frontend (Streamlit) - Port 8501"
echo "    - MongoDB - Port 27017"
echo "    - RabbitMQ - Port 5672/15672"
echo "    - Worker - Consomme les tâches"

docker-compose up -d

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓${NC} Services lancés"
else
    echo -e "${RED}✗ Erreur lors du lancement${NC}"
    exit 1
fi

# 5. Vérifier la santé des services
echo -e "\n${YELLOW}[5/5]${NC} Vérification de la santé des services..."
echo "  Attente de 10 secondes pour stabilisation..."

sleep 10

echo -e "\n${GREEN}✓${NC} État des services:"
docker-compose ps

# Afficher les URLs
echo -e "\n${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                    SERVICES PRÊTS                          ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"

echo -e "\n📱 ${YELLOW}INTERFACE UTILISATEUR${NC}"
echo -e "   Frontend Streamlit: ${GREEN}http://localhost:8501${NC}"

echo -e "\n🔧 ${YELLOW}SERVICES${NC}"
echo -e "   Backend API:        ${GREEN}http://localhost:5001${NC}"
echo -e "   RabbitMQ Admin:     ${GREEN}http://localhost:15672${NC} (guest/guest)"
echo -e "   MongoDB:            ${GREEN}mongodb://localhost:27017/ophtia${NC}"

echo -e "\n📚 ${YELLOW}DOCUMENTATION${NC}"
echo -e "   Architecture:       ${GREEN}./ARCHITECTURE_MICROSERVICES.md${NC}"
echo -e "   Guide Grad-CAM:     ${GREEN}./GUIDE_GRADCAM_MICROSERVICES.md${NC}"
echo -e "   Pipelines:          ${GREEN}./PIPELINES.md${NC}"

echo -e "\n🧪 ${YELLOW}TESTS${NC}"
echo -e "   Script test:        ${GREEN}python test_architecture.py${NC}"

echo -e "\n📝 ${YELLOW}LOGS${NC}"
echo -e "   Backend:            ${GREEN}docker-compose logs -f backend${NC}"
echo -e "   Worker:             ${GREEN}docker-compose logs -f worker${NC}"
echo -e "   Tous:               ${GREEN}docker-compose logs -f${NC}"

echo -e "\n🛑 ${YELLOW}ARRÊT${NC}"
echo -e "   Arrêter services:   ${GREEN}docker-compose down${NC}"
echo -e "   Arrêt complet:      ${GREEN}docker-compose down -v${NC}"

echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "\n✨ ${GREEN}Tout est prêt! Ouvrez${NC} ${YELLOW}http://localhost:8501${NC} ${GREEN}dans votre navigateur.${NC}\n"
