# Prueba de motores de texto a voz (Issue #120)

Scripts con los que se generaron las muestras y las mediciones de la página pública
[`web/tts/`](../../web/tts/index.html) (en la web de pruebas: `/tts/`, sin login).

| Script | Qué hace |
| :--- | :--- |
| `piper_bench.py` | Piper local: tiempo de carga y de síntesis, duración del audio, RTF y RAM pico |
| `gemini_tts.py` | Gemini TTS por la API de AI Studio (`GEMINI_API_KEY`) |
| `whisper_wer.py` | Transcribe los `.mp3` con Whisper en Workers AI y calcula el WER contra el texto |
| `texto_prueba.txt` | Texto de prueba: unos 810 caracteres (~1 minuto) con números, siglas, nombres propios y voseo |

## Reproducir

Fuera del venv del backend (Piper no es una dependencia de la API):

```bash
python -m venv /tmp/tts && /tmp/tts/bin/pip install piper-tts==1.8.0
# Voz (cada una ~60-115 MB): https://huggingface.co/rhasspy/piper-voices/tree/main/es
taskset -c 0 /tmp/tts/bin/python piper_bench.py es_MX-claude-high.onnx texto_prueba.txt 1 claude.wav

GEMINI_API_KEY=... PLAIN=1 python gemini_tts.py gemini-3.8-flash-lite-tts Kore texto_prueba.txt lite.wav

# Workers AI (MeloTTS y Aura-2 ES) se llamó por la API REST:
#   POST https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/ai/run/@cf/myshell-ai/melotts
#        {"prompt": "<texto>", "lang": "es"}            → JSON con el WAV en base64
#   POST .../ai/run/@cf/deepgram/aura-2-es
#        {"text": "<texto>", "speaker": "aquila", "encoding": "mp3"}   → MP3

CF_ACCOUNT_ID=... CF_API_TOKEN=... python whisper_wer.py texto_prueba.txt <carpeta con los .mp3>
```

`taskset -c 0` limita Piper a un núcleo, como una vCPU de Railway. Las muestras se pasan a MP3 de
48 kbps con `ffmpeg -i x.wav -ac 1 -c:a libmp3lame -b:a 48k x.mp3` para compararlas en igualdad.
