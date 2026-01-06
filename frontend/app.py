import streamlit as st
import requests
from PIL import Image
import io

st.title("Diagnostic d'Ophtalmologie")

pathology_options = {
    "Rétinopathie Diabétique": "rd",
    "Glaucome": "glaucoma"
}
selected_pathology_label = st.selectbox("Choisissez la pathologie à détecter :", list(pathology_options.keys()))
model_type = pathology_options[selected_pathology_label]

uploaded_file = st.file_uploader("Choisissez une image de fond d'œil...", type=["jpg", "png", "jpeg"])

if uploaded_file is not None:
    image = Image.open(uploaded_file)
    st.image(image, caption="Image téléchargée.", use_column_width=True)

    if st.button("Lancer le diagnostic"):
        st.write("Envoi de l'image pour le diagnostic...")

        # Convertir l'image en bytes pour l'envoyer à l'API
        buf = io.BytesIO()
        image.save(buf, format='PNG')
        byte_im = buf.getvalue()

        # Adresse de l'API backend
        backend_url = 'http://backend:5000/predict'
        
        # Correction : Envoyer les champs de formulaire et le fichier
        # dans le même dictionnaire 'files'.
        # Pour les champs non-fichier, on utilise un tuple (None, value).
        multipart_payload = {
            'file': ('image.png', byte_im, 'image/png'),
            'model_type': (None, model_type)
        }

        try:
            response = requests.post(backend_url, files=multipart_payload)
            if response.status_code == 200:
                st.success(f"Diagnostic : {response.json()}")
            else:
                st.error(f"Erreur du backend : {response.status_code} - {response.text}")
        except requests.exceptions.RequestException as e:
            st.error(f"Erreur de connexion au backend : {e}")

