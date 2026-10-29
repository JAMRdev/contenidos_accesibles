"""Gemini TTS (AI Studio). Uso: GEMINI_API_KEY=... [PLAIN=1] python gemini_tts.py <modelo> <voz> <texto.txt> <salida.wav>

PLAIN=1 manda solo el texto: los modelos 3.8 leen en voz alta una instrucción de estilo que vaya junto al texto.
"""
import base64, json, os, sys, time, urllib.request, urllib.error, wave
model, voice, text_path, out = sys.argv[1:5]
text = open(text_path, encoding="utf-8").read()
prompt = text if os.environ.get("PLAIN") else "Leé en voz alta, con acento rioplatense, tono claro y pausado de docente:\n\n" + text
body = {"contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["AUDIO"],
                             "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                             data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
t0 = time.perf_counter()
try:
    data = json.load(urllib.request.urlopen(req, timeout=300))
except urllib.error.HTTPError as e:
    print(model, "HTTP", e.code, e.read().decode()[:300]); sys.exit()
elapsed = time.perf_counter() - t0
part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
pcm = base64.b64decode(part["data"])
if pcm[:4] == b"RIFF":
    pcm = pcm[pcm.index(b"data") + 8:]
rate = int(part["mimeType"].split("rate=")[1].split(";")[0]) if "rate=" in part["mimeType"] else 24000
with wave.open(out, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
usage = data.get("usageMetadata", {})
print(f"{model} {voice}: {elapsed:.1f}s, audio {len(pcm)/2/rate:.0f}s, {part['mimeType']}, tokens in={usage.get('promptTokenCount')} out={usage.get('candidatesTokenCount')}")
