"""Piper local: carga, síntesis, duración, RTF y RAM pico. Uso: python piper_bench.py <voz.onnx> <texto.txt> <repeticiones> <salida.wav>"""
import resource, sys, time, wave
from piper import PiperVoice
model, text_path, repeat, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
text = "\n\n".join([open(text_path, encoding="utf-8").read()] * repeat)
t0 = time.perf_counter(); voice = PiperVoice.load(model); load = time.perf_counter() - t0
t0 = time.perf_counter()
with wave.open(out, "wb") as w:
    voice.synthesize_wav(text, w)
synth = time.perf_counter() - t0
with wave.open(out) as w:
    dur = w.getnframes() / w.getframerate()
rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(f"{model.split('/')[-1]} x{repeat}: carga {load:.1f}s, síntesis {synth:.1f}s, audio {dur:.0f}s, RTF {synth/dur:.3f}, RAM pico {rss:.0f} MB, chars {len(text)}")
