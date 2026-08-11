import os
import sys
import traceback
from pathlib import Path
from typing import List, Set

from PyQt5.QtCore import (
    Qt,
    QSize,
    QSettings,
    QThread,
    QUrl,
    pyqtSignal,
)
from PyQt5.QtGui import (
    QIcon,
    QPixmap,
    QImageReader,
    QDesktopServices,
)
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QFileDialog,
    QMessageBox,
    QLabel,
    QPushButton,
    QComboBox,
    QProgressBar,
    QListWidget,
    QListWidgetItem,
    QHBoxLayout,
    QVBoxLayout,
    QFrame,
    QAbstractItemView,
    QCheckBox,
    QPlainTextEdit,
)

from PIL import Image
from rembg import new_session, remove


APP_NAME = "Rembg Studio"
ORGANIZATION_NAME = "LocalApps"

SUPPORTED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".webp",
    ".tif",
    ".tiff",
}

# Большинство распространённых моделей rembg.
# При первом использовании модель автоматически загружается.
MODELS = [
    ("u2net", "U²-Net — универсальная"),
    ("u2netp", "U²-Net P — быстрая и лёгкая"),
    ("u2net_human_seg", "U²-Net Human — люди"),
    ("u2net_cloth_seg", "U²-Net Cloth — одежда"),
    ("isnet-general-use", "IS-Net — универсальная"),
    ("isnet-anime", "IS-Net Anime — аниме"),
    ("silueta", "Silueta — компактная"),
    ("birefnet-general", "BiRefNet General — высокая точность"),
    ("birefnet-general-lite", "BiRefNet Lite — быстрее"),
    ("birefnet-portrait", "BiRefNet Portrait — портреты"),
    ("birefnet-dis", "BiRefNet DIS — сложные объекты"),
    ("birefnet-hrsod", "BiRefNet HRSOD — высокое разрешение"),
    ("sam", "SAM — сегментация"),
]


def unique_output_path(folder: str, source_path: str) -> str:
    """
    Создаёт уникальный путь сохранения, чтобы существующий файл
    случайно не был перезаписан.
    """
    source = Path(source_path)
    base_name = f"{source.stem}_no_bg"
    output = Path(folder) / f"{base_name}.png"

    counter = 2
    while output.exists():
        output = Path(folder) / f"{base_name}_{counter}.png"
        counter += 1

    return str(output)


