# -*- coding: utf-8 -*-
"""
engine/categories.py

Catálogo canónico de categorías de gastos/ingresos del hogar y normalización
de texto libre a (grupo, categoria).

El campo `category` de la tabla `events` sigue siendo texto libre (cero fricción
para cargar), pero para el reporte de flujo de caja estilo estado de resultados
necesitamos agrupar variantes ("luz", "edenor", "electricidad") bajo una misma
categoría canónica ("Luz") y un grupo ("Servicios").

Uso:
    from engine.categories import normalize_category, CANONICAL_GROUPS
    grupo, categoria = normalize_category("Pago de Edenor")
    # -> ("Servicios", "Luz")
"""

from __future__ import annotations

import unicodedata
from typing import Optional

# Grupo "fallback" cuando no reconocemos la categoría.
OTHER_GROUP = "Otros"

# Catálogo canónico: grupo -> {categoria_canonica: [palabras clave]}
# El orden importa: se evalúa categoría por categoría y gana la primera que
# matchea una palabra clave dentro del texto normalizado.
_CATALOG: dict[str, dict[str, list[str]]] = {
    "Vivienda": {
        "Alquiler": ["alquiler", "renta", "rent"],
        "Expensas": ["expensas", "expensa", "consorcio"],
        "Hipoteca": ["hipoteca", "mortgage", "credito hipotecario"],
    },
    "Servicios": {
        "Luz": ["luz", "electricidad", "edenor", "edesur", "epec", "epe"],
        "Gas": ["gas", "metrogas", "naturgy", "ecogas", "camuzzi"],
        "Agua": ["agua", "aysa", "aguas"],
        "Internet": ["internet", "wifi", "fibertel", "telecentro", "iplan"],
        "Telefonia": ["telefono", "telefonia", "celular", "movistar", "claro",
                       "personal", "tuenti"],
        "Cable/Streaming": ["cable", "directv", "netflix", "spotify", "disney",
                             "hbo", "max", "prime video", "youtube premium",
                             "streaming", "flow"],
    },
    "Alimentacion": {
        "Supermercado": ["super", "supermercado", "almacen", "verduleria",
                          "carniceria", "dia", "coto", "carrefour", "jumbo",
                          "vea", "chango", "mercado"],
        "Restaurantes": ["restaurante", "resto", "bar", "cafe", "delivery",
                          "pedidosya", "rappi", "mcdonald", "burger"],
    },
    "Transporte": {
        "Combustible": ["nafta", "combustible", "ypf", "shell", "axion",
                         "gasoil", "estacion"],
        "Transporte publico": ["sube", "colectivo", "subte", "tren", "bondi"],
        "Taxi/App": ["taxi", "uber", "cabify", "didi", "remis"],
        "Auto": ["auto", "patente", "vtv", "seguro auto", "cochera",
                  "estacionamiento", "peaje", "mecanico", "taller"],
    },
    "Salud": {
        "Obra social/Prepaga": ["prepaga", "obra social", "osde", "swiss medical",
                                 "galeno", "medife", "omint"],
        "Farmacia": ["farmacia", "remedio", "medicamento"],
        "Medico": ["medico", "doctor", "dentista", "consulta", "estudios",
                    "laboratorio", "kinesiologo", "psicologo"],
    },
    "Educacion": {
        "Colegio/Universidad": ["colegio", "universidad", "facultad", "cuota escolar",
                                 "matricula", "curso", "capacitacion"],
        "Utiles": ["utiles", "libros", "libreria"],
    },
    "Ocio": {
        "Entretenimiento": ["cine", "teatro", "show", "recital", "juego",
                             "entretenimiento", "salida"],
        "Viajes": ["viaje", "vacaciones", "hotel", "vuelo", "pasaje", "aerolineas",
                    "turismo", "airbnb"],
        "Gimnasio/Deporte": ["gimnasio", "gym", "deporte", "club", "padel",
                              "futbol"],
    },
    "Personal": {
        "Indumentaria": ["ropa", "indumentaria", "zapatillas", "calzado",
                          "vestimenta"],
        "Cuidado personal": ["peluqueria", "barberia", "cosmetica", "perfumeria",
                              "estetica"],
        "Hogar/Compras": ["mueble", "electrodomestico", "ferreteria", "bazar",
                           "decoracion", "hogar"],
    },
    "Impuestos": {
        "Impuestos": ["impuesto", "afip", "arba", "agip", "monotributo",
                       "ingresos brutos", "iibb", "abl", "rentas", "tasa"],
        "Comisiones bancarias": ["comision", "mantenimiento cuenta", "comisiones"],
    },
    "Finanzas": {
        "Interes/Deuda": ["interes", "deuda", "punitorio", "financiacion"],
        "Seguros": ["seguro", "poliza"],
    },
    "Ingresos": {
        "Sueldo": ["sueldo", "salario", "haberes", "remuneracion"],
        "Honorarios": ["honorarios", "factura", "freelance", "consultoria"],
        "Dividendos": ["dividendo", "dividendos"],
        "Cupon/Renta": ["cupon", "renta", "interes ganado", "amortizacion"],
        "Aguinaldo": ["aguinaldo", "sac"],
        "Bono/Premio": ["bono", "premio", "comision ganada", "incentivo"],
        "Alquiler cobrado": ["alquiler cobrado", "renta cobrada", "inquilino"],
    },
}

# Lista plana de categorías canónicas, útil para autocompletar (datalist) en la UI.
CANONICAL_CATEGORIES: list[str] = [
    cat for grupo in _CATALOG.values() for cat in grupo.keys()
]

# Mapa grupo -> lista de categorías, expuesto para la UI / reportes.
CANONICAL_GROUPS: dict[str, list[str]] = {
    grupo: list(cats.keys()) for grupo, cats in _CATALOG.items()
}


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _normalize_text(text: str) -> str:
    return _strip_accents(text or "").lower().strip()


def normalize_category(text: Optional[str]) -> tuple[str, str]:
    """
    Mapea un texto libre de categoría a (grupo, categoria_canonica).

    - Busca palabras clave dentro del texto normalizado (sin acentos, minúsculas).
    - Si no reconoce nada, devuelve (OTHER_GROUP, texto_original_titulado) para
      no perder la información que cargó el usuario.
    """
    if not text or not str(text).strip():
        return (OTHER_GROUP, "Sin categoria")

    raw = str(text).strip()
    norm = _normalize_text(raw)

    for grupo, cats in _CATALOG.items():
        for categoria, keywords in cats.items():
            for kw in keywords:
                if kw in norm:
                    return (grupo, categoria)

    # No reconocida: conservamos el texto del usuario como categoría propia.
    return (OTHER_GROUP, raw)
