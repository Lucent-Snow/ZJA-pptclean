import sys
import os
import re
import time
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QLabel, QFrame, QScrollArea, QGridLayout,
    QFileDialog, QTabWidget, QSlider, QLineEdit, QMessageBox,
    QSizePolicy, QStackedLayout, QGraphicsOpacityEffect, QCheckBox
)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QSize, QTimer
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor, QIcon

# --- 核心依赖库 ---
# 确保你已安装: pip install PySide6 opencv-python pillow imagehash numpy
try:
    import cv2
    import numpy as np
    from PIL import Image
    import imagehash
except ImportError:
    print("错误：缺少核心库！")
    print("请先运行: pip install PySide6 opencv-python pillow imagehash numpy")
    sys.exit()

# --- 复用你的辅助函数 ---
def natural_sort_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]

# =iat
# =============================================================================
# 1. 缩略图网格的核心：自定义缩略图小部件 (Thumbnail Widget)
# =============================================================================
class ThumbnailWidget(QFrame):
    """
    一个自定义的小部件，用于显示缩略图。
    它能处理点击事件，并管理自己的 "选中" 和 "过滤" 状态。
    """
    # 当小部件被点击时发出的信号
    clicked = Signal(str, bool) 

    def __init__(self, file_path, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.file_name = os.path.basename(file_path)
        
        self._is_selected = True  # 默认选中
        self._is_filtered = False # 是否被过滤
        
        self.setFixedSize(160, 120)
        self.setFrameShape(QFrame.StyledPanel)
        self.setLineWidth(1)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # 用于显示图片的 QLabel
        self.image_label = QLabel(self)
        self.image_label.setAlignment(Qt.AlignCenter)

        self.opacity_effect = QGraphicsOpacityEffect(self.image_label)
        self.image_label.setGraphicsEffect(self.opacity_effect)

        # 用于显示文件名的 QLabel
        self.name_label = QLabel(self.file_name)
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setWordWrap(True)
        
        layout.addWidget(self.image_label)
        layout.addWidget(self.name_label)
        
        self.update_style() # 初始化样式

    def set_pixmap(self, pixmap):
        """设置缩略图"""
        scaled_pixmap = pixmap.scaled(
            150, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.image_label.setPixmap(scaled_pixmap)

    def is_selected(self):
        return self._is_selected

    def is_filtered(self):
        return self._is_filtered

    def set_filtered(self, filtered):
        """
        设置过滤状态 (例如，水印检测不合格)
        被过滤的会自动取消选中。
        """
        self._is_filtered = filtered
        if self._is_filtered:
            self._is_selected = False
        self.update_style()

    def set_selected(self, selected, force=False):
        """
        设置选中状态。
        force=True: 强制设置（用于"重置"或"手动点击"）
        force=False: 智能设置（用于"选中可见"或"取消全选"）
        """
        if force:
            self._is_selected = selected
            if self._is_selected:
                self._is_filtered = False
        else:
            if selected:
                if not self._is_filtered:
                    self._is_selected = True
            else:
                self._is_selected = False

        self.update_style()

    def update_style(self):
        """根据状态更新视觉样式 (边框和透明度)"""
        if self._is_filtered:
            self.setStyleSheet("""
                ThumbnailWidget {
                    background-color: #303030;
                    border: 2px solid #E57373;
                }
            """)
            self.opacity_effect.setOpacity(0.4)

        elif self._is_selected:
            self.setStyleSheet("""
                ThumbnailWidget {
                    background-color: #3E4A5F;
                    border: 2px solid #64B5F6;
                }
            """)
            self.opacity_effect.setOpacity(1.0)

        else:
            self.setStyleSheet("""
                ThumbnailWidget {
                    background-color: #262626;
                    border: 1px solid #555555;
                }
            """)
            self.opacity_effect.setOpacity(0.6)

    def mousePressEvent(self, event):
        """处理点击事件"""
        if event.button() == Qt.LeftButton:
            # 切换选中状态
            self.set_selected(not self._is_selected, force=True) 
            # 发出信号，通知主窗口状态已改变
            self.clicked.emit(self.file_name, self._is_selected)

# =============================================================================
# 2. 工作线程 (Worker Thread)
#    用于在后台加载和处理所有重活，防止GUI卡死
# =============================================================================
class WorkerThread(QThread):
    """
    一个通用的工作线程，可以执行不同类型的任务。
    """
    # 信号定义
    thumbnail_loaded = Signal(str, QImage)
    files_loaded = Signal(list)
    processing_done = Signal(str, list)
    pdf_done = Signal(str, str)
    log_message = Signal(str)
    loading_finished = Signal()

    def __init__(self):
        super().__init__()
        self.task_type = ""
        self.params = {}

    def run_task(self, task_type, **params):
        """配置任务并启动线程"""
        self.task_type = task_type
        self.params = params
        self.start() # 启动 QThread.run()

    def run(self):
        """线程的主执行函数"""
        try:
            if self.task_type == "LOAD_FILES":
                self.load_files_task(self.params['folder'])
            elif self.task_type == "WATERMARK_CHECK":
                self.watermark_check_task()
            elif self.task_type == "ANIMATION_CHECK":
                self.animation_check_task()
            elif self.task_type == "CREATE_PDF":
                self.create_pdf_task()
        except Exception as e:
            self.log_message.emit(f"线程任务 {self.task_type} 发生严重错误: {e}")

    # --- 任务 1: 加载文件和缩略图 ---
    def load_files_task(self, folder):
        self.log_message.emit(f"正在从 '{folder}' 加载图片...")
        try:
            all_files = [f for f in os.listdir(folder) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
            all_files.sort(key=natural_sort_key)
        except FileNotFoundError:
            self.log_message.emit(f"错误：找不到文件夹 '{folder}'")
            return

        self.files_loaded.emit([os.path.join(folder, f) for f in all_files])
        
        for file_name in all_files:
            if not self.isRunning(): # 允许线程中途停止
                break
            file_path = os.path.join(folder, file_name)
            try:
                # 使用 QImage 读取，更适合在线程中操作
                img = QImage(file_path)
                if img.isNull():
                    raise Exception("无法加载图片")
                # 发送信号，通知主GUI添加一个缩略图（只发送QImage）
                self.thumbnail_loaded.emit(file_path, img)
            except Exception as e:
                self.log_message.emit(f"警告：跳过无法加载的图片 '{file_name}': {e}")
        self.log_message.emit(f"加载完成！共 {len(all_files)} 张图片。")
        # --- 【2. 增加新代码】 ---
        self.loading_finished.emit()

    # 任务 2: 水印检测
    def watermark_check_task(self):
        folder = self.params['folder']
        watermark_path = self.params['watermark_path']
        threshold = self.params['threshold']

        invert_logic = self.params.get('invert_logic', False)

        try:
            template_img = Image.open(watermark_path).convert('L')
            template = np.array(template_img)
        except Exception as e:
            self.log_message.emit(f"错误：无法读取水印模板 '{watermark_path}'，{e}")
            self.processing_done.emit("watermark", list())
            return

        all_files = self.params.get('file_list', [])
        excluded_set = set()

        if invert_logic:
            self.log_message.emit(f"--- 模式: 排除【包含】水印的图片 (阈值: {threshold:.2f}) ---")
        else:
            self.log_message.emit(f"--- 模式: 排除【不含】水印的图片 (阈值: {threshold:.2f}) ---")

        for file_path in all_files:
            file_name = os.path.basename(file_path)
            try:
                main_img = Image.open(file_path).convert('L')
                main_image = np.array(main_img)
                res = cv2.matchTemplate(main_image, template, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, _ = cv2.minMaxLoc(res)

                found_watermark = (max_val >= threshold)

                if invert_logic:
                    if found_watermark:
                        excluded_set.add(file_name)
                        self.log_message.emit(f"✓ (排除) '{file_name}' 包含水印 (匹配: {max_val:.2f})")
                    else:
                        self.log_message.emit(f"✗ (保留) '{file_name}' 不含水印 (匹配: {max_val:.2f})")
                else:
                    if not found_watermark:
                        excluded_set.add(file_name)
                        self.log_message.emit(f"✗ (排除) '{file_name}' 不含水印 (匹配: {max_val:.2f})")
                    else:
                        self.log_message.emit(f"✓ (保留) '{file_name}' 包含水印 (匹配: {max_val:.2f})")

            except Exception as e:
                excluded_set.add(file_name)
                self.log_message.emit(f"警告：跳过无法处理的图片 '{file_name}': {e}")

        self.log_message.emit(f"\n检测完成。共 {len(excluded_set)} 张图片被加入排除列表。")
        self.processing_done.emit("watermark", list(excluded_set))

    # --- 任务 3: 动画帧检测 (你的逻辑) ---
    def animation_check_task(self):
        folder = self.params['folder']
        threshold = self.params['threshold']
        # 注意：只在“已选中”的文件中进行比较
        valid_files = self.params.get('selected_files', [])
        valid_files.sort(key=natural_sort_key) # 确保排序正确
        
        self.log_message.emit(f"\n--- 开始动画帧精简 (阈值: {threshold}) ---")
        self.log_message.emit(f"将在 {len(valid_files)} 张已选中的图片中进行比较...")
        
        excluded_set = set()
        
        if len(valid_files) > 1:
            hash_cache = {}
            try:
                first_path = os.path.join(folder, valid_files[0])
                hash_cache[valid_files[0]] = imagehash.phash(Image.open(first_path))
            except Exception as e:
                self.log_message.emit(f"无法处理图片 '{valid_files[0]}': {e}")
                self.processing_done.emit("animation", list())
                return

            for i in range(1, len(valid_files)):
                prev_file, curr_file = valid_files[i-1], valid_files[i]
                prev_path = os.path.join(folder, prev_file)
                curr_path = os.path.join(folder, curr_file)

                try:
                    hash_prev = hash_cache[prev_file]
                    hash_curr = imagehash.phash(Image.open(curr_path))
                    hash_cache[curr_file] = hash_curr # 缓存当前
                    
                    distance = hash_prev - hash_curr

                    if distance <= threshold:
                        excluded_set.add(prev_file) # 排除前一张
                        self.log_message.emit(f"-> '{prev_file}' 和 '{curr_file}' 相似 (距离: {distance})")
                    else:
                        self.log_message.emit(f"   '{prev_file}' 和 '{curr_file}' 不同 (距离: {distance})")
                except Exception as e:
                    self.log_message.emit(f"比较时出错: {e}")
        
        self.log_message.emit(f"\n动画帧精简完成。共 {len(excluded_set)} 张图片被加入排除列表。")
        # 【修改】: 将 set 转换为 list
        self.processing_done.emit("animation", list(excluded_set))

    # --- 任务 4: 生成PDF (你的逻辑) ---
    def create_pdf_task(self):
        folder = self.params['folder']
        images_to_include = self.params['final_list']
        output_pdf_path = self.params['output_path']
        
        if not images_to_include:
            self.log_message.emit("筛选后没有可用于生成PDF的图片。")
            self.pdf_done.emit("fail", "没有可生成的图片。")
            return
            
        self.log_message.emit(f"\n--- 正在生成PDF... 将包含 {len(images_to_include)} 张图片 ---")

        image_objects = []
        try:
            cover_path = os.path.join(folder, images_to_include[0])
            self.log_message.emit(f"添加封面: {images_to_include[0]}")
            cover = Image.open(cover_path).convert("RGB")
            
            for filename in images_to_include[1:]:
                file_path = os.path.join(folder, filename)
                try:
                    self.log_message.emit(f"添加页面: {filename}")
                    img = Image.open(file_path).convert("RGB")
                    image_objects.append(img)
                except Exception as e:
                    self.log_message.emit(f"警告：跳过无法打开的图片 '{filename}': {e}")
                
            cover.save(output_pdf_path, "PDF", resolution=100.0, save_all=True, append_images=image_objects)
            self.log_message.emit(f"\n🎉 PDF创建成功！")
            self.pdf_done.emit("success", output_pdf_path)
        except Exception as e:
            self.log_message.emit(f"\n创建PDF时发生严重错误: {e}")
            self.pdf_done.emit("fail", str(e))

# =============================================================================
# 3. 主窗口 (Main Window)
#    实现你设计的UI布局
# =============================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PPT图片管理器 (PySide6 版本)")
        self.setGeometry(100, 100, 1400, 800)
        
        # --- 数据存储 ---
        self.image_folder = ""
        self.thumbnail_widgets = {} # 用文件名映射到小部件
        self.all_files_paths = []   # 存储所有文件的完整路径

        # --- 初始化工作线程 ---
        self.worker = WorkerThread()
        self.connect_signals()

        # --- 初始化UI ---
        self.init_ui()
        
        # --- 设置暗色主题 (可选但推荐) ---
        self.set_dark_theme()

        # --- 【增加：设置正确的 QTimer】 ---
        # 创建一个 QTimer
        self.resize_timer = QTimer(self)
        # 设置为"单次触发"(防抖的关键)
        self.resize_timer.setSingleShot(True)
        # 将定时器的"超时"信号连接到我们的布局函数
        self.resize_timer.timeout.connect(self.update_grid_layout)
        # --- 【修改结束】 ---

    def init_ui(self):
        main_widget = QWidget(self)
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # --- 1. 左栏 (导航与信息) ---
        left_panel = QFrame(self)
        left_panel.setFixedWidth(200)
        left_panel_layout = QVBoxLayout(left_panel)
        left_panel_layout.setAlignment(Qt.AlignTop)

        self.btn_select_folder = QPushButton(QIcon.fromTheme("folder-open"), " 选择文件夹")
        self.btn_select_folder.setFixedHeight(40)
        self.btn_select_folder.clicked.connect(self.select_folder)
        left_panel_layout.addWidget(self.btn_select_folder)

        stats_frame = QFrame(self)
        stats_frame.setFrameShape(QFrame.StyledPanel)
        stats_layout = QVBoxLayout(stats_frame)
        stats_layout.addWidget(QLabel("<b>统计信息</b>"))
        
        self.lbl_total = QLabel("总图片数: 0")
        self.lbl_selected = QLabel("已选择: 0")
        self.lbl_filtered = QLabel("已过滤: 0")
        stats_layout.addWidget(self.lbl_total)
        stats_layout.addWidget(self.lbl_selected)
        stats_layout.addWidget(self.lbl_filtered)
        left_panel_layout.addWidget(stats_frame)

        # 动作按钮
        action_frame = QFrame(self)
        action_frame.setFrameShape(QFrame.StyledPanel)
        action_layout = QVBoxLayout(action_frame)
        action_layout.setContentsMargins(5, 5, 5, 5)

        self.btn_reset_all = QPushButton("重置所有 (清空过滤)")
        self.btn_select_visible = QPushButton("选中可见")
        self.btn_deselect_all = QPushButton("取消全选")

        self.btn_reset_all.clicked.connect(self.reset_all_thumbnails)
        self.btn_select_visible.clicked.connect(self.select_visible_thumbnails)
        self.btn_deselect_all.clicked.connect(self.deselect_all_thumbnails)

        action_layout.addWidget(self.btn_reset_all)
        action_layout.addWidget(self.btn_select_visible)
        action_layout.addWidget(self.btn_deselect_all)
        left_panel_layout.addWidget(action_frame)

        # 2. 中栏 (图片网格)
        center_panel = QFrame(self)
        center_panel_layout = QVBoxLayout(center_panel)
        center_panel_layout.setContentsMargins(0, 0, 0, 0)

        self.center_stack = QStackedLayout()
        center_panel_layout.addLayout(self.center_stack)

        # 卡片 0: 初始提示
        self.placeholder_widget = QWidget()
        placeholder_layout = QVBoxLayout(self.placeholder_widget)
        placeholder_layout.setAlignment(Qt.AlignCenter)
        self.placeholder_label = QLabel('点击"选择文件夹"以开始')
        self.placeholder_label.setStyleSheet("font-size: 20px; color: #777;")
        placeholder_layout.addWidget(self.placeholder_label)

        # 卡片 1: QScrollArea
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        # 将 grid_container (包含网格) 放入 scroll_area
        self.scroll_area.setWidget(self.grid_container)

        # 添加到 Stack
        self.center_stack.addWidget(self.placeholder_widget) # Index 0
        self.center_stack.addWidget(self.scroll_area)        # Index 1

        # 默认显示 "暂无图片"
        self.center_stack.setCurrentIndex(0)


        # --- 3. 右栏 (控制面板) ---
        right_panel = QFrame(self)
        right_panel.setFixedWidth(350)
        right_panel_layout = QVBoxLayout(right_panel)

        self.tabs = QTabWidget(self)
        
        # --- Tab 1: 水印检测 ---
        tab_watermark = QWidget()
        layout_wt = QVBoxLayout(tab_watermark)
        layout_wt.setAlignment(Qt.AlignTop)
        
        self.btn_select_watermark = QPushButton("选择水印模板")
        self.btn_select_watermark.clicked.connect(self.select_watermark)
        self.lbl_watermark_path = QLineEdit("未选择")
        self.lbl_watermark_path.setReadOnly(True)
        
        layout_wt.addWidget(QLabel("<b>水印检测</b>"))
        layout_wt.addWidget(self.btn_select_watermark)
        layout_wt.addWidget(self.lbl_watermark_path)
        
        layout_wt.addWidget(QLabel("匹配阈值 (越大越严格)"))
        self.slider_wt = QSlider(Qt.Horizontal)
        self.slider_wt.setRange(10, 100)
        self.slider_wt.setValue(80)
        self.slider_wt.setTickPosition(QSlider.TicksBelow)
        self.slider_wt.valueChanged.connect(lambda v: self.lbl_wt_val.setText(f"{v/100.0:.2f}"))
        self.lbl_wt_val = QLabel("0.80")
        
        wt_slider_layout = QHBoxLayout()
        wt_slider_layout.addWidget(self.slider_wt)
        wt_slider_layout.addWidget(self.lbl_wt_val)
        layout_wt.addLayout(wt_slider_layout)

        self.cb_wt_global = QCheckBox("针对所有图片 (取消勾选则只检测已选中的)")
        self.cb_wt_global.setChecked(False)
        layout_wt.addWidget(self.cb_wt_global)

        self.cb_wt_invert = QCheckBox("反转逻辑 (排除【包含】水印的图片)")
        self.cb_wt_invert.setChecked(False)
        layout_wt.addWidget(self.cb_wt_invert)

        self.btn_run_watermark = QPushButton("1. 开始水印检测")
        self.btn_run_watermark.clicked.connect(self.run_watermark_check)
        layout_wt.addWidget(self.btn_run_watermark)

        # Tab 2: 相似度筛选
        tab_animation = QWidget()
        layout_at = QVBoxLayout(tab_animation)
        layout_at.setAlignment(Qt.AlignTop)

        layout_at.addWidget(QLabel("<b>相似度筛选 (动画帧)</b>"))
        layout_at.addWidget(QLabel("相似阈值 (越小越严格)"))
        self.slider_at = QSlider(Qt.Horizontal)
        self.slider_at.setRange(0, 30)
        self.slider_at.setValue(15)
        self.slider_at.setTickPosition(QSlider.TicksBelow)
        self.slider_at.valueChanged.connect(lambda v: self.lbl_at_val.setText(f"{v}"))
        self.lbl_at_val = QLabel("15")

        at_slider_layout = QHBoxLayout()
        at_slider_layout.addWidget(self.slider_at)
        at_slider_layout.addWidget(self.lbl_at_val)
        layout_at.addLayout(at_slider_layout)
        
        self.btn_run_animation = QPushButton("2. 开始相似度筛选")
        self.btn_run_animation.clicked.connect(self.run_animation_check)
        layout_at.addWidget(self.btn_run_animation)
        
        # --- Tab 3: 生成PDF ---
        tab_pdf = QWidget()
        layout_pdf = QVBoxLayout(tab_pdf)
        layout_pdf.setAlignment(Qt.AlignTop)

        layout_pdf.addWidget(QLabel("<b>生成 PDF</b>"))
        self.btn_select_output = QPushButton("设置PDF输出路径")
        self.btn_select_output.clicked.connect(self.select_output_pdf)
        self.lbl_output_path = QLineEdit("未设置")
        
        layout_pdf.addWidget(self.btn_select_output)
        layout_pdf.addWidget(self.lbl_output_path)
        
        self.btn_generate_pdf = QPushButton("3. 生成PDF")
        self.btn_generate_pdf.setFixedHeight(40)
        self.btn_generate_pdf.clicked.connect(self.run_create_pdf)
        layout_pdf.addWidget(self.btn_generate_pdf)

        # --- 日志 (放在Tab下面) ---
        log_frame = QFrame()
        log_frame.setFrameShape(QFrame.StyledPanel)
        log_layout = QVBoxLayout(log_frame)
        log_layout.addWidget(QLabel("<b>运行日志</b>"))
        self.log_output = QLabel("请选择文件夹...")
        self.log_output.setWordWrap(True)
        self.log_output.setAlignment(Qt.AlignTop)
        
        log_scroll = QScrollArea()
        log_scroll.setWidgetResizable(True)
        log_scroll.setWidget(self.log_output)
        log_layout.addWidget(log_scroll)


        self.tabs.addTab(tab_watermark, "水印检测")
        self.tabs.addTab(tab_animation, "相似度")
        self.tabs.addTab(tab_pdf, "生成PDF")

        # 设置默认选中的标签页为"相似度"（索引为1）
        self.tabs.setCurrentIndex(1)
        
        right_panel_layout.addWidget(self.tabs)
        right_panel_layout.addWidget(log_frame) # 日志在Tab下面

        # --- 组合布局 ---
        main_layout.addWidget(left_panel)
        main_layout.addWidget(center_panel, stretch=1) # 中栏可伸缩
        main_layout.addWidget(right_panel)

    def set_dark_theme(self):
        """一个简单的暗色主题"""
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #2E2E2E;
                color: #E0E0E0;
                font-family: "Microsoft YaHei";
            }
            QFrame {
                background-color: #353535;
                border-radius: 5px;
            }
            QLabel {
                background-color: transparent;
            }
            QPushButton {
                background-color: #555555;
                color: #EEEEEE;
                border: 1px solid #666666;
                padding: 8px;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #6A6A6A;
            }
            QPushButton:pressed {
                background-color: #4A4A4A;
            }
            QPushButton:disabled {
                background-color: #404040;
                color: #777777;
            }
            /* 特殊按钮 (例如 '生成') */
            QPushButton#GenerateButton {
                background-color: #4A8C6A; /* 绿色 */
            }
            QPushButton#GenerateButton:hover {
                background-color: #5AAF8A;
            }
            
            QLineEdit {
                background-color: #404040;
                border: 1px solid #555555;
                padding: 5px;
                border-radius: 3px;
            }
            QCheckBox {
                color: #E0E0E0;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 1px solid #666666;
                background-color: #404040;
            }
            QCheckBox::indicator:checked {
                background-color: #64B5F6;
                border: 1px solid #64B5F6;
            }
            QCheckBox::indicator:checked:hover {
                background-color: #81C9FA;
                border: 1px solid #81C9FA;
            }
            QTabWidget::pane {
                border-top: 2px solid #444444;
            }
            QTabBar::tab {
                background: #353535;
                border: 1px solid #444444;
                padding: 10px;
                border-top-left-radius: 5px;
                border-top-right-radius: 5px;
            }
            QTabBar::tab:selected {
                background: #505050;
                border-bottom-color: #505050; /* 隐藏选中的tab的底边框 */
            }
            QScrollArea {
                background-color: #262626;
                border: 1px solid #444444;
            }
            QScrollBar:vertical {
                border: none;
                background: #2E2E2E;
                width: 10px;
                margin: 0px 0 0px 0;
            }
            QScrollBar::handle:vertical {
                background: #555555;
                min-height: 20px;
                border-radius: 5px;
            }
        """)
        # 为“生成PDF”按钮设置特殊样式
        self.btn_generate_pdf.setObjectName("GenerateButton")


    # =========================================================================
    # 4. 槽函数 (Slots) - 响应信号
    # =========================================================================

    def connect_signals(self):
        """连接工作线程的信号到主窗口的槽函数"""
        self.worker.files_loaded.connect(self.on_files_loaded)
        self.worker.thumbnail_loaded.connect(self.on_thumbnail_loaded)
        self.worker.processing_done.connect(self.on_processing_done)
        self.worker.pdf_done.connect(self.on_pdf_done)
        self.worker.log_message.connect(self.on_log_message)
        self.worker.loading_finished.connect(lambda: self.set_controls_enabled(True))

    @Slot(str)
    def on_log_message(self, message):
        """更新日志区域 (线程安全)"""
        self.log_output.setText(message)

    @Slot(list)
    def on_files_loaded(self, file_paths):
        """当文件列表加载完毕后，初始化网格并切换 Stack"""
        self.all_files_paths = file_paths

        self.clear_grid()

        if not file_paths:
            self.center_stack.setCurrentIndex(0)
            self.update_stats()
            return

        # 有文件，准备网格
        self.thumbnail_widgets = {}
        for file_path in file_paths:
            file_name = os.path.basename(file_path)
            widget = ThumbnailWidget(file_path)
            widget.clicked.connect(self.on_thumbnail_clicked)
            self.thumbnail_widgets[file_name] = widget

        self.center_stack.setCurrentIndex(1)
        QTimer.singleShot(100, self.update_grid_layout)
        self.update_stats()

    @Slot(str, QImage)
    def on_thumbnail_loaded(self, file_path, q_image):
        """当一张缩略图加载完毕后，将其放入对应的小部件"""
        # 在主线程中将 QImage 转换为 QPixmap
        if q_image.isNull():
            self.on_log_message(f"警告: 收到空的 QImage '{file_path}'")
            return
        pixmap = QPixmap.fromImage(q_image)

        file_name = os.path.basename(file_path)
        if file_name in self.thumbnail_widgets:
            self.thumbnail_widgets[file_name].set_pixmap(pixmap)

    @Slot(str, bool)
    def on_thumbnail_clicked(self, file_name, is_selected):
        """当一个缩略图被点击时，更新统计数据"""
        self.update_stats()

    @Slot(str, list)
    def on_processing_done(self, task_name, excluded_list):
        """当水印或动画检测完成后，更新UI"""
        self.on_log_message(f"任务 {task_name} 完成，正在更新UI...")

        excluded_set = set(excluded_list)

        for file_name, widget in self.thumbnail_widgets.items():
            if file_name in excluded_set:
                widget.set_filtered(True)

        self.update_stats()
        self.set_controls_enabled(True)
        self.on_log_message(f"UI更新完毕！")

    @Slot(str, str)
    def on_pdf_done(self, status, message_or_path):
        """PDF生成完毕"""
        if status == "success":
            QMessageBox.information(self, "成功", f"PDF已成功生成！\n保存在: {message_or_path}")
            # 尝试打开PDF所在文件夹
            try:
                os.startfile(os.path.dirname(message_or_path))
            except Exception:
                pass # 忽略错误
        else:
            QMessageBox.critical(self, "失败", f"PDF生成失败: \n{message_or_path}")
        
        self.set_controls_enabled(True)

    # 实现三个动作的槽函数
    @Slot()
    def reset_all_thumbnails(self):
        """重置所有缩略图：清除过滤 + 全部选中。"""
        if not self.thumbnail_widgets:
            return

        for widget in self.thumbnail_widgets.values():
            widget.set_selected(True, force=True)

        self.update_stats()

    @Slot()
    def select_visible_thumbnails(self):
        """选中所有未被过滤的缩略图。"""
        if not self.thumbnail_widgets:
            return

        for widget in self.thumbnail_widgets.values():
            widget.set_selected(True, force=False)

        self.update_stats()

    @Slot()
    def deselect_all_thumbnails(self):
        """取消选中所有缩略图。"""
        if not self.thumbnail_widgets:
            return

        for widget in self.thumbnail_widgets.values():
            widget.set_selected(False, force=False)

        self.update_stats()


    # =========================================================================
    # 5. UI 辅助函数 (主线程)
    # =========================================================================
    
    def set_controls_enabled(self, enabled):
        """统一控制所有按钮的可用状态，防止重复操作"""
        self.btn_select_folder.setEnabled(enabled)
        self.tabs.setEnabled(enabled)
        # 如果正在运行，设置加载中的文本
        if not enabled:
            self.btn_run_watermark.setText("正在检测...")
            self.btn_run_animation.setText("正在筛选...")
            self.btn_generate_pdf.setText("正在生成...")
        else:
            self.btn_run_watermark.setText("1. 开始水印检测")
            self.btn_run_animation.setText("2. 开始相似度筛选")
            self.btn_generate_pdf.setText("3. 生成PDF")

    def clear_grid(self):
        """清空网格中的所有小部件。"""
        for widget in self.thumbnail_widgets.values():
            self.grid_layout.removeWidget(widget)
            widget.deleteLater()

        self.thumbnail_widgets = {}

    def update_grid_layout(self):
        """
        根据当前窗口宽度，重新计算网格的列数并重新布局。
        """
        if not self.thumbnail_widgets:
            return

        container_width = self.scroll_area.viewport().width() - 10
        widget_width = 160 + 10
        cols = max(1, container_width // widget_width)

        row, col = 0, 0

        for file_name in sorted(self.thumbnail_widgets.keys(), key=natural_sort_key):
            widget = self.thumbnail_widgets[file_name]
            self.grid_layout.removeWidget(widget)
            self.grid_layout.addWidget(widget, row, col)

            col += 1
            if col >= cols:
                col = 0
                row += 1

    def update_stats(self):
        """计算并更新左侧的统计数据"""
        total = len(self.thumbnail_widgets)
        selected = 0
        filtered = 0
        for widget in self.thumbnail_widgets.values():
            if widget.is_selected():
                selected += 1
            if widget.is_filtered():
                filtered += 1
                
        self.lbl_total.setText(f"总图片数: {total}")
        self.lbl_selected.setText(f"已选择: {selected}")
        self.lbl_filtered.setText(f"已过滤: {filtered}")
        
    def resizeEvent(self, event):
        """
        在窗口大小改变时，启动 QTimer 防抖。
        """
        self.resize_timer.start(300)
        super().resizeEvent(event)

    def get_selected_files(self, filtered=False):
        """获取当前选中的或被过滤的文件名列表"""
        files = []
        for file_name, widget in self.thumbnail_widgets.items():
            if filtered and widget.is_filtered():
                files.append(file_name)
            elif not filtered and widget.is_selected():
                files.append(file_name)
        
        files.sort(key=natural_sort_key)
        return files


    # =========================================================================
    # 6. UI 动作 (启动线程)
    # =========================================================================

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择图片文件夹")
        if folder:
            self.image_folder = folder
            pdf_name = os.path.basename(folder) + ".pdf"
            self.lbl_output_path.setText(os.path.join(folder, pdf_name))

            self.clear_grid()
            self.log_output.setText("正在加载文件...")

            self.set_controls_enabled(False)
            self.worker.run_task("LOAD_FILES", folder=self.image_folder)

    def select_watermark(self):
        file, _ = QFileDialog.getOpenFileName(self, "选择水印模板", filter="Image Files (*.png *.jpg *.jpeg)")
        if file:
            self.lbl_watermark_path.setText(file)

    def select_output_pdf(self):
        file, _ = QFileDialog.getOpenFileName(self, "设置PDF输出路径", filter="PDF Files (*.pdf)")
        if file:
            self.lbl_output_path.setText(file)

    def run_watermark_check(self):
        wt_path = self.lbl_watermark_path.text()
        if not self.image_folder:
            QMessageBox.warning(self, "错误", "请先选择一个图片文件夹！")
            return
        if not os.path.exists(wt_path):
            QMessageBox.warning(self, "错误", "请先选择一个有效的水印模板文件！")
            return

        # 根据复选框决定要处理的文件列表
        if self.cb_wt_global.isChecked():
            files_to_check = self.all_files_paths
        else:
            selected_basenames = self.get_selected_files(filtered=False)
            files_to_check = [os.path.join(self.image_folder, basename) for basename in selected_basenames]

            if not files_to_check:
                QMessageBox.information(self, "提示", "没有已选中的图片可供检测。")
                return

        invert_logic = self.cb_wt_invert.isChecked()

        self.set_controls_enabled(False)
        self.worker.run_task(
            "WATERMARK_CHECK",
            folder=self.image_folder,
            watermark_path=wt_path,
            threshold=self.slider_wt.value() / 100.0,
            file_list=files_to_check,
            invert_logic=invert_logic
        )

    def run_animation_check(self):
        if not self.image_folder:
            QMessageBox.warning(self, "错误", "请先选择一个图片文件夹！")
            return
            
        # 注意：只在“已选中”的图片中运行
        selected_files = self.get_selected_files(filtered=False)
        if len(selected_files) < 2:
            QMessageBox.information(self, "提示", "至少需要选中2张图片才能进行相似度比较。")
            return

        self.set_controls_enabled(False)
        self.worker.run_task(
            "ANIMATION_CHECK",
            folder=self.image_folder,
            threshold=self.slider_at.value(),
            selected_files=selected_files # 只传递已选中的
        )

    def run_create_pdf(self):
        output_path = self.lbl_output_path.text()
        if not self.image_folder:
            QMessageBox.warning(self, "错误", "请先选择一个图片文件夹！")
            return
        if output_path == "未设置":
            QMessageBox.warning(self, "错误", "请先设置PDF的输出路径！")
            return
            
        final_list = self.get_selected_files(filtered=False)
        if not final_list:
            QMessageBox.warning(self, "错误", "没有选中的图片，无法生成PDF！")
            return
            
        self.set_controls_enabled(False)
        self.worker.run_task(
            "CREATE_PDF",
            folder=self.image_folder,
            final_list=final_list,
            output_path=output_path
        )

    def closeEvent(self, event):
        """确保在关闭窗口时，工作线程也能被安全停止"""
        if self.worker.isRunning():
            self.worker.requestInterruption() # 请求停止
            self.worker.wait() # 等待线程结束
        event.accept()

# =============================================================================
# 7. 启动程序
# =============================================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())