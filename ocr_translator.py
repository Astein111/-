import os
import ctypes
import textwrap
import threading
import cv2
import numpy as np
import requests
import easyocr
import wordninja
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont
from deep_translator import GoogleTranslator, MyMemoryTranslator
from langdetect import detect, LangDetectException

ESP32_IP = "192.168.1.11"
SNAPSHOT_URL = f"http://{ESP32_IP}/capture"

LANG_MAP = {
    'english': 'en', 'en': 'en',
    'spanish': 'es', 'es': 'es',
    'french': 'fr', 'fr': 'fr',
    'german': 'de', 'de': 'de',
    'italian': 'it', 'it': 'it',
    'portuguese': 'pt', 'pt': 'pt',
    'dutch': 'nl', 'nl': 'nl',
    'korean': 'ko', 'ko': 'ko',
    'japanese': 'ja', 'ja': 'ja',
    'chinese': 'zh-CN', 'zh': 'zh-CN', 'zh-cn': 'zh-CN',
    'arabic': 'ar', 'ar': 'ar',
    'russian': 'ru', 'ru': 'ru'
}

MYMEMORY_MAP = {
    'en': 'en-GB', 'es': 'es-ES', 'fr': 'fr-FR',
    'de': 'de-DE', 'it': 'it-IT', 'pt': 'pt-PT',
    'nl': 'nl-NL', 'ja': 'ja-JP', 'ko': 'ko-KR',
    'zh-CN': 'zh-CN', 'zh': 'zh-CN', 'ar': 'ar-SA', 'ru': 'ru-RU'
}

latest_camera_frame = None
camera_error_msg = "Connecting to XIAO camera..."
stop_camera_thread = False

def camera_fetch_worker():
    global latest_camera_frame, camera_error_msg, stop_camera_thread
    session = requests.Session()
    while not stop_camera_thread:
        try:
            res = session.get(SNAPSHOT_URL, timeout=2.0)
            if res.status_code == 200:
                img_arr = np.frombuffer(res.content, dtype=np.uint8)
                frame = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
                if frame is not None:
                    latest_camera_frame = frame
                    camera_error_msg = None
                else:
                    camera_error_msg = "Received invalid JPEG from ESP32"
            else:
                camera_error_msg = f"ESP32 HTTP Error: {res.status_code}"
        except Exception:
            camera_error_msg = f"Cannot reach {SNAPSHOT_URL} (Check IP & close browser tabs)"

def get_unicode_font(size=20):
    font_candidates = [
        "C:/Windows/Fonts/malgun.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in font_candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()

def select_ocr_script_group():
    print("\nWhich alphabets should the camera look for in the image?")
    print("  [1] Western/European (EN, ES, FR, DE, IT, PT, NL)")
    print("  [2] Korean + English")
    print("  [3] Japanese + English")
    print("  [4] Chinese (Simplified) + English")
    print("  [5] Arabic + English")
    print("  [6] Russian (Cyrillic) + English")
    choice = input("Type 1-6 and press ENTER [Default: 1]: ").strip()
    groups = {
        '1': ['en', 'es', 'fr', 'de', 'it', 'pt', 'nl'],
        '2': ['ko', 'en'],
        '3': ['ja', 'en'],
        '4': ['ch_sim', 'en'],
        '5': ['ar', 'en'],
        '6': ['ru', 'en']
    }
    return groups.get(choice, groups['1'])

def smart_space_repair(raw_text, detected_lang):
    if detected_lang != 'en':
        return raw_text
    tokens = raw_text.split()
    repaired = []
    for token in tokens:
        if len(token) > 4 and token.isalpha() and not token.isupper():
            repaired.append(" ".join(wordninja.split(token)))
        else:
            repaired.append(token)
    return " ".join(repaired)

def translate_multilang(text, src_code, target_code):
    if src_code == target_code:
        return text
    try:
        return GoogleTranslator(source='auto', target=target_code).translate(text)
    except Exception:
        pass
    try:
        src_clean = src_code if src_code in MYMEMORY_MAP else 'en'
        src_tag = MYMEMORY_MAP.get(src_clean, f"{src_clean}-{src_clean.upper()}")
        tgt_tag = MYMEMORY_MAP.get(target_code, f"{target_code}-{target_code.upper()}")
        return MyMemoryTranslator(source=src_tag, target=tgt_tag).translate(text)
    except Exception as e:
        return f"[Translation Error: {e}]"

def speak_text(text, lang_code):
    if not text or text.startswith("[Translation Error"):
        return
    tts_lang = 'zh-CN' if lang_code.lower() in ['zh', 'zh-cn'] else lang_code[:2].lower()
    audio_file = os.path.abspath("tts_output.mp3")
    try:
        print(f"Speaking ({tts_lang.upper()})...")
        tts = gTTS(text=text, lang=tts_lang, slow=False)
        tts.save(audio_file)
        mci = ctypes.windll.winmm.mciSendStringW
        mci('close tts_audio', None, 0, None)
        mci(f'open "{audio_file}" type mpegvideo alias tts_audio', None, 0, None)
        mci('play tts_audio wait', None, 0, None)
        mci('close tts_audio', None, 0, None)
        if os.path.exists(audio_file):
            os.remove(audio_file)
    except Exception as e:
        print(f"[TTS Audio Error: {e}]")

def preprocess_xiao_frame(frame):
    h, w = frame.shape[:2]
    upscaled = cv2.resize(frame, (int(w * 1.5), int(h * 1.5)), interpolation=cv2.INTER_CUBIC)
    gaussian = cv2.GaussianBlur(upscaled, (0, 0), 2.0)
    return cv2.addWeighted(upscaled, 1.3, gaussian, -0.3, 0)

def draw_translation_overlay(frame, ocr_results, src_lang, original_text, target_lang, translated_text):
    annotated = frame.copy()
    for (bbox, text, conf) in ocr_results:
        if conf >= 0.25 and text.strip():
            pts = (np.array(bbox) / 1.5).astype(np.int32)
            cv2.polylines(annotated, [pts], isClosed=True, color=(0, 255, 0), thickness=2)
    rgb_img = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb_img)
    w, h = pil_img.size
    font_small = get_unicode_font(size=16)
    font_large = get_unicode_font(size=22)
    orig_lines = textwrap.wrap(f"Detected ({src_lang.upper()}): {original_text}", width=48)
    trans_lines = textwrap.wrap(f"Translated ({target_lang.upper()}): {translated_text}", width=42)
    banner_height = 25 + (len(orig_lines) * 22) + (len(trans_lines) * 28)
    canvas = Image.new("RGB", (w, h + banner_height), (25, 25, 25))
    canvas.paste(pil_img, (0, 0))
    draw = ImageDraw.Draw(canvas)
    y_cursor = h + 10
    for line in orig_lines:
        draw.text((15, y_cursor), line, font=font_small, fill=(190, 190, 190))
        y_cursor += 22
    y_cursor += 4
    for line in trans_lines:
        draw.text((15, y_cursor), line, font=font_large, fill=(80, 255, 120))
        y_cursor += 28
    return cv2.cvtColor(np.array(canvas), cv2.COLOR_RGB2BGR)

