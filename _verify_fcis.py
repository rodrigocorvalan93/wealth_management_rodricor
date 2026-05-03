import sys
sys.path.insert(0, ".")
from cafci_loader import _get_json, BASE_URL
import os

token = os.environ.get("CAFCI_TOKEN", "")
if not token:
    print("ERROR: token CAFCI no configurado en secrets.txt")
    sys.exit(1)

# Mis tickers locales con keywords para matchear
TICKERS_TO_VERIFY = {
    "DELTA_AHORRO_PLUS_A": ["delta", "ahorro", "plus", "clase a"],
    "DELTA_RENTA_A": ["delta", "renta", "clase a"],
    "DELTA_ACCIONES_A": ["delta", "acciones", "clase a"],
    "DELTA_GESTION_VI_A": ["delta", "gestion vi", "clase a"],
    "DELTA_GESTION_XIII_A": ["delta", "gestion xiii", "clase a"],
    "DELTA_RETORNO_REAL_A": ["delta", "retorno real", "clase a"],
    "DELTA_LATINOAMERICA_A": ["delta", "latin", "clase a"],
    "DELTA_RENTA_DOLARES_D": ["delta", "renta", "dolares", "clase d"],
    "DELTA_SELECT_A": ["delta", "select", "clase a"],
    "FIMA_RENTA_FIJA_DOLARES_C": ["fima", "renta fija", "dolares", "clase c"],
    "FIMA_PREMIUM_DOLARES_A": ["fima", "premium", "clase a"],
}

print("Pidiendo /reports/daily...")
data = _get_json(BASE_URL + "/reports/daily", token)
records = data.get("records", [])
print(f"Total clases en CAFCI: {len(records)}")
print()

print("=" * 110)
print("MATCHES POR TICKER:")
print("=" * 110)

resultados = {}

for ticker, keywords in TICKERS_TO_VERIFY.items():
    matches = []
    for r in records:
        nombre = r.get("nombreDeLaClaseDeFondo", "")
        nombre_low = nombre.lower()
        # Todos los keywords deben aparecer
        if all(kw.lower() in nombre_low for kw in keywords):
            matches.append({
                "idClase": r.get("idClase"),
                "nombre": nombre,
                "moneda": r.get("moneda"),
                "vcp": r.get("vcp"),
            })

    print(f"\n{ticker}:")
    if not matches:
        print(f"  [SIN MATCH] keywords={keywords}")
        resultados[ticker] = None
    elif len(matches) == 1:
        m = matches[0]
        print(f"  ✓ idClase={m['idClase']} | {m['nombre']} | {m['moneda']} | vcp={m['vcp']}")
        resultados[ticker] = m["nombre"]
    else:
        print(f"  [{len(matches)} MATCHES — elegir manualmente]")
        for m in matches:
            print(f"    idClase={m['idClase']:<6} | {m['nombre']:<60} | {m['moneda']}")
        resultados[ticker] = None

# Generar fcis_cafci.txt con los matches únicos
print("\n" + "=" * 110)
print("GENERANDO fcis_cafci.txt (solo matches únicos):")
print("=" * 110)
lines = []
for ticker, nombre in resultados.items():
    if nombre:
        line = f"{ticker}|{nombre}"
        lines.append(line)
        print(f"  {line}")
    else:
        print(f"  # {ticker}|??? (resolver manualmente)")

with open("fcis_cafci_propuesto.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print(f"\nArchivo escrito: fcis_cafci_propuesto.txt ({len(lines)} líneas)")
