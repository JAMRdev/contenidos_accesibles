"""Transcribe cada .mp3 de una carpeta con Whisper (Workers AI) y calcula el WER contra el texto.
Uso: CF_ACCOUNT_ID=... CF_API_TOKEN=... python whisper_wer.py <texto.txt> <carpeta>
"""
import base64, json, os, re, sys, time, unicodedata, urllib.error, urllib.request
from pathlib import Path
ACC = os.environ["CF_ACCOUNT_ID"]
ref = open(sys.argv[1], encoding="utf-8").read()
def words(t):
    t = unicodedata.normalize("NFKC", t).lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return t.split()
def wer(r, h):
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j-1] + 1, prev + (r[i-1] != h[j-1]))
            prev = cur
    return d[len(h)] / len(r)
results = {}
for f in sorted(Path(sys.argv[2]).glob("*.mp3")):
    body = json.dumps({"audio": base64.b64encode(f.read_bytes()).decode(), "language": "es"}).encode()
    req = urllib.request.Request(f"https://api.cloudflare.com/client/v4/accounts/{ACC}/ai/run/@cf/openai/whisper-large-v3-turbo",
                                 data=body, headers={"Authorization": f"Bearer {os.environ['CF_API_TOKEN']}", "Content-Type": "application/json"})
    for attempt in range(4):
        try:
            text = json.load(urllib.request.urlopen(req, timeout=300))["result"]["text"]
            break
        except urllib.error.HTTPError as e:
            if attempt == 3: raise
            time.sleep(5)
    results[f.stem] = text
    print(f"{f.stem:36s} WER {wer(words(ref), words(text)):.1%}")
json.dump(results, open(Path(sys.argv[2]) / "transcripciones.json", "w"), ensure_ascii=False, indent=1)
