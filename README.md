# Electro Vid-WebExt

Aplicación de escritorio para detectar fuentes de video expuestas por páginas web y abrirlas de forma independiente.

## Estado actual

La primera versión incluye:

- interfaz gráfica con PySide6;
- campo para pegar una URL;
- análisis en segundo plano para no congelar la interfaz;
- detección de elementos `<video>`, `<source>`, metadatos OpenGraph y enlaces directos;
- reconocimiento de MP4, WebM, HLS (`.m3u8`), DASH (`.mpd`), MOV y M4V;
- tabla de resultados;
- acciones para abrir una fuente o copiar su URL.

Esta versión analiza el HTML entregado directamente por el servidor. La siguiente etapa añadirá un navegador embebido con QtWebEngine e inspección de tráfico para detectar reproductores y streams creados dinámicamente con JavaScript.

## Requisitos

- Windows 10/11
- Python 3.14
- uv

## Ejecutar

```powershell
git pull
uv sync
uv run main.py
```

## Estructura

```text
Electro-Vid-WebExt/
├── main.py
├── pyproject.toml
├── electro_vid_webext/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   └── detector.py
│   └── ui/
│       ├── __init__.py
│       └── main_window.py
└── README.md
```

## Uso responsable

La herramienta está pensada para analizar fuentes multimedia accesibles normalmente por el navegador. No intenta eludir DRM, controles de acceso ni protecciones de servicios.
