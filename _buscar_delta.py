import sys
sys.path.insert(0, '.')
from cafci_loader import _get_json, BASE_URL
import os

# El loader ya carga secrets al import
token = os.environ.get('CAFCI_TOKEN', '')
print(f'Token: {"OK" if token else "NO"}')
print(f'BASE_URL: {BASE_URL}')
print()
print('Pidiendo /reports/daily...')
data = _get_json(BASE_URL + '/reports/daily', token)
records = data.get('records', [])
print(f'Total clases de fondos: {len(records)}')
print()
print('=== Buscando Delta Gestion ===')
for r in records:
    nombre = r.get('nombreDeLaClaseDeFondo', '')
    if 'delta' in nombre.lower():
        print(f"  idClase={r.get('idClase'):<6} | {nombre} | moneda={r.get('moneda')}")
