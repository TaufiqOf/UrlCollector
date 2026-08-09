#!/usr/bin/env python3

import json
import os
import sys
import traceback

from ctypes.util import find_library
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QUrl, Signal, QSize
from PySide6.QtGui import QAction, QIcon

from PySide6.QtWidgets import (
  QAbstractItemView,
    QApplication,
  QDialog,
  QDialogButtonBox,
    QFileDialog,
  QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
  QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
  QTableWidget,
  QTableWidgetItem,
  QTextEdit,
    QVBoxLayout,
    QWidget,
    QStyle,
)

from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineScript,
)

from PySide6.QtWebEngineWidgets import QWebEngineView

CAPTURE_PREFIX = "__XHR_CAPTURE__"

INJECT_SCRIPT = r'''
(function() {
  if (window.__xhrCollectorInstalled) {
    return;
  }
  window.__xhrCollectorInstalled = true;

  var MAX_BODY_PREVIEW = 5000;

  function nowIso() {
    return new Date().toISOString();
  }

  function toAbsoluteUrl(rawUrl) {
    try {
      return new URL(rawUrl, window.location.href).href;
    } catch (_err) {
      return String(rawUrl || "");
    }
  }

  function emitCapture(payload) {
    try {
      console.log("__XHR_CAPTURE__" + JSON.stringify(payload));
    } catch (_err) {}
  }

  function headersToObject(headers) {
    var obj = {};
    if (!headers) {
      return obj;
    }
    try {
      if (headers instanceof Headers) {
        headers.forEach(function(value, key) {
          obj[String(key).toLowerCase()] = String(value);
        });
        return obj;
      }
      if (Array.isArray(headers)) {
        headers.forEach(function(pair) {
          if (Array.isArray(pair) && pair.length >= 2) {
            obj[String(pair[0]).toLowerCase()] = String(pair[1]);
          }
        });
        return obj;
      }
      Object.keys(headers).forEach(function(key) {
        obj[String(key).toLowerCase()] = String(headers[key]);
      });
      return obj;
    } catch (_err) {
      return obj;
    }
  }

  function parseRawResponseHeaders(raw) {
    var obj = {};
    if (!raw) {
      return obj;
    }
    raw.split(/\r?\n/).forEach(function(line) {
      var idx = line.indexOf(":");
      if (idx <= 0) {
        return;
      }
      var key = line.slice(0, idx).trim().toLowerCase();
      var value = line.slice(idx + 1).trim();
      if (key) {
        obj[key] = value;
      }
    });
    return obj;
  }

  function isTextualContentType(contentType) {
    var ct = String(contentType || "").toLowerCase();
    if (!ct) {
      return false;
    }
    if (ct.indexOf("text/") === 0) {
      return true;
    }
    return (
      ct.indexOf("application/json") >= 0 ||
      ct.indexOf("application/javascript") >= 0 ||
      ct.indexOf("application/xml") >= 0 ||
      ct.indexOf("application/x-www-form-urlencoded") >= 0 ||
      ct.indexOf("application/graphql") >= 0 ||
      ct.indexOf("+json") >= 0 ||
      ct.indexOf("+xml") >= 0
    );
  }

  function previewRequestBody(body) {
    if (body == null) {
      return null;
    }
    if (typeof body === "string") {
      return body.slice(0, MAX_BODY_PREVIEW);
    }
    if (body instanceof URLSearchParams) {
      return body.toString().slice(0, MAX_BODY_PREVIEW);
    }
    return "[non-text body]";
  }

  var originalFetch = window.fetch;
  if (typeof originalFetch === "function") {
    window.fetch = function(resource, init) {
      var method = "GET";
      if (init && init.method) {
        method = String(init.method).toUpperCase();
      }
      var rawUrl = "";
      if (typeof resource === "string") {
        rawUrl = resource;
      } else if (resource && resource.url) {
        rawUrl = resource.url;
      } else {
        rawUrl = String(resource || "");
      }

      var requestHeaders = headersToObject(init && init.headers ? init.headers : null);
      var requestBody = previewRequestBody(init && init.body ? init.body : null);
      var startedAt = nowIso();
      var absoluteUrl = toAbsoluteUrl(rawUrl);

      return originalFetch.apply(this, arguments).then(async function(response) {
        var responseHeaders = headersToObject(response.headers);
        var responseBodyPreview = null;
        var responseBodyError = null;
        try {
          var contentType = response.headers.get("content-type") || "";
          if (isTextualContentType(contentType)) {
            responseBodyPreview = (await response.clone().text()).slice(0, MAX_BODY_PREVIEW);
          }
        } catch (err) {
          responseBodyError = String(err);
        }

        emitCapture({
          api: "fetch",
          method: method,
          url: absoluteUrl,
          page: window.location.href,
          started_at: startedAt,
          finished_at: nowIso(),
          request_headers: requestHeaders,
          request_body_preview: requestBody,
          response_status: response.status,
          response_status_text: response.statusText,
          response_ok: response.ok,
          response_headers: responseHeaders,
          response_body_preview: responseBodyPreview,
          response_body_error: responseBodyError
        });

        return response;
      }).catch(function(err) {
        emitCapture({
          api: "fetch",
          method: method,
          url: absoluteUrl,
          page: window.location.href,
          started_at: startedAt,
          finished_at: nowIso(),
          request_headers: requestHeaders,
          request_body_preview: requestBody,
          response_status: null,
          response_status_text: null,
          response_ok: false,
          response_headers: {},
          response_body_preview: null,
          response_body_error: String(err)
        });
        throw err;
      });
    };
  }

  var originalOpen = XMLHttpRequest.prototype.open;
  var originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
  var originalSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function(method, url) {
    this.__collectorMethod = method ? String(method).toUpperCase() : "GET";
    this.__collectorUrl = toAbsoluteUrl(url);
    this.__collectorRequestHeaders = {};
    return originalOpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.setRequestHeader = function(header, value) {
    try {
      if (!this.__collectorRequestHeaders) {
        this.__collectorRequestHeaders = {};
      }
      this.__collectorRequestHeaders[String(header).toLowerCase()] = String(value);
    } catch (_err) {}
    return originalSetRequestHeader.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function() {
    var self = this;
    var startedAt = nowIso();
    var requestBody = previewRequestBody(arguments.length ? arguments[0] : null);

    self.addEventListener("loadend", function() {
      var responseHeaders = {};
      var responseBodyPreview = null;
      var responseBodyError = null;

      try {
        responseHeaders = parseRawResponseHeaders(self.getAllResponseHeaders());
      } catch (errHeaders) {
        responseBodyError = String(errHeaders);
      }

      try {
        var contentType = responseHeaders["content-type"] || "";
        var responseType = String(self.responseType || "");
        if (responseType === "" || responseType === "text") {
          if (isTextualContentType(contentType)) {
            responseBodyPreview = String(self.responseText || "").slice(0, MAX_BODY_PREVIEW);
          }
        } else if (responseType === "json") {
          responseBodyPreview = JSON.stringify(self.response).slice(0, MAX_BODY_PREVIEW);
        }
      } catch (errBody) {
        responseBodyError = String(errBody);
      }

      emitCapture({
        api: "xhr",
        method: self.__collectorMethod || "GET",
        url: self.__collectorUrl || "",
        page: window.location.href,
        started_at: startedAt,
        finished_at: nowIso(),
        request_headers: self.__collectorRequestHeaders || {},
        request_body_preview: requestBody,
        response_status: Number(self.status || 0),
        response_status_text: String(self.statusText || ""),
        response_ok: Number(self.status || 0) >= 200 && Number(self.status || 0) < 300,
        response_headers: responseHeaders,
        response_body_preview: responseBodyPreview,
        response_body_error: responseBodyError
      });
    });

    return originalSend.apply(this, arguments);
  };
})();
'''


