import re
path = 'engine/importer.py'
with open(path, 'r', encoding='utf-8') as f:
    code = f.read()

# Buscar la función _read_rows y modificarla
old = '''    headers = []
    for c in range(1, ws.max_column + 1):
        h = ws.cell(row=header_row, column=c).value
        headers.append(h)'''

new = '''    headers = []
    for c in range(1, ws.max_column + 1):
        h = ws.cell(row=header_row, column=c).value
        if isinstance(h, str):
            h = h.strip()
        headers.append(h)'''

if old in code:
    code = code.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(code)
    print('Patch aplicado: headers ahora se strippean automaticamente')
else:
    print('WARN: no encontré el bloque a parchear (quizás ya está aplicado)')
