import sys
sys.path.insert(0, '.')
import os
# Listar todas las env vars con CAFCI
print('=== Vars de entorno antes de import ===')
for k in os.environ:
    if 'CAFCI' in k.upper(): print(f'  {k}: <set>')
print()
import cafci_loader
print()
print('=== Vars de entorno después de import ===')
for k in os.environ:
    if 'CAFCI' in k.upper(): print(f'  {k}: <set>')
print()
print('BASE_URL:', getattr(cafci_loader, 'BASE_URL', 'NO DEFINIDO'))
