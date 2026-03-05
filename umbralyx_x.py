import os
import sys
import subprocess
from dataclasses import dataclass

import yt_dlp

from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QMessageBox,
    QProgressBar,
    QTabWidget,
)

# ----------------------------
# Utilidad para PyInstaller
# ----------------------------
def resource_path(relative_path: str) -> str:
    """
    Devuelve una ruta absoluta válida tanto en desarrollo como en ejecutable PyInstaller.
    - En PyInstaller: sys._MEIPASS apunta al directorio temporal/unpacked.
    - En dev: usa el directorio del .py.
    """
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


@dataclass
class DownloadRequest:
    url: str
    output_dir: str
    mode: str  # "video" or "audio"


class DownloaderWorker(QObject):
    progress_changed = Signal(int)          # 0..100
    status_changed = Signal(str)            # text updates
    finished_ok = Signal(str)               # message
    failed = Signal(str)                    # error message

    def __init__(self, request: DownloadRequest):
        super().__init__()
        self.request = request
        self._cancelled = False

    @Slot()
    def run(self):
        try:
            req = self.request

            def hook(d: dict):
                if self._cancelled:
                    raise RuntimeError("Descarga cancelada por el usuario.")

                status = d.get("status")
                if status == "downloading":
                    percent = self._extract_percent(d)
                    if percent is not None:
                        self.progress_changed.emit(percent)

                    # Mensaje de estado (suave)
                    speed = d.get("speed")
                    eta = d.get("eta")
                    parts = []
                    if speed:
                        parts.append(f"Velocidad: {self._fmt_speed(speed)}")
                    if eta is not None:
                        parts.append(f"ETA: {eta}s")
                    if parts:
                        self.status_changed.emit(" | ".join(parts))

                elif status == "finished":
                    self.progress_changed.emit(100)
                    self.status_changed.emit("Descarga finalizada. Procesando...")

            outtmpl = os.path.join(req.output_dir, "%(title)s.%(ext)s")

            if req.mode == "video":
                ydl_opts = {
                    "format": "bestvideo+bestaudio/best",
                    "outtmpl": outtmpl,
                    "merge_output_format": "mp4",
                    "noplaylist": True,
                    "quiet": True,
                    "progress_hooks": [hook],
                }
            else:
                # audio -> mp3 via postprocessor de yt-dlp (usa ffmpeg)
                ydl_opts = {
                    "format": "bestaudio/best",
                    "outtmpl": outtmpl,
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                    "noplaylist": True,
                    "quiet": True,
                    "progress_hooks": [hook],
                }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(req.url, download=True)

            tipo = "Vídeo + Audio" if req.mode == "video" else "Audio (MP3)"
            self.finished_ok.emit(f"{tipo} descargado correctamente en:\n{req.output_dir}")

        except Exception as e:
            self.failed.emit(str(e))

    def cancel(self):
        self._cancelled = True

    @staticmethod
    def _extract_percent(d: dict) -> int | None:
        # Preferir bytes si están disponibles
        downloaded = d.get("downloaded_bytes")
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if downloaded is not None and total:
            if total > 0:
                p = int((downloaded / total) * 100)
                return max(0, min(100, p))

        # Fallback a _percent_str si existe
        pstr = d.get("_percent_str")
        if isinstance(pstr, str):
            pstr = pstr.strip().replace("%", "")
            try:
                p = int(float(pstr))
                return max(0, min(100, p))
            except ValueError:
                return None
        return None

    @staticmethod
    def _fmt_speed(speed_bps: float) -> str:
        units = ["B/s", "KB/s", "MB/s", "GB/s"]
        s = float(speed_bps)
        i = 0
        while s >= 1024 and i < len(units) - 1:
            s /= 1024
            i += 1
        return f"{s:.2f} {units[i]}"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Descargador de YouTube (yt-dlp) - PySide6")
        self.setMinimumSize(700, 450)

        self.output_dir = ""
        self.thread: QThread | None = None
        self.worker: DownloaderWorker | None = None

        # Tabs
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.tab_folder = QWidget()
        self.tab_url = QWidget()
        self.tabs.addTab(self.tab_folder, "Seleccionar Carpeta")
        self.tabs.addTab(self.tab_url, "Pegar URL")

        self._build_folder_tab()
        self._build_url_tab()

    def _build_folder_tab(self):
        layout = QVBoxLayout(self.tab_folder)

        label = QLabel("Selecciona una carpeta de destino:")
        layout.addWidget(label)

        row = QHBoxLayout()
        self.btn_browse = QPushButton("Explorar")
        self.btn_browse.clicked.connect(self.choose_folder)
        row.addWidget(self.btn_browse)

        self.lbl_folder = QLabel("(ninguna seleccionada)")
        self.lbl_folder.setWordWrap(True)
        row.addWidget(self.lbl_folder, 1)

        layout.addLayout(row)
        layout.addStretch(1)

    def _build_url_tab(self):
        layout = QVBoxLayout(self.tab_url)

        label = QLabel("Introduce la URL del vídeo de YouTube:")
        layout.addWidget(label)

        self.input_url = QLineEdit()
        self.input_url.setPlaceholderText("https://www.youtube.com/watch?v=...")
        layout.addWidget(self.input_url)

        btn_row = QHBoxLayout()

        self.btn_download = QPushButton("Descargar")
        self.btn_download.clicked.connect(self.start_download)
        btn_row.addWidget(self.btn_download)

        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_download)
        btn_row.addWidget(self.btn_cancel)

        layout.addLayout(btn_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        layout.addWidget(self.lbl_status)

        layout.addStretch(1)

    @Slot()
    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Selecciona carpeta de destino")
        if folder:
            self.output_dir = folder
            self.lbl_folder.setText(folder)
            QMessageBox.information(self, "Ruta seleccionada", f"Los vídeos se guardarán en:\n{folder}")

    @Slot()
    def start_download(self):
        url = self.input_url.text().strip()
        if not url:
            QMessageBox.warning(self, "URL vacía", "Introduce una URL de YouTube válida.")
            return
        if not self.output_dir:
            QMessageBox.warning(self, "Ruta no seleccionada", "Selecciona una carpeta de destino primero.")
            self.tabs.setCurrentWidget(self.tab_folder)
            return

        res = QMessageBox.question(
            self,
            "Selecciona tipo de descarga",
            "¿Qué deseas descargar?\n\n"
            "Sí  -> Vídeo + Audio (MP4)\n"
            "No  -> Solo Audio (MP3)",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        mode = "video" if res == QMessageBox.Yes else "audio"

        self.progress.setValue(0)
        self.lbl_status.setText("Iniciando descarga...")
        self._set_busy(True)

        req = DownloadRequest(url=url, output_dir=self.output_dir, mode=mode)

        self.thread = QThread()
        self.worker = DownloaderWorker(req)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.progress_changed.connect(self.progress.setValue)
        self.worker.status_changed.connect(self.lbl_status.setText)
        self.worker.finished_ok.connect(self.on_finished_ok)
        self.worker.failed.connect(self.on_failed)

        self.worker.finished_ok.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self._cleanup_thread)

        self.thread.start()

    @Slot()
    def cancel_download(self):
        if self.worker:
            self.worker.cancel()
            self.lbl_status.setText("Cancelando...")

    @Slot(str)
    def on_finished_ok(self, msg: str):
        self._set_busy(False)
        QMessageBox.information(self, "Descarga completa", msg)

    @Slot(str)
    def on_failed(self, err: str):
        self._set_busy(False)
        QMessageBox.critical(self, "Error de descarga", f"Ha ocurrido un error:\n{err}")

    @Slot()
    def _cleanup_thread(self):
        if self.worker:
            self.worker.deleteLater()
            self.worker = None
        if self.thread:
            self.thread.deleteLater()
            self.thread = None
        self.lbl_status.setText(self.lbl_status.text() or "")

    def _set_busy(self, busy: bool):
        self.btn_download.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        self.btn_browse.setEnabled(not busy)


def main():
    app = QApplication(sys.argv)

    # Cargar el archivo de estilo QSS (compatible con PyInstaller)
    try:
        with open(resource_path("estilo_oscuro.qss"), "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    except FileNotFoundError:
        # Si por lo que sea no está el QSS, no rompemos la app:
        # arrancará con estilo por defecto.
        pass

    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()