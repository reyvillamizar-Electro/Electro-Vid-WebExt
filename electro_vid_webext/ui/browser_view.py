from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

from electro_vid_webext.core.detector import VideoSource, media_kind_from_url

_MEDIA_HINT_RE = re.compile(
    r"(?:\.mp4|\.webm|\.m3u8|\.mpd|\.mov|\.m4v|\.mkv)(?:$|[?#])",
    re.IGNORECASE,
)


@dataclass(slots=True)
class _CookieRecord:
    name: str
    value: str
    domain: str
    path: str
    secure: bool


def _bytes_text(value) -> str:
    try:
        return bytes(value).decode("utf-8", errors="replace")
    except Exception:
        return str(value)


class MediaRequestInterceptor(QWebEngineUrlRequestInterceptor):
    media_found = Signal(object)
    navigation_seen = Signal(object)
    embedded_page_seen = Signal(str)

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:
        url = info.requestUrl().toString()
        kind = media_kind_from_url(url)

        try:
            is_main_frame = (
                info.resourceType()
                == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMainFrame
            )
        except Exception:
            is_main_frame = False

        if is_main_frame:
            self.navigation_seen.emit(
                {
                    "event": "Solicitud principal",
                    "url": url,
                    "first_party": info.firstPartyUrl().toString(),
                }
            )

        try:
            is_sub_frame = (
                info.resourceType()
                == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeSubFrame
            )
        except Exception:
            is_sub_frame = False

        if is_sub_frame:
            self.navigation_seen.emit(
                {
                    "event": "Iframe / subframe",
                    "url": url,
                    "first_party": info.firstPartyUrl().toString(),
                }
            )
            self.embedded_page_seen.emit(url)

        is_media_resource = (
            info.resourceType() == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMedia
        )
        if not kind and not is_media_resource and not _MEDIA_HINT_RE.search(url.lower()):
            return

        headers: dict[str, str] = {}
        try:
            for key, value in info.httpHeaders().items():
                headers[_bytes_text(key).lower()] = _bytes_text(value)
        except Exception:
            pass

        first_party = info.firstPartyUrl().toString()
        referer = headers.get("referer") or first_party or None
        origin = headers.get("origin")

        self.media_found.emit(
            {
                "url": url,
                "kind": kind or "Media",
                "origin": f"Red · {kind or 'Media'}",
                "referer": referer,
                "cookie_header": headers.get("cookie"),
                "origin_header": origin,
            }
        )


class QuietWebEnginePage(QWebEnginePage):
    certificate_problem = Signal(object)

    def __init__(self, profile: QWebEngineProfile, parent=None) -> None:
        super().__init__(profile, parent)

        # The embedded browser is an extractor, not a personal browser.
        # Deny sensitive browser permissions and cancel WebAuth/passkey flows.
        self.permissionRequested.connect(self._deny_permission)
        self.webAuthUxRequested.connect(self._cancel_webauth)
        self.fileSystemAccessRequested.connect(self._reject_file_system_access)
        try:
            self.certificateError.connect(self._reject_certificate_error)
        except Exception:
            pass

    @staticmethod
    def _deny_permission(permission) -> None:
        try:
            permission.deny()
        except Exception:
            pass

    @staticmethod
    def _cancel_webauth(request) -> None:
        try:
            request.cancel()
        except Exception:
            pass

    @staticmethod
    def _reject_file_system_access(request) -> None:
        try:
            request.reject()
        except Exception:
            pass

    def _reject_certificate_error(self, error) -> None:
        url = ""
        description = ""
        error_type = "Certificado inválido"

        try:
            url = error.url().toString()
        except Exception:
            pass

        try:
            description = str(error.description() or "")
        except Exception:
            pass

        try:
            error_type = str(error.type()).split(".")[-1]
        except Exception:
            pass

        try:
            error.rejectCertificate()
        except Exception:
            pass

        self.certificate_problem.emit(
            {
                "event": "SSL bloqueado",
                "url": url,
                "detail": description or error_type,
                "error_type": error_type,
            }
        )

    def chooseFiles(self, mode, old_files, accepted_mime_types):
        # Do not allow web pages to open local file pickers from extractor mode.
        return []

    def createWindow(self, window_type):
        # Block popup windows. They are not required for media extraction.
        return None

    def javaScriptConsoleMessage(
        self,
        level,
        message: str,
        line_number: int,
        source_id: str,
    ) -> None:
        # Suppress console noise produced by third-party pages. The extractor
        # surfaces its own failures through the application status/UI instead.
        return


