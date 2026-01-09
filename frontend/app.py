import streamlit as st
import requests
from PIL import Image
import io
import base64
import time

st.set_page_config(page_title="Diagnostic Ophtalmologique", layout="wide")
st.title("🏥 Diagnostic d'Ophtalmologie")

pathology_options = {
    "Rétinopathie Diabétique": "rd",
    "Glaucome": "glaucoma"
}

col1, col2 = st.columns([2, 1])

with col1:
    selected_pathology_label = st.selectbox("Choisissez la pathologie à détecter :", list(pathology_options.keys()))
    model_type = pathology_options[selected_pathology_label]

with col2:
    st.write("")  # Alignement
    show_gradcam = st.checkbox("🔍 Afficher Grad-CAM", value=False)

uploaded_file = st.file_uploader("Choisissez une image de fond d'œil...", type=["jpg", "png", "jpeg"])

if uploaded_file is not None:
    # Affichage original
    col1, col2 = st.columns(2)
    
    with col1:
        image = Image.open(uploaded_file)
        st.image(image, caption="Image téléchargée", use_column_width=True)
    
    # Bouton diagnostic
    if st.button("🔬 Lancer le diagnostic", use_container_width=True):
        with st.spinner("Analyse en cours..."):
            # Convertir l'image en bytes
            buf = io.BytesIO()
            image.save(buf, format='PNG')
            byte_im = buf.getvalue()

            # Adresse de l'API backend
            backend_url_predict = 'http://backend:5000/predict'
            backend_url_gradcam = 'http://backend:5000/predict_with_gradcam'
            
            multipart_payload = {
                'file': ('image.png', byte_im, 'image/png'),
                'model_type': (None, model_type)
            }

            try:
                # Prédiction avec ou sans Grad-CAM
                if show_gradcam:
                    response = requests.post(backend_url_gradcam, files=multipart_payload)
                else:
                    response = requests.post(backend_url_predict, files=multipart_payload)
                
                # Gérer le 202 (soumis) et attendre le résultat
                if response.status_code == 202:
                    # Extraire task_id
                    response_data = response.json()
                    task_id = response_data.get('task_id')
                    poll_url = response_data.get('poll_url', f'/result/{task_id}')
                    
                    # Construire l'URL complète pour le polling
                    if show_gradcam:
                        backend_poll = f'http://backend:5000{poll_url}'
                    else:
                        backend_poll = f'http://backend:5000{poll_url}'
                    
                    # Polling avec timeout
                    max_attempts = 30
                    attempt = 0
                    result = None
                    
                    while attempt < max_attempts:
                        time.sleep(1)
                        poll_response = requests.get(backend_poll)
                        
                        if poll_response.status_code == 200:
                            result = poll_response.json()
                            break
                        elif poll_response.status_code == 202:
                            # Toujours en cours
                            attempt += 1
                        else:
                            st.error(f"Erreur lors du polling: {poll_response.status_code}")
                            break
                    
                    if result is None:
                        st.error("⏱️ Timeout : le diagnostic a pris trop de temps")
                    else:
                        response_status = 200  # Simuler un succès
                elif response.status_code == 200:
                    result = response.json()
                    response_status = 200
                else:
                    st.error(f"Erreur du backend: {response.status_code}")
                    st.write(response.text)
                    response_status = response.status_code
                
                # Afficher le résultat si succès
                if response_status == 200 and result:
                    # Affichage du résultat
                    with col2:
                        st.markdown("### 📋 Résultat")
                        
                        # Classe
                        prediction = result.get("result", {}).get("prediction_class", "Erreur")
                        if prediction == 0:
                            st.success(f"Classe: {prediction} (NÉGATIF)")
                        else:
                            st.error(f"Classe: {prediction} (POSITIF)")
                        
                        # Recommandation
                        recommendation = result.get("result", {}).get("recommendation", "")
                        st.info(recommendation)
                        
                        # Probabilité
                        prob = result.get("result", {}).get("probability", 0)
                        st.metric("Confiance", f"{prob*100:.2f}%")
                    
                    # Affichage Grad-CAM si activé
                    if show_gradcam and 'grad_cam' in result.get("result", {}):
                        st.markdown("---")
                        st.markdown("### 🔥 Heatmap Grad-CAM")
                        st.markdown("*Zones d'intérêt identifiées par le modèle (rouge = important)*")
                        
                        # Décoder l'image base64
                        grad_cam_base64 = result['result']['grad_cam']
                        if grad_cam_base64.startswith('data:'):
                            grad_cam_base64 = grad_cam_base64.split(',')[1]
                        image_data = base64.b64decode(grad_cam_base64)
                        heatmap_image = Image.open(io.BytesIO(image_data))
                        st.image(heatmap_image, caption="Zones d'intérêt détectées", use_column_width=True)
                        
                        # Explication
                        st.caption("""
                        **Interprétation:**
                        - 🔴 **Rouge** : Zones très importantes pour la décision
                        - 🟡 **Orange** : Zones moyennement importantes
                        - 🟢 **Vert** : Zones moins importantes
                        """)
                    
                    # Détails supplémentaires (multiclass pour RD)
                    if model_type == 'rd' and 'all_probabilities' in result.get("result", {}):
                        with st.expander("📊 Détails multiclass (RD)"):
                            probs = result['result']['all_probabilities']
                            st.write({
                                'Aucune': f"{probs.get('class_0', 0)*100:.1f}%",
                                'Légère': f"{probs.get('class_1', 0)*100:.1f}%",
                                'Modérée': f"{probs.get('class_2', 0)*100:.1f}%",
                                'Sévère': f"{probs.get('class_3', 0)*100:.1f}%",
                                'Proliférative': f"{probs.get('class_4', 0)*100:.1f}%"
                            })
            
            except requests.exceptions.RequestException as e:
                st.error(f"❌ Erreur de connexion au backend: {e}")

