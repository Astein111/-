from flask import Flask, request
import easyocr
from googletrans import Translator
from gtts import gTTS
import os
app = Flask(__name__)
reader = easyocr.Reader(['en', 'es', 'fr', 'de', 'ru', 'bg'])
translator = Translator()
@app.route('/upload', methods=['POST'])
def upload_image():
    image_data = request.data
    with open("captured.jpg", "wb") as f:
        f.write(image_data)
    print("Снимката е получена от очилата!")
    results = reader.readtext("captured.jpg")
    extracted_text = " ".join([res[1] for res in results])
    print(f"Разчетен текст: {extracted_text}")
    if not extracted_text.strip():
        return "Няма намерен текст", 200
    target_lang = 'bg' 
    translated = translator.translate(extracted_text, dest=target_lang)
    print(f"🔄 Превод: {translated.text}")
    tts = gTTS(text=translated.text, lang=target_lang)
    tts.save("speech.mp3")
    os.system("start speech.mp3" if os.name == 'nt' else "mpg123 speech.mp3")
    return f"Преведено: {translated.text}", 200
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)