def _has_xcb_cursor_library() -> bool:
  return find_library("xcb-cursor") is not None


def has_gui_session() -> bool:
  if not sys.platform.startswith("linux"):
    return True
  return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def print_startup_context() -> None:
  platform = os.environ.get("QT_QPA_PLATFORM", "<auto>")
  display = os.environ.get("DISPLAY", "<unset>")
  wayland = os.environ.get("WAYLAND_DISPLAY", "<unset>")
  xdg_session = os.environ.get("XDG_SESSION_TYPE", "<unset>")
  print("[Desktop XHR Collector] Startup context")
  print(f"  QT_QPA_PLATFORM={platform}")
  print(f"  DISPLAY={display}")
  print(f"  WAYLAND_DISPLAY={wayland}")
  print(f"  XDG_SESSION_TYPE={xdg_session}")


def configure_linux_qt_platform() -> bool:
    if not sys.platform.startswith("linux"):
        return True

    if os.environ.get("QT_QPA_PLATFORM"):
        return True

    has_wayland_session = bool(os.environ.get("WAYLAND_DISPLAY"))
    has_xcb_cursor = _has_xcb_cursor_library()

    # On systems without xcb-cursor, use Wayland if available to avoid xcb plugin abort.
    if not has_xcb_cursor and has_wayland_session:
        os.environ["QT_QPA_PLATFORM"] = "wayland"
        return True

    if not has_xcb_cursor:
        print(
            "Missing Qt XCB dependency: libxcb-cursor.",
            file=sys.stderr,
        )
        print(
            "Install it, then run the app again.",
            file=sys.stderr,
        )
        print(
            "Debian/Ubuntu: sudo apt install libxcb-cursor0",
            file=sys.stderr,
        )
        return False

    return True


