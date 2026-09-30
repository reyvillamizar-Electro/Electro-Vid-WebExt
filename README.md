# Electro Vid-WebExt

Aplicación de escritorio para detectar, inspeccionar, previsualizar y descargar fuentes de video expuestas por páginas web.

## Funciones actuales

- interfaz gráfica con PySide6;
- inicio maximizado y diseño adaptable al área útil de Windows;
- detección de elementos `<video>`, `<source>`, OpenGraph y enlaces directos;
- MP4, WebM, HLS (`.m3u8`), DASH (`.mpd`), MOV y M4V;
- tabla con tipo, duración, calidad, codec, tamaño, origen y URL;
- metadatos concurrentes mediante FFprobe;
- reproducción integrada con mpv;
- aceleración por hardware segura con fallback por software;
- reproducir/pausar, detener y desplazamiento temporal;
- volumen y silencio;
- repetición completa del video;
- bucle A–B entre dos puntos elegidos por el usuario;
- modo de pantalla completa con salida mediante Escape;
- descarga de archivos directos;
- descarga de HLS/DASH mediante FFmpeg cuando la fuente es accesible;
- abrir fuente y copiar URL.

## Reproductor mpv

Electro Vid-WebExt usa mpv como motor de reproducción.

La configuración prioriza compatibilidad:

- `hwdec=auto-safe`;
- `gpu-api=auto`;
- `vo=gpu-next`;
- `vd-lavc-dr=no`.

Si la GPU no soporta AV1, HEVC, VP9 u otro codec mediante hardware, mpv puede recurrir a decodificación por software.

La aplicación busca `mpv.exe` tanto en el PATH como en ubicaciones comunes de Windows, incluyendo:

```text
C:\Program Files\MPV Player\mpv.exe
```

## Bucle A–B

Durante la reproducción:

1. pulsa **A** en el punto inicial;
2. avanza hasta el punto final;
3. pulsa **B**;
4. mpv repetirá únicamente ese intervalo;
5. **A–B ✕** elimina el bucle.

El botón **Bucle** repite el archivo completo.

## Descargas

Para MP4, WebM, MOV y M4V accesibles directamente, la aplicación descarga el archivo por HTTP.

Para HLS y DASH utiliza FFmpeg con copia de streams cuando es posible.

La disponibilidad de descarga depende de cómo el servidor publique la fuente. La aplicación no intenta eludir DRM, autenticación ni controles de acceso.

## Requisitos

- Windows 10/11
- Python 3.14
- uv
- FFmpeg / FFprobe
- mpv

Comprobación:

```powershell
ffprobe -version
& "C:\Program Files\MPV Player\mpv.exe" --version
```

## Ejecutar

```powershell
git pull
uv sync
uv run main.py
```

## Próxima etapa

La siguiente fase incorporará QtWebEngine e inspección de tráfico para detectar fuentes creadas dinámicamente por JavaScript, incluidos reproductores con `blob:`, HLS y DASH.

## Uso responsable

La herramienta está pensada para trabajar con fuentes multimedia accesibles normalmente por el navegador y para contenido que el usuario tenga permiso de reproducir o guardar. No intenta eludir DRM ni controles de acceso.
