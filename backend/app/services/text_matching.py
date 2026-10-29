"""
Comparación de citas de la IA con el texto del material: sin mayúsculas, comillas ni espacios de
más, y con los '...' del modelo como cortes. La usan el diagnóstico, la adaptación y la versión
sin imágenes (Issue #117).
"""
import re
import unicodedata


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[\"'“”‘’«»]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def fragment_parts(fragment: str) -> list[str]:
    """El modelo a veces abrevia con '...': cada tramo se compara por separado."""
    parts = [normalize(p) for p in re.split(r"\.{3}|…", fragment)]
    return [p for p in parts if p]
