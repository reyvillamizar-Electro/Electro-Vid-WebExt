# Electro Vid-WebExt

Aplicación de escritorio para detectar, inspeccionar y previsualizar fuentes de video expuestas por páginas web.

## Estado actual

La aplicación incluye:

- interfaz gráfica con PySide6;
- inicio maximizado;
- campo para pegar una URL;
- análisis en segundo plano para no congelar la interfaz;
- detección de elementos `<video>`, `<source>`, metadatos OpenGraph y enlaces directos;
- reconocimiento de MP4, WebM, HLS (`.m3u8`), DASH (`.mpd`), MOV y M4V;
- tabla con tipo, duración, calidad, **codec**, tamaño, origen y URL;
- análisis concurrente de metadatos;
- pestaña interna de previsualización;
- reproducción mediante **mpv**;
- controles de reproducir/pausar, detener y desplazarse por el video;
- acciones para abrir una fuente o copiar su URL.

## Motor de reproducción

Electro Vid-WebExt usa **mpv** para la previsualización en lugar de Qt Multimedia.

Se inicia con una configuración orientada a compatibilidad:

- `hwdec=auto-safe`: intenta aceleración por hardware solo cuando es segura;
- si un codec como AV1, HEVC o VP9 no puede decodificarse por GPU, mpv puede recurrir a decodificación por software;
- `gpu-api=auto`: deja que mpv elija el backend gráfico apropiado;
- `vd-lavc-dr=no`: evita algunos problemas de direct rendering entre decodificadores y drivers.

Esto reduce los problemas que pueden aparecer cuando Windows intenta forzar D3D11 para un codec no soportado por la GPU.

## Metadatos

FFprobe se utiliza para obtener:

- duración;
- resolución real;
- codec de video, por ejemplo H.264/AVC, H.265/HEVC, AV1, VP9;
- la aplicación intenta además obtener el tamaño mediante los encabezados HTTP.

En HLS/DASH puede no existir un tamaño único porque el contenido se entrega en segmentos.

## Requisitos

- Windows 10/11
- Python 3.14
- uv
- FFmpeg / ffprobe
- mpv

Comprueba las herramientas con:

```powershell
ffprobe -version
mpv --version
```

Si `mpv --version` no funciona, la aplicación seguirá pudiendo analizar videos y leer metadatos, pero la pestaña de previsualización avisará que falta mpv.

## Ejecutar

```powershell
git pull
uv sync
uv run main.py
```

## Próxima etapa

La siguiente fase incorporará QtWebEngine e inspección de tráfico para detectar fuentes creadas dinámicamente por JavaScript, incluyendo casos con `blob:`, HLS y DASH.

## Uso responsable

La herramienta está pensada para analizar fuentes multimedia accesibles normalmente por el navegador. No intenta eludir DRM, controles de acceso ni protecciones de servicios.