class BrowserView(QWebEngineView):
    media_found = Signal(object)
    navigation_event = Signal(object)
    embedded_page_found = Signal(str)
    page_ready = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._cookies: dict[tuple[str, str, str], _CookieRecord] = {}
        self._initial_url = ""
        self._last_url = ""
        self._embedded_seen: set[str] = set()
        self._shutting_down = False

        # Clear data from the older versions that used Qt's default profile.
        # This profile is not used for browsing anymore.
        legacy_profile = QWebEngineProfile.defaultProfile()
        try:
            legacy_profile.cookieStore().deleteAllCookies()
            legacy_profile.clearHttpCache()
            legacy_profile.clearAllVisitedLinks()
            for permission in legacy_profile.listAllPermissions():
                permission.reset()
        except Exception:
            pass

        # A profile without a storage name is off-the-record: cookies/cache and
        # permissions are kept only for the lifetime of this BrowserView.
        app = QApplication.instance()
        self.profile = QWebEngineProfile(app)
        self.profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        )
        self.profile.setPersistentPermissionsPolicy(
            QWebEngineProfile.PersistentPermissionsPolicy.StoreInMemory
        )
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.setPage(QuietWebEnginePage(self.profile, self))
        self._media_compatibility_enabled = True
        self._install_credential_blocker()
        self._install_media_compatibility_shim()

        self.interceptor = MediaRequestInterceptor(self)
        self.interceptor.media_found.connect(self._on_network_media)
        self.interceptor.navigation_seen.connect(self._on_navigation_seen)
        self.interceptor.embedded_page_seen.connect(self._on_embedded_page_seen)
        self.profile.setUrlRequestInterceptor(self.interceptor)

        page = self.page()
        if isinstance(page, QuietWebEnginePage):
            page.certificate_problem.connect(self._on_certificate_problem)
        page.navigationRequested.connect(self._on_navigation_requested)
        page.newWindowRequested.connect(self._on_new_window_requested)
        self.urlChanged.connect(self._on_url_changed)
        self.loadStarted.connect(self._on_load_started)

        cookie_store = self.profile.cookieStore()
        cookie_store.cookieAdded.connect(self._cookie_added)
        cookie_store.cookieRemoved.connect(self._cookie_removed)

        self.loadFinished.connect(self._on_load_finished)

    def _install_credential_blocker(self) -> None:
        script = QWebEngineScript()
        script.setName("ElectroVidWebExt.DisableCredentialAPIs")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        script.setSourceCode(
            """
            (() => {
              const denied = () => Promise.reject(
                new DOMException(
                  'Credential and passkey APIs are disabled in extractor mode.',
                  'NotAllowedError'
                )
              );

              try {
                if (navigator.credentials) {
                  const proto = Object.getPrototypeOf(navigator.credentials);
                  if (proto) {
                    Object.defineProperty(proto, 'get', {
                      value: denied,
                      configurable: false,
                      writable: false
                    });
                    Object.defineProperty(proto, 'create', {
                      value: denied,
                      configurable: false,
                      writable: false
                    });
                    if ('store' in proto) {
                      Object.defineProperty(proto, 'store', {
                        value: denied,
                        configurable: false,
                        writable: false
                      });
                    }
                  }
                }
              } catch (_) {}

              try {
                Object.defineProperty(window, 'PublicKeyCredential', {
                  value: undefined,
                  configurable: false,
                  writable: false
                });
              } catch (_) {}
            })();
            """
        )
        self.page().scripts().insert(script)

    def _install_media_compatibility_shim(self) -> None:
        previous = getattr(self, "_media_compatibility_script", None)
        if previous is not None:
            try:
                self.page().scripts().remove(previous)
            except Exception:
                pass

        enabled_literal = (
            "true" if self._media_compatibility_enabled else "false"
        )

        script = QWebEngineScript()
        script.setName("ElectroVidWebExt.MediaCompatibility")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        script.setSourceCode(
            """
            (() => {
              if (window.__ELECTRO_MEDIA_COMPAT_INSTALLED__) {
                return;
              }

              window.__ELECTRO_MEDIA_COMPAT_INSTALLED__ = true;
              window.__ELECTRO_MEDIA_COMPAT__ = __DEFAULT_ENABLED__;

              const targetType = (value) => {
                const type = String(value || '').toLowerCase();
                if (!type) return false;

                const isH264 =
                  type.includes('video/mp4') &&
                  (type.includes('avc1') || type.includes('avc3'));

                const isAac =
                  (type.includes('audio/mp4') || type.includes('video/mp4')) &&
                  type.includes('mp4a');

                return isH264 || isAac;
              };

              try {
                const originalCanPlayType =
                  HTMLMediaElement.prototype.canPlayType;

                Object.defineProperty(
                  HTMLMediaElement.prototype,
                  'canPlayType',
                  {
                    configurable: true,
                    writable: true,
                    value: function(type) {
                      const actual = originalCanPlayType.call(this, type);
                      if (actual) return actual;
                      if (
                        window.__ELECTRO_MEDIA_COMPAT__ &&
                        targetType(type)
                      ) {
                        return 'probably';
                      }
                      return actual;
                    }
                  }
                );
              } catch (_) {}

              try {
                if (window.MediaSource && MediaSource.isTypeSupported) {
                  const originalIsTypeSupported =
                    MediaSource.isTypeSupported.bind(MediaSource);

                  MediaSource.isTypeSupported = function(type) {
                    const actual = originalIsTypeSupported(type);
                    if (actual) return true;
                    if (
                      window.__ELECTRO_MEDIA_COMPAT__ &&
                      targetType(type)
                    ) {
                      return true;
                    }
                    return false;
                  };
                }
              } catch (_) {}

              try {
                if (
                  navigator.mediaCapabilities &&
                  navigator.mediaCapabilities.decodingInfo
                ) {
                  const originalDecodingInfo =
                    navigator.mediaCapabilities.decodingInfo.bind(
                      navigator.mediaCapabilities
                    );

                  navigator.mediaCapabilities.decodingInfo =
                    async function(config) {
                      const actual = await originalDecodingInfo(config);
                      if (!window.__ELECTRO_MEDIA_COMPAT__) return actual;

                      const videoType =
                        config && config.video
                          ? config.video.contentType
                          : '';
                      const audioType =
                        config && config.audio
                          ? config.audio.contentType
                          : '';

                      if (
                        !actual.supported &&
                        (targetType(videoType) || targetType(audioType))
                      ) {
                        return {
                          supported: true,
                          smooth: false,
                          powerEfficient: false
                        };
                      }
                      return actual;
                    };
                }
              } catch (_) {}
            })();
            """
        )
        source = script.sourceCode().replace(
            "__DEFAULT_ENABLED__",
            enabled_literal,
        )
        script.setSourceCode(source)
        self.page().scripts().insert(script)
        self._media_compatibility_script = script

    def set_media_compatibility(self, enabled: bool) -> None:
        self._media_compatibility_enabled = bool(enabled)
        self._install_media_compatibility_shim()
        value = "true" if enabled else "false"
        script = (
            "window.__ELECTRO_MEDIA_COMPAT__ = "
            + value
            + ";"
        )
        try:
            self.page().runJavaScript(script)
        except Exception:
            pass

    def media_compatibility_enabled(self) -> bool:
        return self._media_compatibility_enabled

    def shutdown(self) -> None:
        """Stop web activity before the main window is destroyed."""
        self._shutting_down = True
        try:
            self.stop()
        except Exception:
            pass

        try:
            self.profile.setUrlRequestInterceptor(None)
        except Exception:
            pass

        try:
            self.setUrl(QUrl("about:blank"))
        except Exception:
            pass

        try:
            page = self.page()
            page.deleteLater()
        except Exception:
            pass

    def load_page(self, url: str) -> None:
        self._initial_url = url
        self._last_url = ""
        self._embedded_seen.clear()
        self.navigation_event.emit(
            {
                "event": "URL inicial",
                "from": "",
                "to": url,
                "detail": "Solicitada por Electro Vid-WebExt",
            }
        )
        self.setUrl(QUrl(url))

    def _on_certificate_problem(self, problem: object) -> None:
        if not isinstance(problem, dict):
            return

        self.navigation_event.emit(
            {
                "event": str(problem.get("event") or "SSL bloqueado"),
                "from": self.url().toString(),
                "to": str(problem.get("url") or ""),
                "detail": str(problem.get("detail") or problem.get("error_type") or ""),
            }
        )

    def _on_embedded_page_seen(self, url: str) -> None:
        if not url or url in self._embedded_seen:
            return
        if url.startswith(("about:", "data:", "blob:")):
            return
        self._embedded_seen.add(url)
        self.embedded_page_found.emit(url)

    def _on_navigation_seen(self, context: object) -> None:
        if not isinstance(context, dict):
            return
        url = str(context.get("url") or "")
        if not url:
            return
        self.navigation_event.emit(
            {
                "event": str(context.get("event") or "Solicitud principal"),
                "from": str(context.get("first_party") or ""),
                "to": url,
                "detail": "Solicitud de documento principal observada en red",
            }
        )

    def _on_navigation_requested(self, request) -> None:
        try:
            if not request.isMainFrame():
                return
            url = request.url().toString()
            navigation_type = str(request.navigationType()).split(".")[-1]
        except Exception:
            return

        self.navigation_event.emit(
            {
                "event": "Navegación",
                "from": self.url().toString(),
                "to": url,
                "detail": navigation_type,
            }
        )

    def _on_new_window_requested(self, request) -> None:
        try:
            url = request.requestedUrl().toString()
            destination = str(request.destination()).split(".")[-1]
            initiated = bool(request.isUserInitiated())
        except Exception:
            return

        self.navigation_event.emit(
            {
                "event": "Popup / nueva ventana",
                "from": self.url().toString(),
                "to": url,
                "detail": (
                    f"{destination} · "
                    + ("iniciado por usuario" if initiated else "automático/bloqueado")
                ),
            }
        )

        kind = media_kind_from_url(url)
        if kind:
            self.media_found.emit(
                self.session_source(
                    url=url,
                    kind=kind,
                    origin=f"Popup · {kind}",
                    referer=self.url().toString() or None,
                )
            )

        # Intentionally do not call request.openIn(): extractor mode records
        # the target but does not allow arbitrary popups to take over the UI.

    def _on_load_started(self) -> None:
        QTimer.singleShot(
            0,
            lambda: self.set_media_compatibility(
                self._media_compatibility_enabled
            ),
        )
        requested = ""
        try:
            requested = self.page().requestedUrl().toString()
        except Exception:
            pass

        self.navigation_event.emit(
            {
                "event": "Carga iniciada",
                "from": self._last_url,
                "to": requested or self.url().toString(),
                "detail": "",
            }
        )

    def _on_url_changed(self, url: QUrl) -> None:
        current = url.toString()
        previous = self._last_url
        if current == previous:
            return

        requested = ""
        try:
            requested = self.page().requestedUrl().toString()
        except Exception:
            pass

        redirected = bool(requested and current and requested != current)
        self.navigation_event.emit(
            {
                "event": "Redirección" if redirected else "URL cambió",
                "from": previous,
                "to": current,
                "detail": (
                    f"Solicitada originalmente: {requested}"
                    if redirected
                    else ""
                ),
            }
        )
        self._last_url = current

    def session_source(
        self,
        url: str,
        kind: str,
        origin: str,
        referer: str | None = None,
        origin_header: str | None = None,
        cookie_header: str | None = None,
    ) -> VideoSource:
        return VideoSource(
            url=url,
            kind=kind,
            origin=origin,
            referer=referer or self.url().toString() or None,
            user_agent=self.profile.httpUserAgent(),
            cookie_header=cookie_header or self.cookie_header_for(url),
            origin_header=origin_header,
        )

    def cookie_header_for(self, url: str) -> str | None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path or "/"
        secure_request = parsed.scheme.lower() == "https"

        values: list[str] = []
        for cookie in self._cookies.values():
            domain = cookie.domain.lower().lstrip(".")
            domain_ok = host == domain or host.endswith(f".{domain}")
            path_ok = path.startswith(cookie.path or "/")
            secure_ok = not cookie.secure or secure_request
            if domain_ok and path_ok and secure_ok:
                values.append(f"{cookie.name}={cookie.value}")

        return "; ".join(values) if values else None

    def _cookie_added(self, cookie) -> None:
        record = _CookieRecord(
            name=_bytes_text(cookie.name()),
            value=_bytes_text(cookie.value()),
            domain=str(cookie.domain() or ""),
            path=str(cookie.path() or "/"),
            secure=bool(cookie.isSecure()),
        )
        self._cookies[(record.name, record.domain, record.path)] = record

    def _cookie_removed(self, cookie) -> None:
        key = (
            _bytes_text(cookie.name()),
            str(cookie.domain() or ""),
            str(cookie.path() or "/"),
        )
        self._cookies.pop(key, None)

    def _on_network_media(self, context: object) -> None:
        if not isinstance(context, dict):
            return

        url = str(context.get("url") or "")
        if not url:
            return

        source = self.session_source(
            url=url,
            kind=str(context.get("kind") or "Media"),
            origin=str(context.get("origin") or "Red"),
            referer=context.get("referer"),
            origin_header=context.get("origin_header"),
            cookie_header=context.get("cookie_header"),
        )
        self.media_found.emit(source)

    def _on_load_finished(self, ok: bool) -> None:
        final_url = self.url().toString()
        requested = ""
        try:
            requested = self.page().requestedUrl().toString()
        except Exception:
            pass

        self.navigation_event.emit(
            {
                "event": "Carga completada" if ok else "Carga fallida",
                "from": requested,
                "to": final_url,
                "detail": (
                    "URL final distinta de la solicitada"
                    if requested and final_url and requested != final_url
                    else ""
                ),
            }
        )

        if not ok:
            return
        self.set_media_compatibility(self._media_compatibility_enabled)
        self.rescan_dom()
        QTimer.singleShot(1000, self.rescan_dom)
        QTimer.singleShot(3000, self.rescan_dom)
        QTimer.singleShot(7000, self.rescan_dom)
        self.page_ready.emit()

    def rescan_dom(self) -> None:
        if self._shutting_down:
            return
        script = """
        (() => {
          const media = new Set();
          const iframes = new Set();

          const addMedia = (value) => {
            if (!value || typeof value !== 'string') return;
            if (value.startsWith('blob:') || value.startsWith('data:')) return;
            media.add(value);
          };

          const addFrame = (value) => {
            if (!value || typeof value !== 'string') return;
            if (value.startsWith('about:') || value.startsWith('data:') || value.startsWith('blob:')) return;
            try {
              iframes.add(new URL(value, document.baseURI).href);
            } catch (_) {}
          };

          document.querySelectorAll('video').forEach(video => {
            addMedia(video.currentSrc);
            addMedia(video.src);
          });

          document.querySelectorAll('source').forEach(source => addMedia(source.src));
          document.querySelectorAll('iframe[src], frame[src]').forEach(frame => addFrame(frame.src));

          try {
            performance.getEntriesByType('resource').forEach(entry => {
              addMedia(entry.name);
              const type = String(entry.initiatorType || '').toLowerCase();
              if (type === 'iframe' || type === 'frame') addFrame(entry.name);
            });
          } catch (_) {}

          try {
            const html = document.documentElement ? document.documentElement.innerHTML : '';
            const re = /https?:\\/\\/[^"'\\s<>]+?\\.(?:mp4|webm|m3u8|mpd|mov|m4v|mkv)(?:\\?[^"'\\s<>]*)?/gi;
            for (const match of html.matchAll(re)) addMedia(match[0].replace(/&amp;/g, '&'));
          } catch (_) {}

          return {
            media: Array.from(media),
            iframes: Array.from(iframes)
          };
        })();
        """
        self.page().runJavaScript(script, self._consume_dom_results)

    def _consume_dom_results(self, values) -> None:
        if self._shutting_down:
            return
        if not isinstance(values, dict):
            return

        media_values = values.get("media")
        if isinstance(media_values, list):
            for value in media_values:
                if not isinstance(value, str):
                    continue
                kind = media_kind_from_url(value)
                if not kind:
                    continue

                self.media_found.emit(
                    self.session_source(
                        url=value,
                        kind=kind,
                        origin=f"DOM/HTML dinámico · {kind}",
                        referer=self.url().toString() or None,
                    )
                )

        iframe_values = values.get("iframes")
        if isinstance(iframe_values, list):
            for value in iframe_values:
                if not isinstance(value, str):
                    continue
                self._on_embedded_page_seen(value)
