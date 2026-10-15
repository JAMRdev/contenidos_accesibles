"""
Prueba de conexión y latencia contra el Worker pasarela de D1 (Issue #58).

Usa D1_GATEWAY_URL y D1_GATEWAY_TOKEN del .env (o de las variables del entorno), así que
sirve tanto para el Worker local (`npm run dev` en backend/d1-gateway) como para el de
Cloudflare. No necesita DATABASE_BACKEND=d1.

    python scripts/d1_ping.py              # 20 consultas y 5 batch
    python scripts/d1_ping.py --n 100      # más muestras

Escribe en la tabla `gateway_healthcheck` (migración 0000) para probar también escrituras.
"""
import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.db.d1_client import D1Error, client_from_settings  # noqa: E402


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, round(pct / 100 * len(ordered)) - 1)]


def timed(fn) -> float:
    started = time.perf_counter()
    fn()
    return (time.perf_counter() - started) * 1000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=20, help="cantidad de consultas de lectura")
    parser.add_argument("--batches", type=int, default=5, help="cantidad de batch de escritura")
    args = parser.parse_args()

    print(f"Worker: {settings.D1_GATEWAY_URL}")
    try:
        with client_from_settings(retries=0) as db:
            assert db.query("SELECT ?1 + ?2 AS suma", [2, 3]).rows == [{"suma": 5}]
            reads = [timed(lambda: db.query("SELECT COUNT(*) AS n FROM gateway_healthcheck")) for _ in range(args.n)]
            writes = [
                timed(lambda: db.batch([
                    ("INSERT INTO gateway_healthcheck (source) VALUES (?)", ["d1_ping"]),
                    ("SELECT COUNT(*) AS n FROM gateway_healthcheck", []),
                ]))
                for _ in range(args.batches)
            ]
    except D1Error as e:
        print(f"ERROR: {e}")
        return 1

    for name, values in (("Lectura (query)", reads), ("Escritura (batch de 2)", writes)):
        print(f"{name}: n={len(values)} p50={statistics.median(values):.0f} ms "
              f"p95={percentile(values, 95):.0f} ms max={max(values):.0f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
