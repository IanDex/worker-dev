"""
Junta el config.json del zip con el que ya está instalado, para que actualizar no borre nada: se conserva lo instalado
(token, carpeta de repos, puerto) y solo se toma del zip lo que trae lleno y no es el texto de ejemplo.

    python fusionar_config.py <config del zip o vacío> <config instalado>
"""
import json
import sys
from pathlib import Path

EJEMPLO = "PEGA-AQUI"


def leer(ruta: str) -> dict:
    try:
        return json.loads(Path(ruta).read_text(encoding="utf-8")) if ruta and Path(ruta).exists() else {}
    except ValueError:
        return {}


nuevo, instalado = leer(sys.argv[1]), leer(sys.argv[2])
final = dict(instalado)
for clave, valor in nuevo.items():
    if valor in ("", None) or (isinstance(valor, str) and EJEMPLO in valor):
        continue
    final[clave] = valor
if not final.get("token") or EJEMPLO in str(final.get("token")):
    sys.exit("Falta tu token: copia config.ejemplo.json como config.json y pega el token que te dio el revisor.")
final.setdefault("api", "https://b2b.global66.tech")
Path(sys.argv[2]).parent.mkdir(parents=True, exist_ok=True)
Path(sys.argv[2]).write_text(json.dumps(final, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"config listo ({'actualizado' if instalado else 'nuevo'})")