def main():
    global stop_camera_thread
    ocr_langs = select_ocr_script_group()
    print(f"Loading EasyOCR models for: {ocr_langs}...")
    reader = easyocr.Reader(ocr_langs, gpu=False)
    user_target = input("Enter target language (e.g. es, de, fr, ko, ja, en): ").strip().lower()
    target_code = LANG_MAP.get(user_target, "en")
    print(f"Target Output Language set to: {target_code.upper()}")
    cam_thread = threading.Thread(target=camera_fetch_worker, daemon=True)
    cam_thread.start()
    window_name = "XIAO ESP32-S3 Translator"
    print("Click the pop-up video window: Press [SPACE] to translate, [Q] to quit.")
    while True:
        if latest_camera_frame is not None:
            display_frame = latest_camera_frame.copy()
            cv2.rectangle(display_frame, (0, 0), (display_frame.shape[1], 32), (0, 0, 0), -1)
            cv2.putText(
                display_frame,
                f"LIVE | Target: {target_code.upper()} | Press [SPACE] to Translate | [Q] Quit",
                (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA
            )
            if camera_error_msg:
                cv2.putText(display_frame, "Reconnecting...", (10, 50),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA)
        else:
            display_frame = np.zeros((360, 640, 3), dtype=np.uint8)
            cv2.putText(display_frame, "Waiting for XIAO ESP32-S3 Camera...", (30, 140),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(display_frame, str(camera_error_msg), (30, 185),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 100, 255), 1, cv2.LINE_AA)
            cv2.putText(display_frame, "Press [Q] in this window to quit", (30, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
        cv2.imshow(window_name, display_frame)
        key = cv2.waitKey(30) & 0xFF
        if key in [ord('q'), 27]:
            stop_camera_thread = True
            break
        elif key == ord(' ') and latest_camera_frame is not None:
            snap = latest_camera_frame.copy()
            status_frame = snap.copy()
            cv2.rectangle(status_frame, (0, 0), (status_frame.shape[1], 40), (0, 0, 180), -1)
            cv2.putText(status_frame, "Scanning & Translating... Please wait", (15, 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
            cv2.imshow(window_name, status_frame)
            cv2.waitKey(1)
            processed_frame = preprocess_xiao_frame(snap)
            ocr_results = reader.readtext(processed_frame, detail=1, paragraph=False, width_ths=0.3)
            if not ocr_results:
                print("No text detected in camera view.")
                continue
            ocr_results.sort(key=lambda r: (round(r[0][0][1] / 25), r[0][0][0]))
            valid_words = [text.strip() for (_, text, conf) in ocr_results if conf >= 0.25 and text.strip()]
            if not valid_words:
                print("Low confidence text ignored.")
                continue
            raw_sentence = " ".join(valid_words)
            try:
                detected_src = detect(raw_sentence).lower()
                detected_src = 'zh-CN' if detected_src.startswith('zh') else detected_src[:2]
            except LangDetectException:
                detected_src = "en"
            cleaned_sentence = smart_space_repair(raw_sentence, detected_src)
            translated_sentence = translate_multilang(cleaned_sentence, detected_src, target_code)
            print(f"\nDetected ({detected_src.upper()}): {cleaned_sentence}")
            print(f"Translated ({target_code.upper()}): {translated_sentence}")
            overlay_img = draw_translation_overlay(
                snap, ocr_results, detected_src, cleaned_sentence, target_code, translated_sentence
            )
            cv2.imshow(window_name, overlay_img)
            cv2.waitKey(1)
            speak_text(translated_sentence, target_code)
            print("Press any key in the video window to return to live view...")
            cv2.waitKey(0)
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()