# Electro Vid-WebExt

Aplicación de escritorio para detectar, inspeccionar, previsualizar y descargar fuentes de video expuestas por páginas web.

## Funciones actuales

- interfaz gráfica con PySide6;
- inicio maximizado y diseño adaptable al área útil de Windows;
- detección de elementos `<video>`, `<source>`, OpenGraph y enlaces directos;
- MP4, WebM, HLS (`.m3u8`), DASH (`.mpd`), MOV y M4V;
- tabla con tipo, duración, calidad, resolución exacta, codec, tamaño, protección, origen y URL;
- filtro global o por columna;
- ordenamiento al hacer clic en cualquier encabezado;
- columnas redimensionables manualmente y desplazamiento horizontal;
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
- progreso detallado de descarga con porcentaje, tiempo procesado, tamaño y velocidad cuando la fuente lo permite;
- cancelación desde la ventana de progreso, deteniendo la transferencia activa y eliminando solo el archivo parcial de ese intento;
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

La disponibilidad de descarga depende de cómo el servidor publique la fuente. Durante descargas con FFmpeg se muestra el tiempo procesado, duración total cuando puede determinarse, tamaño generado, velocidad relativa y porcentaje. Las descargas HTTP muestran bytes descargados, tamaño total cuando el servidor lo informa, velocidad y porcentaje. La ventana de progreso permite cancelar la descarga; al cancelar, se elimina únicamente el archivo temporal asociado a ese intento y no se tocan descargas anteriores. La aplicación no intenta eludir DRM ni controles de acceso.

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

## Detección dinámica

La aplicación incorpora QtWebEngine en la pestaña **Navegador**. Al cargar o reproducir contenido dentro de esa pestaña, inspecciona solicitudes de red y el DOM para incorporar nuevas fuentes MP4, WebM, HLS y DASH a la tabla de resultados sin duplicados.

Esto permite encontrar fuentes y variantes que no están presentes en el HTML inicial. Los enlaces `blob:` no se descargan directamente; se intenta detectar la fuente de red subyacente.

La pestaña **Navegador** también registra navegación del documento principal, cambios de URL, redirecciones, recargas y solicitudes de nuevas ventanas/popups. Los popups se registran pero no se abren automáticamente, de modo que una página no puede desplazar silenciosamente la sesión de extracción a otra ventana.

### Sesión del navegador

Cuando QtWebEngine captura una fuente, Electro Vid-WebExt conserva temporalmente el contexto útil de esa petición (Referer, User-Agent, Origin y cookies aplicables) y lo reutiliza con mpv, FFprobe y FFmpeg cuando es posible. Esto mejora la compatibilidad con servidores que reproducen correctamente en el navegador pero rechazan peticiones externas sin sesión.

El navegador integrado usa un perfil temporal/off-the-record: no persiste cookies ni permisos entre ejecuciones. También deniega permisos sensibles, bloquea selectores de archivos y ventanas emergentes, y cancela solicitudes WebAuth/passkey para evitar diálogos de autenticación ajenos a la extracción de video.

### Variantes HLS y protección

Los manifiestos HLS maestros se inspeccionan y sus variantes se agregan como filas separadas con pistas de calidad/resolución. Así puedes escoger una variante concreta en vez de depender siempre de la primera calidad disponible.

La columna **Protección** marca señales reconocibles de Widevine, PlayReady o FairPlay. En esos casos la aplicación no intenta reproducir ni descargar saltándose DRM. Un HLS cifrado convencional puede mostrarse como **Cifrado HLS** sin asumir automáticamente que sea DRM.

## Uso responsable

La herramienta está pensada para trabajar con fuentes multimedia accesibles normalmente por el navegador y para contenido que el usuario tenga permiso de reproducir o guardar. No intenta eludir DRM ni controles de acceso.
