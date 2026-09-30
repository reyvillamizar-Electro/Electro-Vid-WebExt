# Electro Vid-WebExt

Aplicación de escritorio para detectar, inspeccionar y previsualizar fuentes de video expuestas por páginas web.

## Estado actual

La aplicación incluye:

- interfaz gráfica con PySide6;
- campo para pegar una URL;
- análisis en segundo plano para no congelar la interfaz;
- detección de elementos `<video>`, `<source>`, metadatos OpenGraph y enlaces directos;
- reconocimiento de MP4, WebM, HLS (`.m3u8`), DASH (`.mpd`), MOV y M4V;
- tabla con tipo, duración, calidad, tamaño, origen y URL;
- pestaña interna de previsualización;
- controles de reproducir, pausar, detener y desplazarse por el video;
- acciones para abrir una fuente o copiar su URL.

## Metadatos

- **Tamaño:** se intenta obtener mediante encabezados HTTP.
- **Calidad:** se obtiene con `ffprobe` cuando está disponible; si no, también se intenta inferir desde nombres como `720p`, `1080p` o `1920x1080`.
- **Duración:** se obtiene con `ffprobe` cuando está disponible.

En streams HLS/DASH puede no existir un tamaño único porque el video se entrega en segmentos.

## Ejecutar

```powershell
git pull
uv sync
uv run main.py
```

## ffprobe opcional

La aplicación funciona sin `ffprobe`, pero para obtener duración y resolución de forma más fiable conviene tener FFmpeg instalado y `ffprobe` disponible en el PATH de Windows.

Puedes comprobarlo con:

```powershell
ffprobe -version
```

## Próxima etapa

La siguiente fase incorporará QtWebEngine e inspección de tráfico para detectar fuentes creadas dinámicamente por JavaScript, incluyendo casos con `blob:`, HLS y DASH.

## Uso responsable

La herramienta está pensada para analizar fuentes multimedia accesibles normalmente por el navegador. No intenta eludir DRM, controles de acceso ni protecciones de servicios.