@dataclass
class CaptureEvent:
    started_at: str
    finished_at: str
    api: str
    method: str
    url: str
    page: str
    request_headers: Dict[str, str]
    request_body_preview: Optional[str]
    response_status: Optional[int]
    response_status_text: Optional[str]
    response_ok: bool
    response_headers: Dict[str, str]
    response_body_preview: Optional[str]
    response_body_error: Optional[str]


class CapturePage(QWebEnginePage):
    capture_received = Signal(str)

    def javaScriptConsoleMessage(self, level: QWebEnginePage.JavaScriptConsoleMessageLevel, message: str, line_number: int, source_id: str) -> None:  # noqa: N802
        if message.startswith(CAPTURE_PREFIX):
            self.capture_received.emit(message[len(CAPTURE_PREFIX) :])
            return
        super().javaScriptConsoleMessage(level, message, line_number, source_id)


class XhrCollectorWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Desktop XHR Collector")
        self.resize(1400, 850)

        self.events: List[CaptureEvent] = []
        self.table_columns = [
          ("call_type", "Type", True),
          ("status_code", "Status Code", True),
          ("method", "Method", True),
          ("url", "URL", True),
          ("started_at", "Started At", False),
          ("finished_at", "Finished At", False),
          ("page", "Page", False),
          ("status_text", "Status Text", False),
          ("ok", "OK", False),
          ("request_headers", "Request Headers", False),
          ("request_body", "Request Body", False),
          ("response_headers", "Response Headers", False),
          ("response_body", "Response Body", False),
          ("response_error", "Response Error", False),
        ]
        self.url_column_index = next(
            index
            for index, (key, _title, _is_default_visible) in enumerate(self.table_columns)
            if key == "url"
        )

        self.browser = QWebEngineView(self)
        self.page = CapturePage(self.browser)
        self.browser.setPage(self.page)
        self.page.capture_received.connect(self.handle_capture)

        self._inject_capture_script()
        self._build_ui()

        self.browser.setUrl(QUrl("https://www.carrefour.es/"))

    def _inject_capture_script(self) -> None:
        script = QWebEngineScript()
        script.setName("xhr-capture-script")
        script.setSourceCode(INJECT_SCRIPT)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setRunsOnSubFrames(True)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        self.page.scripts().insert(script)

    def _build_ui(self) -> None:
        root = QWidget(self)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(8)

        style = self.style()

        # ---------------------------------------------------------
        # Helper: Linux theme icon + Qt fallback
        # ---------------------------------------------------------

        def theme_icon(name: str, fallback):
            icon = QIcon.fromTheme(name)

            if icon.isNull():
                icon = style.standardIcon(fallback)

            return icon

        # ---------------------------------------------------------
        # Navigation actions
        # ---------------------------------------------------------

        back_action = QAction(
            theme_icon(
                "go-previous",
                QStyle.StandardPixmap.SP_ArrowBack,
            ),
            "Back",
            self,
        )
        back_action.setToolTip("Back")
        back_action.triggered.connect(self.browser.back)

        forward_action = QAction(
            theme_icon(
                "go-next",
                QStyle.StandardPixmap.SP_ArrowForward,
            ),
            "Forward",
            self,
        )
        forward_action.setToolTip("Forward")
        forward_action.triggered.connect(self.browser.forward)

        reload_action = QAction(
            theme_icon(
                "view-refresh",
                QStyle.StandardPixmap.SP_BrowserReload,
            ),
            "Reload",
            self,
        )
        reload_action.setToolTip("Reload")
        reload_action.triggered.connect(self.browser.reload)

        # ---------------------------------------------------------
        # Navigation toolbar
        # ---------------------------------------------------------

        toolbar = self.addToolBar("Navigation")

        toolbar.setMovable(False)

        # IMPORTANT:
        # show icons only, not "Back Forward Reload"
        toolbar.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
        )

        toolbar.setIconSize(QSize(20, 20))

        toolbar.addAction(back_action)
        toolbar.addAction(forward_action)
        toolbar.addAction(reload_action)

        toolbar.addSeparator()

        # ---------------------------------------------------------
        # URL input
        # ---------------------------------------------------------

        self.url_input = QLineEdit(self)
        self.url_input.setPlaceholderText(
            "Enter URL, e.g. https://example.com"
        )

        self.url_input.returnPressed.connect(
            self.navigate_to_input_url
        )

        # Let URL field stretch
        self.url_input.setMinimumWidth(300)

        toolbar.addWidget(self.url_input)

        # ---------------------------------------------------------
        # Go button
        # ---------------------------------------------------------

        go_button = QPushButton(self)

        go_button.setIcon(
            theme_icon(
                "go-jump",
                QStyle.StandardPixmap.SP_ArrowForward,
            )
        )

        go_button.setIconSize(QSize(18, 18))
        go_button.setToolTip("Go")
        go_button.setFixedSize(32, 30)

        go_button.clicked.connect(
            self.navigate_to_input_url
        )

        toolbar.addWidget(go_button)

        # ---------------------------------------------------------
        # Main splitter
        # ---------------------------------------------------------

        splitter = QSplitter(
            Qt.Orientation.Horizontal,
            self,
        )

        splitter.setChildrenCollapsible(False)

        # ---------------------------------------------------------
        # Left side
        # ---------------------------------------------------------

        left_container = QWidget(self)

        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_layout.addWidget(self.browser)

        # ---------------------------------------------------------
        # Right side
        # ---------------------------------------------------------

        right_container = QWidget(self)

        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(8)

        # ---------------------------------------------------------
        # Right header
        # ---------------------------------------------------------

        header_layout = QHBoxLayout()

        panel_title = QLabel(
            "Captured XHR/Fetch URLs",
            self,
        )

        self.count_label = QLabel(
            "Total: 0",
            self,
        )

        header_layout.addWidget(panel_title)
        header_layout.addStretch()
        header_layout.addWidget(self.count_label)

        self.columns_button = QPushButton("Columns", self)
        self.columns_button.setToolTip("Show or hide table columns")
        self.columns_button.clicked.connect(self._show_column_menu)
        header_layout.addWidget(self.columns_button)

        # ---------------------------------------------------------
        # Captured request list
        # ---------------------------------------------------------

        self.captured_table = QTableWidget(self)
        self.captured_table.setColumnCount(len(self.table_columns))
        self.captured_table.setHorizontalHeaderLabels([title for _, title, _ in self.table_columns])
        self.captured_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.captured_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.captured_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.captured_table.setAlternatingRowColors(True)
        self.captured_table.verticalHeader().setVisible(False)
        header = self.captured_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(self.url_column_index, QHeaderView.ResizeMode.Stretch)
        header.setSectionsMovable(True)
        self.captured_table.cellDoubleClicked.connect(self._open_event_details_for_row)

        for column_index, (_key, _title, is_default_visible) in enumerate(self.table_columns):
          self.captured_table.setColumnHidden(column_index, not is_default_visible)

        # ---------------------------------------------------------
        # Bottom buttons
        # ---------------------------------------------------------

        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()

        # Save button
        save_button = QPushButton(self)

        save_button.setIcon(
            theme_icon(
                "document-save",
                QStyle.StandardPixmap.SP_DialogSaveButton,
            )
        )

        save_button.setIconSize(QSize(18, 18))
        save_button.setToolTip("Save captured requests")
        save_button.setFixedSize(34, 34)

        save_button.clicked.connect(
            self.save_events
        )

        # Clear button
        clear_button = QPushButton(self)

        clear_button.setIcon(
            theme_icon(
                "edit-delete",
                QStyle.StandardPixmap.SP_TrashIcon,
            )
        )

        clear_button.setIconSize(QSize(18, 18))
        clear_button.setToolTip("Clear captured requests")
        clear_button.setFixedSize(34, 34)

        clear_button.clicked.connect(
            self.clear_events
        )

        buttons_layout.addWidget(save_button)
        buttons_layout.addWidget(clear_button)

        # ---------------------------------------------------------
        # Assemble right side
        # ---------------------------------------------------------

        right_layout.addLayout(header_layout)

        right_layout.addWidget(
          self.captured_table,
            1,
        )

        right_layout.addLayout(buttons_layout)

        # ---------------------------------------------------------
        # Splitter
        # ---------------------------------------------------------

        splitter.addWidget(left_container)
        splitter.addWidget(right_container)

        splitter.setSizes([
            1000,
            400,
        ])

        # ---------------------------------------------------------
        # Main widget
        # ---------------------------------------------------------

        root_layout.addWidget(
            splitter,
            1,
        )

        self.setCentralWidget(root)

        # Synchronize browser URL
        self.browser.urlChanged.connect(
            self._sync_url_input
        )

    def _sync_url_input(self, url: QUrl) -> None:
        self.url_input.setText(url.toString())

    def _show_column_menu(self) -> None:
      menu = QMenu(self)
      for column_index, (_key, title, _is_default_visible) in enumerate(self.table_columns):
        action = menu.addAction(title)
        action.setCheckable(True)
        action.setChecked(not self.captured_table.isColumnHidden(column_index))
        action.toggled.connect(
          lambda checked, col=column_index: self.captured_table.setColumnHidden(col, not checked)
        )
      menu.exec(self.columns_button.mapToGlobal(self.columns_button.rect().bottomLeft()))

    def _truncate(self, value: Optional[str], max_len: int = 120) -> str:
      if value is None:
        return ""
      if len(value) <= max_len:
        return value
      return value[: max_len - 3] + "..."

    def _column_value(self, event: CaptureEvent, key: str) -> str:
      if key == "call_type":
        return event.api.upper()
      if key == "url":
        return event.url
      if key == "status_code":
        return str(event.response_status) if event.response_status is not None else "ERR"
      if key == "method":
        return event.method
      if key == "started_at":
        return event.started_at
      if key == "finished_at":
        return event.finished_at
      if key == "page":
        return event.page
      if key == "status_text":
        return event.response_status_text or ""
      if key == "ok":
        return "Yes" if event.response_ok else "No"
      if key == "request_headers":
        return self._truncate(json.dumps(event.request_headers, ensure_ascii=False))
      if key == "request_body":
        return self._truncate(event.request_body_preview)
      if key == "response_headers":
        return self._truncate(json.dumps(event.response_headers, ensure_ascii=False))
      if key == "response_body":
        return self._truncate(event.response_body_preview)
      if key == "response_error":
        return self._truncate(event.response_body_error)
      return ""

    def _open_event_details_for_row(self, row: int, _column: int) -> None:
      if row < 0 or row >= self.captured_table.rowCount():
        return

      cell_item = self.captured_table.item(row, 0)
      if cell_item is None:
        return

      event = cell_item.data(Qt.ItemDataRole.UserRole)
      if event is None:
        return

      dialog = QDialog(self)
      dialog.setWindowTitle("Captured Call Details")
      dialog.resize(860, 620)

      dialog_layout = QVBoxLayout(dialog)

      details_editor = QTextEdit(dialog)
      details_editor.setReadOnly(True)
      details_editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
      details_editor.setText(json.dumps(asdict(event), ensure_ascii=False, indent=2))

      button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
      button_box.rejected.connect(dialog.reject)

      dialog_layout.addWidget(details_editor)
      dialog_layout.addWidget(button_box)

      dialog.exec()

    def navigate_to_input_url(self) -> None:
        raw = self.url_input.text().strip()
        if not raw:
            return

        if not raw.startswith(("http://", "https://")):
            raw = "https://" + raw

        self.browser.setUrl(QUrl(raw))

    def handle_capture(self, payload_json: str) -> None:
        try:
            payload: Dict[str, Any] = json.loads(payload_json)
        except json.JSONDecodeError:
            return

        event = CaptureEvent(
          started_at=str(payload.get("started_at", "")),
          finished_at=str(payload.get("finished_at", "")),
            api=str(payload.get("api", "")),
            method=str(payload.get("method", "GET")),
            url=str(payload.get("url", "")),
            page=str(payload.get("page", "")),
          request_headers=dict(payload.get("request_headers", {}) or {}),
          request_body_preview=(
            str(payload.get("request_body_preview"))
            if payload.get("request_body_preview") is not None
            else None
          ),
          response_status=(
            int(payload.get("response_status"))
            if payload.get("response_status") is not None
            else None
          ),
          response_status_text=(
            str(payload.get("response_status_text"))
            if payload.get("response_status_text") is not None
            else None
          ),
          response_ok=bool(payload.get("response_ok", False)),
          response_headers=dict(payload.get("response_headers", {}) or {}),
          response_body_preview=(
            str(payload.get("response_body_preview"))
            if payload.get("response_body_preview") is not None
            else None
          ),
          response_body_error=(
            str(payload.get("response_body_error"))
            if payload.get("response_body_error") is not None
            else None
          ),
        )

        if not event.url:
            return

        self.events.append(event)
        row = self.captured_table.rowCount()
        self.captured_table.insertRow(row)

        for column_index, (key, _title, _is_default_visible) in enumerate(self.table_columns):
          item = QTableWidgetItem(self._column_value(event, key))
          item.setData(Qt.ItemDataRole.UserRole, event)
          self.captured_table.setItem(row, column_index, item)

        self.count_label.setText(f"Total: {len(self.events)}")

    def save_events(self) -> None:
        if not self.events:
            QMessageBox.information(self, "Save", "There is nothing to save yet.")
            return

        default_name = f"captured_urls_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        default_path = str(Path.cwd() / default_name)

        target, _ = QFileDialog.getSaveFileName(
            self,
            "Save captured URLs",
            default_path,
            "JSON Files (*.json);;Text Files (*.txt)",
        )

        if not target:
            return

        target_path = Path(target)
        try:
            if target_path.suffix.lower() == ".txt":
                with target_path.open("w", encoding="utf-8") as fp:
                    for ev in self.events:
                      fp.write(
                        f"{ev.started_at}\t{ev.finished_at}\t{ev.api}\t{ev.method}\t"
                        f"{ev.response_status}\t{ev.url}\n"
                      )
            else:
                serializable = [
                    {
                      "started_at": ev.started_at,
                      "finished_at": ev.finished_at,
                        "api": ev.api,
                        "method": ev.method,
                        "url": ev.url,
                        "page": ev.page,
                      "request_headers": ev.request_headers,
                      "request_body_preview": ev.request_body_preview,
                      "response_status": ev.response_status,
                      "response_status_text": ev.response_status_text,
                      "response_ok": ev.response_ok,
                      "response_headers": ev.response_headers,
                      "response_body_preview": ev.response_body_preview,
                      "response_body_error": ev.response_body_error,
                    }
                    for ev in self.events
                ]
                with target_path.open("w", encoding="utf-8") as fp:
                    json.dump(serializable, fp, ensure_ascii=False, indent=2)

            QMessageBox.information(self, "Saved", f"Saved {len(self.events)} items to:\n{target_path}")
        except Exception as ex:
            QMessageBox.critical(self, "Save error", f"Failed to save file:\n{ex}")

    def clear_events(self) -> None:
        self.events.clear()
        self.captured_table.setRowCount(0)
        self.count_label.setText("Total: 0")


def main() -> int:
    if not configure_linux_qt_platform():
        return 2

    print_startup_context()

    if not has_gui_session():
        print(
            "No active GUI session found (DISPLAY/WAYLAND_DISPLAY are unset).",
            file=sys.stderr,
        )
        print(
            "Run this from your desktop terminal session, not a headless shell.",
            file=sys.stderr,
        )
        return 3

    try:
        app = QApplication(sys.argv)
        window = XhrCollectorWindow()
        window.show()
        print("[Desktop XHR Collector] UI launched.")
        exit_code = app.exec()
        print(f"[Desktop XHR Collector] UI closed with exit code {exit_code}.")
        return exit_code
    except Exception as ex:
        print(f"Unhandled startup error: {ex}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