class DropListWidget(QListWidget):
    """
    Список с поддержкой перетаскивания файлов и папок.
    """

    files_dropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setWrapping(True)
        self.setSpacing(12)
        self.setIconSize(QSize(145, 110))
        self.setGridSize(QSize(175, 165))
        self.setUniformItemSizes(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = []

        for url in event.mimeData().urls():
            local_path = url.toLocalFile()
            if local_path:
                paths.append(local_path)

        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class ProcessingWorker(QThread):
    """
    Обрабатывает изображения в отдельном потоке,
    чтобы интерфейс не зависал.
    """

    progress_changed = pyqtSignal(int, int)
    file_started = pyqtSignal(str)
    file_finished = pyqtSignal(str, str)
    log_message = pyqtSignal(str)
    error_occurred = pyqtSignal(str, str)
    processing_finished = pyqtSignal(int, int, bool)

    def __init__(
        self,
        files: List[str],
        output_folder: str,
        model_name: str,
        overwrite: bool,
        parent=None,
    ):
        super().__init__(parent)

        self.files = files
        self.output_folder = output_folder
        self.model_name = model_name
        self.overwrite = overwrite
        self.cancel_requested = False

    def cancel(self):
        self.cancel_requested = True

    def build_output_path(self, source_path: str) -> str:
        source = Path(source_path)
        default_path = Path(self.output_folder) / f"{source.stem}_no_bg.png"

        if self.overwrite:
            return str(default_path)

        return unique_output_path(self.output_folder, source_path)

    def run(self):
        success_count = 0
        error_count = 0
        cancelled = False

        try:
            self.log_message.emit(
                f"Загрузка модели «{self.model_name}»..."
            )

            # Сессия создаётся один раз и используется для всех файлов.
            session = new_session(self.model_name)

            self.log_message.emit("Модель загружена.")
        except Exception as error:
            error_count = len(self.files)
            self.error_occurred.emit(
                "Не удалось загрузить модель",
                f"{error}\n\n{traceback.format_exc()}",
            )
            self.processing_finished.emit(
                success_count,
                error_count,
                False,
            )
            return

        total = len(self.files)

        for index, file_path in enumerate(self.files, start=1):
            if self.cancel_requested:
                cancelled = True
                self.log_message.emit("Обработка остановлена пользователем.")
                break

            self.file_started.emit(file_path)
            self.progress_changed.emit(index - 1, total)

            try:
                file_name = os.path.basename(file_path)
                self.log_message.emit(
                    f"[{index}/{total}] Обработка: {file_name}"
                )

                # Загружаем изображение через Pillow.
                # convert("RGBA") обеспечивает корректную прозрачность.
                with Image.open(file_path) as image:
                    image = image.convert("RGBA")

                    result = remove(
                        image,
                        session=session,
                    )

                    output_path = self.build_output_path(file_path)
                    result.save(output_path, format="PNG")

                success_count += 1

                self.file_finished.emit(file_path, output_path)
                self.log_message.emit(
                    f"Сохранено: {output_path}"
                )

            except Exception as error:
                error_count += 1

                self.error_occurred.emit(
                    os.path.basename(file_path),
                    str(error),
                )

                self.log_message.emit(
                    f"Ошибка: {os.path.basename(file_path)} — {error}"
                )

            self.progress_changed.emit(index, total)

        self.processing_finished.emit(
            success_count,
            error_count,
            cancelled,
        )


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.settings = QSettings(
            ORGANIZATION_NAME,
            APP_NAME,
        )

        self.image_files: List[str] = []
        self.image_file_set: Set[str] = set()
        self.worker = None

        self.setWindowTitle("Rembg Studio — удаление фона")
        self.setMinimumSize(980, 700)
        self.resize(1120, 790)
        self.setAcceptDrops(True)

        self.create_interface()
        self.apply_styles()
        self.load_settings()
        self.update_interface_state()

    def create_interface(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(24, 22, 24, 22)
        root_layout.setSpacing(16)

        # Заголовок
        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)

        title_label = QLabel("Rembg Studio")
        title_label.setObjectName("titleLabel")

        subtitle_label = QLabel(
            "Перетащите изображения в окно или выберите их вручную"
        )
        subtitle_label.setObjectName("subtitleLabel")

        title_box.addWidget(title_label)
        title_box.addWidget(subtitle_label)

        self.telegram_button = QPushButton("Телеграмм")
        self.telegram_button.setObjectName("telegramButton")
        self.telegram_button.setCursor(Qt.PointingHandCursor)
        self.telegram_button.setToolTip("https://t.me/f0bass")
        self.telegram_button.clicked.connect(self.open_telegram)

        header_layout.addLayout(title_box)
        header_layout.addStretch()
        header_layout.addWidget(self.telegram_button, 0, Qt.AlignTop)

        root_layout.addLayout(header_layout)

        # Верхняя панель
        toolbar_frame = QFrame()
        toolbar_frame.setObjectName("panel")

        toolbar_layout = QHBoxLayout(toolbar_frame)
        toolbar_layout.setContentsMargins(16, 14, 16, 14)
        toolbar_layout.setSpacing(10)

        self.add_button = QPushButton("＋ Добавить изображения")
        self.add_button.setObjectName("primaryButton")
        self.add_button.clicked.connect(self.select_images)

        self.add_folder_button = QPushButton("Добавить папку")
        self.add_folder_button.clicked.connect(self.select_input_folder)

        self.remove_button = QPushButton("Удалить выбранные")
        self.remove_button.clicked.connect(self.remove_selected)

        self.clear_button = QPushButton("Очистить")
        self.clear_button.clicked.connect(self.clear_files)

        toolbar_layout.addWidget(self.add_button)
        toolbar_layout.addWidget(self.add_folder_button)
        toolbar_layout.addStretch()
        toolbar_layout.addWidget(self.remove_button)
        toolbar_layout.addWidget(self.clear_button)

        root_layout.addWidget(toolbar_frame)

        # Основная область
        content_layout = QHBoxLayout()
        content_layout.setSpacing(16)

        # Список изображений
        images_frame = QFrame()
        images_frame.setObjectName("panel")

        images_layout = QVBoxLayout(images_frame)
        images_layout.setContentsMargins(14, 14, 14, 14)
        images_layout.setSpacing(10)

        list_header_layout = QHBoxLayout()

        list_title = QLabel("Изображения")
        list_title.setObjectName("sectionTitle")

        self.count_label = QLabel("0 файлов")
        self.count_label.setObjectName("mutedLabel")

        list_header_layout.addWidget(list_title)
        list_header_layout.addStretch()
        list_header_layout.addWidget(self.count_label)

        images_layout.addLayout(list_header_layout)

        self.image_list = DropListWidget()
        self.image_list.setObjectName("imageList")
        self.image_list.files_dropped.connect(self.add_paths)
        self.image_list.itemSelectionChanged.connect(
            self.update_interface_state
        )

        self.empty_label = QLabel(
            "Перетащите сюда изображения\n\n"
            "PNG · JPG · JPEG · WEBP · BMP · TIFF"
        )
        self.empty_label.setObjectName("emptyLabel")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setAttribute(
            Qt.WA_TransparentForMouseEvents
        )

        # Контейнер нужен, чтобы над QListWidget показывать подсказку.
        list_container = QFrame()
        list_container.setObjectName("listContainer")

        list_container_layout = QVBoxLayout(list_container)
        list_container_layout.setContentsMargins(0, 0, 0, 0)
        list_container_layout.addWidget(self.image_list)

        self.empty_label.setParent(list_container)
        self.empty_label.raise_()

        images_layout.addWidget(list_container, 1)

        # Правая панель
        settings_frame = QFrame()
        settings_frame.setObjectName("panel")
        settings_frame.setFixedWidth(330)

        settings_layout = QVBoxLayout(settings_frame)
        settings_layout.setContentsMargins(18, 18, 18, 18)
        settings_layout.setSpacing(12)

        settings_title = QLabel("Настройки")
        settings_title.setObjectName("sectionTitle")
        settings_layout.addWidget(settings_title)

        model_label = QLabel("Модель удаления фона")
        model_label.setObjectName("fieldLabel")
        settings_layout.addWidget(model_label)

        self.model_combo = QComboBox()

        for model_id, model_description in MODELS:
            self.model_combo.addItem(
                model_description,
                model_id,
            )

        self.model_combo.currentIndexChanged.connect(
            self.save_settings
        )
        settings_layout.addWidget(self.model_combo)

        model_note = QLabel(
            "При первом выборе модель будет автоматически "
            "загружена. Это может занять некоторое время."
        )
        model_note.setObjectName("helpLabel")
        model_note.setWordWrap(True)
        settings_layout.addWidget(model_note)

        output_label = QLabel("Папка сохранения")
        output_label.setObjectName("fieldLabel")
        settings_layout.addSpacing(6)
        settings_layout.addWidget(output_label)

        self.output_path_label = QLabel("Папка не выбрана")
        self.output_path_label.setObjectName("pathLabel")
        self.output_path_label.setWordWrap(True)
        self.output_path_label.setMinimumHeight(58)
        settings_layout.addWidget(self.output_path_label)

        self.output_button = QPushButton("Выбрать папку")
        self.output_button.clicked.connect(self.select_output_folder)
        settings_layout.addWidget(self.output_button)

        self.open_output_button = QPushButton("Открыть папку")
        self.open_output_button.clicked.connect(
            self.open_output_folder
        )
        settings_layout.addWidget(self.open_output_button)

        self.overwrite_checkbox = QCheckBox(
            "Перезаписывать существующие файлы"
        )
        self.overwrite_checkbox.stateChanged.connect(
            self.save_settings
        )
        settings_layout.addWidget(self.overwrite_checkbox)

        settings_layout.addStretch()

        self.start_button = QPushButton("Удалить фон")
        self.start_button.setObjectName("successButton")
        self.start_button.setMinimumHeight(48)
        self.start_button.clicked.connect(self.start_processing)
        settings_layout.addWidget(self.start_button)

        self.stop_button = QPushButton("Остановить")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setMinimumHeight(44)
        self.stop_button.clicked.connect(self.stop_processing)
        self.stop_button.setVisible(False)
        settings_layout.addWidget(self.stop_button)

        content_layout.addWidget(images_frame, 1)
        content_layout.addWidget(settings_frame)

        root_layout.addLayout(content_layout, 1)

        # Прогресс
        progress_frame = QFrame()
        progress_frame.setObjectName("panel")

        progress_layout = QVBoxLayout(progress_frame)
        progress_layout.setContentsMargins(16, 14, 16, 14)
        progress_layout.setSpacing(8)

        status_layout = QHBoxLayout()

        self.status_label = QLabel("Готово к работе")
        self.status_label.setObjectName("statusLabel")

        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("percentLabel")

        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        status_layout.addWidget(self.percent_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(10)

        progress_layout.addLayout(status_layout)
        progress_layout.addWidget(self.progress_bar)

        root_layout.addWidget(progress_frame)

        # Журнал
        self.log_widget = QPlainTextEdit()
        self.log_widget.setObjectName("logWidget")
        self.log_widget.setReadOnly(True)
        self.log_widget.setMaximumHeight(130)
        self.log_widget.setPlaceholderText(
            "Здесь появится журнал обработки..."
        )

        root_layout.addWidget(self.log_widget)

    def apply_styles(self):
        self.setStyleSheet(
            """
            QWidget {
                background-color: #111318;
                color: #e8eaf0;
                font-family: "Segoe UI";
                font-size: 10pt;
            }

            QMainWindow {
                background-color: #111318;
            }

            QLabel#titleLabel {
                font-size: 24pt;
                font-weight: 700;
                color: #ffffff;
            }

            QLabel#subtitleLabel {
                color: #9298a8;
                font-size: 10.5pt;
                margin-bottom: 4px;
            }

            QLabel#sectionTitle {
                font-size: 12pt;
                font-weight: 600;
                color: #ffffff;
            }

            QLabel#fieldLabel {
                color: #bec3cf;
                font-weight: 600;
                margin-top: 4px;
            }

            QLabel#mutedLabel,
            QLabel#helpLabel {
                color: #858b9b;
            }

            QLabel#helpLabel {
                font-size: 9pt;
                line-height: 1.3;
            }

            QLabel#pathLabel {
                background-color: #181b22;
                border: 1px solid #292e39;
                border-radius: 8px;
                padding: 10px;
                color: #bbc0cc;
            }

            QLabel#emptyLabel {
                color: #656c7c;
                font-size: 12pt;
                background: transparent;
            }

            QLabel#statusLabel {
                color: #cdd1dc;
            }

            QLabel#percentLabel {
                color: #8f96a7;
                font-weight: 600;
            }

            QFrame#panel {
                background-color: #171a20;
                border: 1px solid #252a34;
                border-radius: 12px;
            }

            QFrame#listContainer {
                background-color: transparent;
                border: none;
            }

            QPushButton {
                background-color: #242832;
                color: #e8eaf0;
                border: 1px solid #323744;
                border-radius: 8px;
                padding: 9px 14px;
                font-weight: 600;
            }

            QPushButton:hover {
                background-color: #2d323e;
                border-color: #424957;
            }

            QPushButton:pressed {
                background-color: #20242c;
            }

            QPushButton:disabled {
                background-color: #1c1f26;
                color: #5f6572;
                border-color: #252932;
            }

            QPushButton#primaryButton {
                background-color: #3867e8;
                border-color: #3867e8;
                color: white;
            }

            QPushButton#primaryButton:hover {
                background-color: #4775ec;
                border-color: #4775ec;
            }

            QPushButton#successButton {
                background-color: #28a66a;
                border-color: #28a66a;
                color: white;
                font-size: 11pt;
            }

            QPushButton#successButton:hover {
                background-color: #31b978;
                border-color: #31b978;
            }

            QPushButton#dangerButton {
                background-color: #b7474f;
                border-color: #b7474f;
                color: white;
            }

            QPushButton#dangerButton:hover {
                background-color: #c5545c;
            }

            QPushButton#telegramButton {
                background-color: #2AABEE;
                border-color: #2AABEE;
                color: white;
                font-weight: 700;
                padding: 9px 18px;
            }

            QPushButton#telegramButton:hover {
                background-color: #38b8f5;
                border-color: #38b8f5;
            }

            QPushButton#telegramButton:pressed {
                background-color: #1f9ad9;
            }

            QComboBox {
                background-color: #20242c;
                border: 1px solid #303541;
                border-radius: 8px;
                padding: 9px 12px;
                min-height: 20px;
            }

            QComboBox:hover {
                border-color: #465064;
            }

            QComboBox::drop-down {
                border: none;
                width: 28px;
            }

            QComboBox QAbstractItemView {
                background-color: #20242c;
                border: 1px solid #363c49;
                selection-background-color: #3867e8;
                outline: none;
            }

            QCheckBox {
                spacing: 8px;
                color: #bcc1cc;
                padding-top: 6px;
            }

            QCheckBox::indicator {
                width: 17px;
                height: 17px;
                border: 1px solid #444b5a;
                border-radius: 4px;
                background-color: #20242c;
            }

            QCheckBox::indicator:checked {
                background-color: #3867e8;
                border-color: #3867e8;
            }

            QListWidget#imageList {
                background-color: #13161b;
                border: 1px dashed #303642;
                border-radius: 10px;
                padding: 10px;
                outline: none;
            }

            QListWidget#imageList::item {
                background-color: #1d2027;
                border: 1px solid #292e38;
                border-radius: 9px;
                padding: 7px;
                color: #d8dbe4;
            }

            QListWidget#imageList::item:hover {
                border-color: #46536d;
                background-color: #222630;
            }

            QListWidget#imageList::item:selected {
                border: 2px solid #4775ec;
                background-color: #242c3f;
            }

            QProgressBar {
                background-color: #252932;
                border: none;
                border-radius: 5px;
            }

            QProgressBar::chunk {
                background-color: #4775ec;
                border-radius: 5px;
            }

            QPlainTextEdit#logWidget {
                background-color: #15181e;
                border: 1px solid #252a34;
                border-radius: 10px;
                padding: 9px;
                color: #aeb4c1;
                font-family: Consolas;
                font-size: 9pt;
                selection-background-color: #3867e8;
            }

            QScrollBar:vertical {
                background: #171a20;
                width: 10px;
                margin: 2px;
            }

            QScrollBar::handle:vertical {
                background: #363c48;
                min-height: 28px;
                border-radius: 5px;
            }

            QScrollBar::handle:vertical:hover {
                background: #454c5b;
            }

            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
            }
            """
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_empty_label()

    def position_empty_label(self):
        if not hasattr(self, "empty_label"):
            return

        parent = self.empty_label.parentWidget()

        if parent:
            self.empty_label.setGeometry(parent.rect())
            self.empty_label.raise_()

    def load_settings(self):
        output_folder = self.settings.value(
            "output_folder",
            "",
            type=str,
        )

        model_name = self.settings.value(
            "model",
            "u2net",
            type=str,
        )

        overwrite = self.settings.value(
            "overwrite",
            False,
            type=bool,
        )

        self.output_folder = output_folder
        self.overwrite_checkbox.setChecked(overwrite)

        model_index = self.model_combo.findData(model_name)

        if model_index >= 0:
            self.model_combo.setCurrentIndex(model_index)

        self.update_output_label()

    def save_settings(self):
        self.settings.setValue(
            "output_folder",
            getattr(self, "output_folder", ""),
        )

        self.settings.setValue(
            "model",
            self.model_combo.currentData(),
        )

        self.settings.setValue(
            "overwrite",
            self.overwrite_checkbox.isChecked(),
        )

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            answer = QMessageBox.question(
                self,
                "Закрыть программу?",
                "Сейчас выполняется обработка изображений. "
                "Остановить её и закрыть программу?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if answer != QMessageBox.Yes:
                event.ignore()
                return

            self.worker.cancel()
            self.worker.wait(5000)

        self.save_settings()
        event.accept()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.toLocalFile()
        ]

        self.add_paths(paths)
        event.acceptProposedAction()

    def select_images(self):
        initial_folder = self.settings.value(
            "input_folder",
            str(Path.home()),
            type=str,
        )

        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите изображения",
            initial_folder,
            (
                "Изображения "
                "(*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff)"
            ),
        )

        if files:
            self.settings.setValue(
                "input_folder",
                str(Path(files[0]).parent),
            )
            self.add_paths(files)

    def select_input_folder(self):
        initial_folder = self.settings.value(
            "input_folder",
            str(Path.home()),
            type=str,
        )

        folder = QFileDialog.getExistingDirectory(
            self,
            "Выберите папку с изображениями",
            initial_folder,
        )

        if folder:
            self.settings.setValue("input_folder", folder)
            self.add_paths([folder])

    def select_output_folder(self):
        initial_folder = (
            self.output_folder
            if getattr(self, "output_folder", "")
            else str(Path.home())
        )

        folder = QFileDialog.getExistingDirectory(
            self,
            "Выберите папку сохранения",
            initial_folder,
        )

        if folder:
            self.output_folder = folder
            self.save_settings()
            self.update_output_label()
            self.update_interface_state()

    def open_output_folder(self):
        if not self.output_folder:
            return

        folder = Path(self.output_folder)

        if not folder.exists():
            QMessageBox.warning(
                self,
                "Папка не найдена",
                "Выбранная папка больше не существует.",
            )
            return

        QDesktopServices.openUrl(
            QUrl(folder.as_uri())
        )

    def open_telegram(self):
        QDesktopServices.openUrl(
            QUrl("https://t.me/f0bass")
        )

    def collect_images_from_path(self, path: str) -> List[str]:
        found_files = []
        path_object = Path(path)

        if path_object.is_file():
            if path_object.suffix.lower() in SUPPORTED_EXTENSIONS:
                found_files.append(str(path_object.resolve()))

        elif path_object.is_dir():
            try:
                for child in sorted(path_object.iterdir()):
                    if (
                        child.is_file()
                        and child.suffix.lower() in SUPPORTED_EXTENSIONS
                    ):
                        found_files.append(str(child.resolve()))
            except PermissionError:
                self.append_log(
                    f"Нет доступа к папке: {path_object}"
                )

        return found_files

    def add_paths(self, paths: List[str]):
        added_count = 0

        for input_path in paths:
            for image_path in self.collect_images_from_path(input_path):
                normalized_path = os.path.normcase(
                    os.path.abspath(image_path)
                )

                if normalized_path in self.image_file_set:
                    continue

                self.image_file_set.add(normalized_path)
                self.image_files.append(image_path)
                self.add_thumbnail_item(image_path)
                added_count += 1

        if added_count:
            self.append_log(
                f"Добавлено изображений: {added_count}"
            )

        self.update_interface_state()
        self.position_empty_label()

    def create_thumbnail(self, file_path: str) -> QPixmap:
        reader = QImageReader(file_path)
        reader.setAutoTransform(True)
        reader.setScaledSize(QSize(145, 110))

        image = reader.read()

        if image.isNull():
            pixmap = QPixmap(145, 110)
            pixmap.fill(Qt.transparent)
            return pixmap

        pixmap = QPixmap.fromImage(image)

        return pixmap.scaled(
            145,
            110,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

    def add_thumbnail_item(self, file_path: str):
        item = QListWidgetItem()
        item.setText(Path(file_path).name)
        item.setToolTip(file_path)
        item.setData(Qt.UserRole, file_path)
        item.setTextAlignment(Qt.AlignHCenter)
        item.setIcon(QIcon(self.create_thumbnail(file_path)))
        item.setSizeHint(QSize(165, 155))

        self.image_list.addItem(item)

    def remove_selected(self):
        selected_items = self.image_list.selectedItems()

        for item in selected_items:
            file_path = item.data(Qt.UserRole)
            normalized_path = os.path.normcase(
                os.path.abspath(file_path)
            )

            self.image_file_set.discard(normalized_path)

            try:
                self.image_files.remove(file_path)
            except ValueError:
                pass

            row = self.image_list.row(item)
            self.image_list.takeItem(row)

        self.update_interface_state()
        self.position_empty_label()

    def clear_files(self):
        self.image_files.clear()
        self.image_file_set.clear()
        self.image_list.clear()
        self.progress_bar.setValue(0)
        self.percent_label.setText("0%")
        self.status_label.setText("Готово к работе")

        self.update_interface_state()
        self.position_empty_label()

    def update_output_label(self):
        if getattr(self, "output_folder", ""):
            self.output_path_label.setText(self.output_folder)
            self.output_path_label.setToolTip(self.output_folder)
        else:
            self.output_path_label.setText("Папка не выбрана")
            self.output_path_label.setToolTip("")

    def update_interface_state(self):
        is_processing = bool(
            self.worker and self.worker.isRunning()
        )

        has_files = bool(self.image_files)
        has_output_folder = bool(
            getattr(self, "output_folder", "")
        )
        has_selection = bool(
            self.image_list.selectedItems()
        )

        self.count_label.setText(
            f"{len(self.image_files)} файлов"
        )

        self.empty_label.setVisible(not has_files)

        self.start_button.setEnabled(
            has_files
            and has_output_folder
            and not is_processing
        )

        self.add_button.setEnabled(not is_processing)
        self.add_folder_button.setEnabled(not is_processing)
        self.output_button.setEnabled(not is_processing)
        self.model_combo.setEnabled(not is_processing)
        self.overwrite_checkbox.setEnabled(not is_processing)

        self.remove_button.setEnabled(
            has_selection and not is_processing
        )

        self.clear_button.setEnabled(
            has_files and not is_processing
        )

        self.open_output_button.setEnabled(
            has_output_folder
        )

    def append_log(self, message: str):
        self.log_widget.appendPlainText(message)

        scrollbar = self.log_widget.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def start_processing(self):
        if not self.image_files:
            QMessageBox.warning(
                self,
                "Нет изображений",
                "Добавьте хотя бы одно изображение.",
            )
            return

        if not self.output_folder:
            QMessageBox.warning(
                self,
                "Папка не выбрана",
                "Выберите папку для сохранения результатов.",
            )
            return

        output_path = Path(self.output_folder)

        try:
            output_path.mkdir(
                parents=True,
                exist_ok=True,
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "Ошибка папки",
                f"Не удалось создать или открыть папку:\n{error}",
            )
            return

        model_name = self.model_combo.currentData()

        self.progress_bar.setValue(0)
        self.percent_label.setText("0%")
        self.status_label.setText("Подготовка...")
        self.log_widget.clear()

        self.append_log(
            f"Модель: {model_name}"
        )
        self.append_log(
            f"Папка сохранения: {self.output_folder}"
        )

        self.worker = ProcessingWorker(
            files=self.image_files.copy(),
            output_folder=self.output_folder,
            model_name=model_name,
            overwrite=self.overwrite_checkbox.isChecked(),
            parent=self,
        )

        self.worker.progress_changed.connect(
            self.on_progress_changed
        )
        self.worker.file_started.connect(
            self.on_file_started
        )
        self.worker.file_finished.connect(
            self.on_file_finished
        )
        self.worker.log_message.connect(
            self.append_log
        )
        self.worker.error_occurred.connect(
            self.on_processing_error
        )
        self.worker.processing_finished.connect(
            self.on_processing_finished
        )

        self.stop_button.setVisible(True)
        self.start_button.setVisible(False)

        self.worker.start()
        self.update_interface_state()

    def stop_processing(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.stop_button.setEnabled(False)
            self.status_label.setText(
                "Остановка после текущего изображения..."
            )
            self.append_log(
                "Запрошена остановка обработки..."
            )

    def on_file_started(self, file_path: str):
        self.status_label.setText(
            f"Обработка: {Path(file_path).name}"
        )

    def on_file_finished(
        self,
        source_path: str,
        output_path: str,
    ):
        # Помечаем обработанный элемент галочкой.
        for index in range(self.image_list.count()):
            item = self.image_list.item(index)

            if item.data(Qt.UserRole) == source_path:
                item.setText(f"✓ {Path(source_path).name}")
                item.setToolTip(
                    f"Исходник:\n{source_path}\n\n"
                    f"Результат:\n{output_path}"
                )
                break

    def on_progress_changed(self, current: int, total: int):
        if total <= 0:
            percent = 0
        else:
            percent = int(current / total * 100)

        self.progress_bar.setValue(percent)
        self.percent_label.setText(f"{percent}%")

    def on_processing_error(
        self,
        file_name: str,
        error_message: str,
    ):
        self.append_log(
            f"Ошибка [{file_name}]: {error_message}"
        )

    def on_processing_finished(
        self,
        success_count: int,
        error_count: int,
        cancelled: bool,
    ):
        self.stop_button.setVisible(False)
        self.stop_button.setEnabled(True)
        self.start_button.setVisible(True)

        if cancelled:
            self.status_label.setText("Обработка остановлена")
            title = "Обработка остановлена"
        elif error_count:
            self.status_label.setText(
                "Завершено с ошибками"
            )
            title = "Завершено с ошибками"
        else:
            self.status_label.setText("Готово")
            self.progress_bar.setValue(100)
            self.percent_label.setText("100%")
            title = "Готово"

        message = (
            f"Успешно обработано: {success_count}\n"
            f"Ошибок: {error_count}"
        )

        if cancelled:
            message += "\n\nОбработка была остановлена."

        self.append_log(message.replace("\n", " | "))

        QMessageBox.information(
            self,
            title,
            message,
        )

        self.worker = None
        self.update_interface_state()


def main():
    # Улучшает отображение на мониторах с высоким DPI.
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(
            Qt.AA_EnableHighDpiScaling,
            True,
        )

    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(
            Qt.AA_UseHighDpiPixmaps,
            True,
        )

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORGANIZATION_NAME)

    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

