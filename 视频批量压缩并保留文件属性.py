import os
import shutil
import time
import subprocess
import json
from PyQt6.QtWidgets import (QApplication, QMainWindow, QPushButton, QVBoxLayout, 
                            QHBoxLayout, QWidget, QFileDialog, QTreeWidget, 
                            QTreeWidgetItem, QLabel, QCheckBox, QHeaderView,
                            QDoubleSpinBox, QTreeWidgetItemIterator)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot, QByteArray, QSize
from PyQt6.QtGui import QPixmap
import platform


"""
使用公式估算比特率（仅供参考）
有一个简单的估算公式：比特率（Mbps）=（分辨率宽度 × 分辨率高度 × 帧率 × 量化系数）/（1024×1024）。
其中量化系数可以根据视频质量要求来选择，一般在 0.07 - 0.15 之间。
例如，对于一个 1920×1080、30fps 的视频，如果希望画质较好，选择量化系数为 0.12，那么比特率大约为（1920×1080×30×0.12）/（1024×1024）≈7.3Mbps。
返回单位 bps
"""
def estimate_appropriate_bitrate(input_video_path, quantization_coef):
    # 获取视频的分辨率和帧率
    command = f'ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate,duration,bit_rate -of json "{input_video_path}"'
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"获取视频信息失败：{result.stderr}")
        return 0, None, None, None
    
    try:
        data = json.loads(result.stdout)
        stream = data['streams'][0]
        width = int(stream['width'])
        height = int(stream['height'])
        frame_rate = eval(stream['r_frame_rate'])  # 处理类似 "30000/1001" 的格式
        duration = float(stream.get('duration', 0))
        current_bitrate = int(stream.get('bit_rate', 0))
        
        print(f"分辨率：{width}x{height}, 帧率：{frame_rate}, 时长：{duration}秒")

        # 计算建议比特率
        bitrate = (width * height * frame_rate * quantization_coef)
        return bitrate, duration, current_bitrate, frame_rate
    except Exception as e:
        print(f"解析视频信息失败：{e}")
        return 0, None, None, None


class VideoCompressThread(QThread):
    progress_signal = pyqtSignal(dict)
    finished_signal = pyqtSignal()

    def __init__(self, folder_path, target_folder, delete_source, quantization_coef):
        super().__init__()
        self.folder_path = folder_path
        self.target_folder = target_folder
        self.delete_source = delete_source
        self.quantization_coef = quantization_coef
        self.is_running = True
        self.current_process = None

    def update_quantization_coef(self, new_coef):
        """更新量化系数"""
        self.quantization_coef = new_coef
        print(f"量化系数已更新为：{new_coef}")

    def run(self):
        if not os.path.exists(self.target_folder):
            os.makedirs(self.target_folder)

        video_extensions = ['.mp4', '.avi', '.mov', '.mkv']
        files_to_process = []
        
        # 收集所有视频文件及其修改时间
        for root, dirs, files in os.walk(self.folder_path):
            for file in files:
                file_extension = os.path.splitext(file)[1].lower()
                if file_extension in video_extensions:
                    file_path = os.path.join(root, file)
                    mod_time = os.path.getmtime(file_path)
                    files_to_process.append((file_path, mod_time))

        # 按修改时间排序，最新的文件在前
        files_to_process.sort(key=lambda x: x[1], reverse=True)

        # 处理排序后的文件
        for file_path, mod_time in files_to_process:
            if not self.is_running:
                if self.current_process:
                    self.current_process.terminate()
                return

            file = os.path.basename(file_path)
            input_video_path = file_path
            
            # 定义输出文件路径
            file_name_without_extension = os.path.splitext(file)[0]
            file_extension = os.path.splitext(file)[1]
            output_video_name = file_name_without_extension + "_comp" + file_extension
            output_video_path = os.path.join(self.target_folder, output_video_name)
            
            # 获取原始文件大小
            input_video_size = os.path.getsize(input_video_path)
            start_time = time.time()
            print(f"正在压缩：{input_video_path}，原文件大小：{input_video_size / 1024 / 1024:.2f}MB")
            
            # 获取视频信息并更新表格
            appropriate_bitrate, duration, current_bitrate, frame_rate = estimate_appropriate_bitrate(input_video_path, self.quantization_coef)
            if appropriate_bitrate == 0:
                print(f"无法获取视频信息，跳过压缩：{input_video_path}")
                self.progress_signal.emit({
                    "file_name": file,
                    "status": "获取信息失败",
                    "error": True
                })
                continue

            # 检查是否需要压缩
            if current_bitrate and appropriate_bitrate >= current_bitrate * 0.95:
                print(f"无需压缩：{file}，新比特率（{appropriate_bitrate/1024/1024:.2f}Mbps）接近或高于原比特率（{current_bitrate/1024/1024:.2f}Mbps）")
                self.progress_signal.emit({
                    "file_name": file,
                    "duration": f"{duration:.2f}" if duration else "未知",
                    "original_size": input_video_size,
                    "original_bitrate": current_bitrate / 1024 / 1024 if current_bitrate else 0,
                    "target_bitrate": appropriate_bitrate / 1024 / 1024,
                    "status": "无需压缩",
                    "skip_compression": True
                })
                continue

            # 发送开始压缩信号，更新视频信息
            progress_data = {
                "file_name": file,
                "duration": f"{duration:.2f}" if duration else "未知",
                "original_size": input_video_size,
                "original_bitrate": current_bitrate / 1024 / 1024 if current_bitrate else 0,
                "target_bitrate": appropriate_bitrate / 1024 / 1024,
                "status": "正在压缩"
            }
            self.progress_signal.emit(progress_data)

            # 直接压缩为目标文件
            try:
                # 添加 -progress pipe:1 参数来输出进度信息
                command = [
                    'ffmpeg', '-i', input_video_path,
                    '-b:v', str(appropriate_bitrate),
                    '-progress', 'pipe:1',  # 输出进度到管道
                    '-nostats',  # 禁用默认统计信息
                    output_video_path
                ]
                creation_flags = subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0
                self.current_process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,  # 使用文本模式
                    creationflags=creation_flags
                )

                # 读取进度信息
                while self.current_process.poll() is None and self.is_running:
                    line = self.current_process.stdout.readline()
                    if line:
                        if 'out_time_ms=' in line:
                            try:
                                # 处理 'N/A' 的情况
                                time_str = line.split('=')[1].strip()
                                if time_str != 'N/A':
                                    time_ms = int(time_str) / 1000000  # 转换为秒
                                    if duration:
                                        progress = (time_ms / float(duration)) * 100
                                        # 更新进度信息
                                        progress_data.update({
                                            "status": f"正在压缩 {progress:.1f}%"
                                        })
                                        self.progress_signal.emit(progress_data)
                            except (ValueError, IndexError) as e:
                                print(f"解析进度信息失败：{e}")
                                continue

                if not self.is_running:
                    if os.path.exists(output_video_path):
                        os.remove(output_video_path)
                    return

                # 检查压缩结果
                if os.path.exists(output_video_path):
                    output_video_size = os.path.getsize(output_video_path)
                    end_time = time.time()
                    
                    # 更新状态为"计算SSIM"
                    progress_data.update({
                        "compressed_size": output_video_size,
                        "compression_ratio": output_video_size / input_video_size,
                        "time_taken": end_time - start_time,
                        "status": "计算SSIM"
                    })
                    self.progress_signal.emit(progress_data)
                    
                    # 计算SSIM并获取带数值的影响程度描述
                    ssim = self.calculate_ssim(input_video_path, output_video_path)
                    impact_level = self.get_impact_level(ssim)
                    
                    # 更新状态为"复制属性"
                    progress_data.update({
                        "status": "复制属性"
                    })
                    self.progress_signal.emit(progress_data)
                    
                    # 复制文件属性
                    if self.copy_video_metadata(input_video_path, output_video_path):
                        # 如果启用了替换源文件选项
                        if self.delete_source:  # 保持变量名不变，但功能改为替换
                            try:
                                # 备份原文件（添加.bak后缀）
                                backup_path = input_video_path + '.bak'
                                os.rename(input_video_path, backup_path)
                                
                                # 将压缩后的文件移动到源文件位置
                                os.rename(output_video_path, input_video_path)
                                
                                # 删除备份文件
                                os.remove(backup_path)
                                
                                print(f"已替换源文件：{input_video_path}")
                            except Exception as e:
                                print(f"替换源文件失败：{e}")
                                # 如果替换失败，尝试恢复原文件
                                try:
                                    if os.path.exists(backup_path):
                                        os.rename(backup_path, input_video_path)
                                except Exception as e2:
                                    print(f"恢复原文件失败：{e2}")
                        
                        # 更新最终结果
                        progress_data.update({
                            "impact_level": impact_level,
                            "status": "完成"
                        })
                    else:
                        progress_data.update({
                            "impact_level": impact_level,
                            "status": "完成(属性复制失败)"
                        })
                    self.progress_signal.emit(progress_data)
                else:
                    print(f"压缩失败：{file}")
                    progress_data.update({
                        "status": "压缩失败",
                        "impact_level": "未知"
                    })
                    self.progress_signal.emit(progress_data)

            except Exception as e:
                print(f"压缩视频失败：{e}")
                progress_data.update({"status": "压缩失败"})
                self.progress_signal.emit(progress_data)

        self.finished_signal.emit()

    def stop(self):
        self.is_running = False
        # 如果有正在运行的进程，立即终止它
        if self.current_process:
            try:
                if platform.system() == 'Windows':
                    import ctypes
                    PROCESS_TERMINATE = 1
                    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, self.current_process.pid)
                    ctypes.windll.kernel32.TerminateProcess(handle, -1)
                    ctypes.windll.kernel32.CloseHandle(handle)
                else:
                    import signal
                    os.kill(self.current_process.pid, signal.SIGTERM)
            except Exception as e:
                print(f"终止进程失败：{e}")

    def calculate_ssim(self, original_path, compressed_path):
        """计算两个视频的SSIM值"""
        try:
            # 使用ffmpeg提取一帧进行比较
            command = [
                'ffmpeg',
                '-i', original_path,
                '-i', compressed_path,
                '-filter_complex', '[0:v][1:v]ssim',
                '-f', 'null',
                '-'
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            
            # 从输出中提取SSIM值
            for line in result.stderr.split('\n'):
                if 'SSIM' in line:
                    try:
                        ssim = float(line.split('All:')[1].split('(')[0].strip())
                        return ssim
                    except:
                        return None
            return None
        except Exception as e:
            print(f"计算SSIM失败：{e}")
            return None

    def get_impact_level(self, ssim):
        """根据SSIM值返回影响程度描述和具体数值"""
        if ssim is None:
            return "未知"
        
        # 格式化SSIM值为百分比
        ssim_percent = f"{ssim * 100:.2f}%"
        
        if ssim >= 0.98:
            return f"极小 ({ssim_percent})"
        elif ssim >= 0.95:
            return f"轻微 ({ssim_percent})"
        elif ssim >= 0.90:
            return f"中等 ({ssim_percent})"
        else:
            return f"显著 ({ssim_percent})"

    def copy_video_metadata(self, input_path, output_path):
        """复制视频的元数据信息"""
        try:
            # 使用ffmpeg复制元数据
            command = [
                'ffmpeg', '-i', input_path,  # 输入为原始文件
                '-i', output_path,  # 压缩后的文件
                '-map', '1:v',  # 使用第二个输入的视频流（压缩后的）
                '-map_metadata', '0',  # 使用第一个输入的元数据（原始的）
                '-c', 'copy',  # 仅复制，不重新编码
                '-y',  # 覆盖输出文件
                f"{os.path.splitext(output_path)[0]}_temp{os.path.splitext(output_path)[1]}"  # 临时文件
            ]
            
            # 执行命令
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"ffmpeg命令执行失败：{result.stderr}")
                return False
            
            # 替换原文件
            try:
                temp_path = f"{os.path.splitext(output_path)[0]}_temp{os.path.splitext(output_path)[1]}"
                
                # 在Windows系统中，需要先删除目标文件
                if platform.system() == 'Windows' and os.path.exists(output_path):
                    os.remove(output_path)
                
                os.rename(temp_path, output_path)
                
                # 复制文件时间属性
                stats = os.stat(input_path)
                os.utime(output_path, (stats.st_atime, stats.st_mtime))
                
                return True
            except Exception as e:
                print(f"替换文件失败：{e}")
                return False
                
        except Exception as e:
            print(f"复制元数据失败：{e}")
            return False
        finally:
            # 清理可能存在的临时文件
            try:
                temp_path = f"{os.path.splitext(output_path)[0]}_temp{os.path.splitext(output_path)[1]}"
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception as e:
                print(f"清理临时文件失败：{e}")

class ThumbnailLoader(QThread):
    thumbnail_ready = pyqtSignal(object, QPixmap)
    
    def __init__(self, file_path, item):
        super().__init__()
        self.file_path = file_path
        self.item = item
        
    def run(self):
        try:
            command = [
                'ffmpeg',
                '-i', self.file_path,
                '-vf', 'scale=120:-1',
                '-frames:v', '1',
                '-f', 'image2pipe',
                '-vcodec', 'png',
                '-'
            ]
            
            result = subprocess.run(command, capture_output=True)
            if result.returncode == 0:
                image_data = QByteArray(result.stdout)
                pixmap = QPixmap()
                if pixmap.loadFromData(image_data):
                    if pixmap.height() > 67:
                        pixmap = pixmap.scaledToHeight(67, Qt.TransformationMode.SmoothTransformation)
                    self.thumbnail_ready.emit(self.item, pixmap)
        except Exception as e:
            print(f"生成缩略图失败：{e}")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("视频批量压缩工具")
        self.setMinimumSize(800, 600)
        
        # 加载窗口设置
        self.settings_file = "settings.json"
        self.load_window_settings()
        
        # 主布局
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        # 文件夹选择区域
        folder_layout = QHBoxLayout()
        self.source_path_label = QLabel("源文件夹：")
        self.source_path_button = QPushButton("选择文件夹")
        self.source_path_button.clicked.connect(self.select_source_folder)
        folder_layout.addWidget(self.source_path_label)
        folder_layout.addWidget(self.source_path_button)
        layout.addLayout(folder_layout)

        # 量化系数设置区域
        coef_layout = QHBoxLayout()
        coef_label = QLabel("量化系数(0.07-0.15)：")
        self.coef_spin = QDoubleSpinBox()
        self.coef_spin.setRange(0.01, 1.00)
        self.coef_spin.setSingleStep(0.01)
        self.coef_spin.setDecimals(2)
        self.coef_spin.setValue(0.12)
        self.coef_spin.valueChanged.connect(self.on_coef_changed)
        
        # 添加警告提示
        self.coef_warning = QLabel("")
        self.coef_warning.setStyleSheet("color: red")
        
        coef_layout.addWidget(coef_label)
        coef_layout.addWidget(self.coef_spin)
        coef_layout.addWidget(self.coef_warning)
        coef_layout.addStretch()
        layout.addLayout(coef_layout)

        # 将表格改为树形结构
        self.tree = QTreeWidget()
        
        # 初始化表头（先不设置列，等加载设置后再设置）
        self.init_tree_columns()
        
        # 设置缩略图列的宽度和行高
        self.tree.setColumnWidth(1, 120)  # 设置缩略图列宽（改为第二列）
        self.tree.setIconSize(QSize(120, 67))
        
        # 其他列自适应内容
        for i in range(2, self.tree.columnCount()):  # 从第三列开始自适应
            self.tree.header().setSectionResizeMode(
                i, QHeaderView.ResizeMode.ResizeToContents
            )
        
        # 第一列（文件夹/文件名）可以伸展
        self.tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Interactive
        )
        self.tree.setColumnWidth(0, 200)  # 设置默认宽度
        
        # 设置为只读
        self.tree.setEditTriggers(QTreeWidget.EditTrigger.NoEditTriggers)
        self.tree.itemDoubleClicked.connect(self.handle_item_double_click)
        layout.addWidget(self.tree)
        
        # 存储缩略图加载线程的引用
        self.thumbnail_threads = []

        # 展开/折叠按钮和选项的布局
        options_layout = QHBoxLayout()
        
        # 展开/折叠按钮
        self.expand_button = QPushButton("展开全部")
        self.expand_button.clicked.connect(self.toggle_expand_collapse)
        self.expand_button.setFixedWidth(100)
        options_layout.addWidget(self.expand_button)
        
        # 添加弹性空间
        options_layout.addStretch()
        
        # 显示缩略图选项
        self.show_thumbnail_cb = QCheckBox("显示缩略图")
        self.show_thumbnail_cb.setChecked(True)  # 默认显示
        self.show_thumbnail_cb.stateChanged.connect(self.toggle_thumbnails)
        options_layout.addWidget(self.show_thumbnail_cb)
        
        # 替换源文件选项
        self.replace_source_cb = QCheckBox("压缩后替换源文件")
        self.replace_source_cb.stateChanged.connect(self.on_replace_source_changed)
        options_layout.addWidget(self.replace_source_cb)
        
        # 将选项布局添加到主布局
        layout.addLayout(options_layout)

        # 控制按钮
        button_layout = QHBoxLayout()
        self.start_button = QPushButton("开始压缩")
        self.stop_button = QPushButton("停止压缩")
        self.start_button.clicked.connect(self.start_compression)
        self.stop_button.clicked.connect(self.stop_compression)
        self.stop_button.setEnabled(False)
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.stop_button)
        layout.addLayout(button_layout)

        # 加载设置
        self.load_settings()

        self.temp_files = []  # 用于跟踪临时文件

    def init_tree_columns(self):
        """初始化树形结构的列"""
        headers = ["文件夹/文件名"]
        headers.extend([
            "时长", "文件大小", "当前比特率",
            "目标比特率", "压缩后大小", "体积比例", "影响程度", "状态"
        ])
        
        self.tree.setColumnCount(len(headers))
        self.tree.setHeaderLabels(headers)
        
        # 设置缩略图列宽度
        if len(headers) > 1 and headers[1] == "缩略图":
            self.tree.setColumnWidth(1, 120)

    def load_settings(self):
        """加载设置"""
        try:
            with open(self.settings_file, 'r', encoding='utf-8') as f:
                settings = json.load(f)
                self.source_folder = settings.get('last_folder', '')
                self.coef_spin.setValue(settings.get('quantization_coef', 0.12))
                self.replace_source_cb.setChecked(settings.get('replace_source', False))
                self.show_thumbnail_cb.setChecked(settings.get('show_thumbnail', True))
                if self.source_folder:
                    self.source_path_label.setText(f"源文件夹：{self.source_folder}")
                    self.update_file_list()
        except (FileNotFoundError, json.JSONDecodeError):
            self.source_folder = ''
            self.coef_spin.setValue(0.12)
            self.replace_source_cb.setChecked(False)
            self.show_thumbnail_cb.setChecked(True)

    def load_window_settings(self):
        """加载窗口设置"""
        try:
            with open(self.settings_file, 'r', encoding='utf-8') as f:
                settings = json.load(f)
                window_settings = settings.get('window', {})
                
                # 获取所有屏幕
                screens = QApplication.screens()
                if not screens:
                    return
                
                # 获取主屏幕尺寸
                primary_screen = QApplication.primaryScreen()
                screen_geometry = primary_screen.availableGeometry()
                
                # 恢复窗口尺寸
                if 'size' in window_settings:
                    width = min(window_settings['size']['width'], screen_geometry.width())
                    height = min(window_settings['size']['height'], screen_geometry.height())
                    self.resize(width, height)
                
                # 恢复窗口位置
                if 'pos' in window_settings:
                    x = window_settings['pos']['x']
                    y = window_settings['pos']['y']
                    
                    # 检查位置是否在任何屏幕内
                    pos_visible = False
                    for screen in screens:
                        screen_geo = screen.availableGeometry()
                        if screen_geo.contains(x, y):
                            pos_visible = True
                            break
                    
                    # 如果位置有效则使用，否则居中显示
                    if pos_visible:
                        self.move(x, y)
                    else:
                        self.center_window()
                else:
                    self.center_window()
                
        except (FileNotFoundError, json.JSONDecodeError):
            self.center_window()

    def center_window(self):
        """将窗口居中显示"""
        screen = QApplication.primaryScreen().availableGeometry()
        size = self.geometry()
        x = (screen.width() - size.width()) // 2
        y = (screen.height() - size.height()) // 2
        self.move(x, y)

    def save_settings(self):
        """保存所有设置"""
        try:
            settings = {}
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
            
            settings.update({
                'last_folder': self.source_folder,
                'quantization_coef': self.coef_spin.value(),
                'replace_source': self.replace_source_cb.isChecked(),
                'show_thumbnail': self.show_thumbnail_cb.isChecked(),
                'window': {
                    'size': {
                        'width': self.width(),
                        'height': self.height()
                    },
                    'pos': {
                        'x': self.x(),
                        'y': self.y()
                    }
                }
            })
            
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"保存设置失败：{e}")

    def select_source_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择源文件夹", self.source_folder)  # 使用上次的路径作为默认值
        if folder:
            self.source_folder = folder
            self.source_path_label.setText(f"源文件夹：{folder}")
            self.save_settings()  # 保存选择的文件夹路径
            self.update_file_list()

    def update_file_list(self):
        video_extensions = ['.mp4', '.avi', '.mov', '.mkv']
        
        # 在更新前禁用树形控件
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        
        # 清理旧的缩略图加载线程
        for thread in self.thumbnail_threads:
            thread.quit()
            thread.wait()
        self.thumbnail_threads.clear()
        
        # 设置列标题
        headers = [
            "文件夹/文件名",
            "缩略图",  # 始终保留缩略图列
            "时长", "文件大小", "当前比特率",
            "目标比特率", "压缩后大小", "体积比例", "影响程度", "状态"
        ]
        
        self.tree.setColumnCount(len(headers))
        self.tree.setHeaderLabels(headers)
        
        # 根据开关状态设置缩略图列宽度
        self.tree.setColumnWidth(1, 120 if self.show_thumbnail_cb.isChecked() else 0)
        
        # 创建根节点字典，用于跟踪文件夹节点
        folder_nodes = {}
        
        # 先收集所有文件信息
        files_info = []
        for root, dirs, files in os.walk(self.source_folder):
            rel_path = os.path.relpath(root, self.source_folder)
            for filename in sorted(files):
                if os.path.splitext(filename)[1].lower() in video_extensions:
                    file_path = os.path.join(root, filename)
                    files_info.append((rel_path, filename, file_path))

        # 按文件夹路径排序
        files_info.sort(key=lambda x: (x[0], x[1]))

        # 创建文件夹结构和添加文件
        for rel_path, filename, file_path in files_info:
            # 创建当前文件夹的节点
            if rel_path == '.':
                current_node = self.tree
            else:
                # 确保父文件夹路径存在
                path_parts = rel_path.split(os.sep)
                current_path = ''
                parent_node = self.tree
                
                for part in path_parts:
                    current_path = os.path.join(current_path, part) if current_path else part
                    if current_path not in folder_nodes:
                        folder_node = QTreeWidgetItem(parent_node)
                        folder_node.setText(0, part)
                        folder_node.setExpanded(True)
                        folder_nodes[current_path] = folder_node
                    parent_node = folder_nodes[current_path]
                current_node = parent_node

            # 创建文件项
            item = QTreeWidgetItem(current_node)
            item.setText(0, filename)
            
            # 设置文件大小
            size_in_bytes = os.path.getsize(file_path)
            formatted_size = format_size(size_in_bytes)
            item.setText(3, formatted_size)
            
            # 设置初始状态
            item.setText(9, "等待压缩")
            
            # 存储完整文件路径
            item.setData(0, Qt.ItemDataRole.UserRole, file_path)
            
            # 只在开关打开时加载缩略图
            if self.show_thumbnail_cb.isChecked():
                thread = ThumbnailLoader(file_path, item)
                thread.thumbnail_ready.connect(self.set_thumbnail)
                self.thumbnail_threads.append(thread)
                thread.start()

        # 重新启用树形控件的更新
        self.tree.setUpdatesEnabled(True)

        # 更新完文件列表后，更新展开/折叠按钮的文本
        for i in range(self.tree.topLevelItemCount()):
            if self.tree.topLevelItem(i).isExpanded():
                self.expand_button.setText("折叠全部")
                break
        else:
            self.expand_button.setText("展开全部")

    def set_thumbnail(self, item, pixmap):
        """设置缩略图"""
        # 只在显示缩略图开启时设置缩略图
        if self.show_thumbnail_cb.isChecked() and not item.isHidden():
            label = QLabel()
            label.setPixmap(pixmap)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tree.setItemWidget(item, 1, label)

    def start_compression(self):
        if not self.source_folder:
            return
        
        self.source_path_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        # 不再禁用量化系数调整
        # self.coef_spin.setEnabled(False)
        
        self.compress_thread = VideoCompressThread(
            self.source_folder, 
            self.source_folder, 
            self.replace_source_cb.isChecked(),
            self.coef_spin.value()
        )
        self.compress_thread.progress_signal.connect(self.update_progress)
        self.compress_thread.finished_signal.connect(self.compression_finished)
        self.compress_thread.start()

    def stop_compression(self):
        if hasattr(self, 'compress_thread'):
            # 立即更新界面状态
            self.source_path_button.setEnabled(True)
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            # 不再需要重新启用量化系数调整
            # self.coef_spin.setEnabled(True)
            
            # 更新正在压缩的文件状态为"停止压缩"
            for row in range(self.tree.topLevelItemCount()):
                for col in range(self.tree.columnCount()):
                    if self.tree.itemWidget(self.tree.topLevelItem(row), col) is not None:
                        self.tree.itemWidget(self.tree.topLevelItem(row), col).setStyleSheet("background-color: red")
            
            # 停止压缩线程
            self.compress_thread.stop()
            # 不等待线程结束
            self.compress_thread = None

    def update_progress(self, data):
        """更新树形结构中的压缩进度"""
        # 查找对应的项目
        items = []
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            if item.text(0) == os.path.basename(data["file_name"]):
                items.append(item)
            iterator += 1

        if not items:
            return
            
        item = items[0]  # 使用找到的第一个匹配项
        
        # 如果是错误状态，只更新状态列
        if data.get("error"):
            item.setText(9, data["status"])
            return
        
        # 更新各列信息
        if "duration" in data:
            duration_str = f"{float(data['duration']):.2f} 秒" if data['duration'] != "未知" else "未知"
            item.setText(2, duration_str)
        if "original_size" in data:
            item.setText(3, format_size(data["original_size"]))
        if "original_bitrate" in data:
            item.setText(4, f"{data['original_bitrate']:.2f} Mbps")
        if "target_bitrate" in data:
            item.setText(5, f"{data['target_bitrate']:.2f} Mbps")
        
        # 如果是跳过压缩的情况，清空压缩后的信息列
        if data.get("skip_compression"):
            item.setText(6, "-")
            item.setText(7, "-")
        else:
            if "compressed_size" in data:
                compressed_size = data["compressed_size"]
                item.setText(6, format_size(compressed_size))
                if "original_size" in data:
                    # 计算并显示体积比例
                    ratio = compressed_size / data["original_size"]
                    ratio_text = f"{ratio:.1%}"
                    item.setText(7, ratio_text)
        
        # 更新影响程度列
        if "impact_level" in data:
            item.setText(8, data["impact_level"])
        
        # 更新状态列
        item.setText(9, data["status"])

    def compression_finished(self):
        """压缩完成后的处理"""
        self.source_path_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        # 移除文件夹刷新
        # self.update_file_list()

    def closeEvent(self, event):
        """程序关闭时保存设置"""
        # 检查压缩线程是否存在并且正在运行
        if hasattr(self, 'compress_thread') and self.compress_thread is not None:
            try:
                if self.compress_thread.isRunning():
                    self.compress_thread.stop()
                    self.compress_thread.wait()
            except Exception as e:
                print(f"停止压缩线程失败：{e}")
        
        # 保存设置
        self.save_settings()
        event.accept()

    def cleanup_temp_files(self):
        """清理所有临时文件"""
        if not hasattr(self, 'target_folder'):
            return
            
        # 查找并删除所有临时文件
        for root, dirs, files in os.walk(self.source_folder):
            for file in files:
                if "_temp." in file:  # 检查是否是临时文件
                    try:
                        temp_file_path = os.path.join(root, file)
                        if os.path.exists(temp_file_path):
                            os.remove(temp_file_path)
                            print(f"已删除临时文件：{temp_file_path}")
                    except Exception as e:
                        print(f"删除临时文件失败：{temp_file_path}, 错误：{e}")

    def on_coef_changed(self, new_value):
        """处理量化系数变化"""
        # 更新警告提示
        if new_value < 0.07 or new_value > 0.15:
            self.coef_warning.setText("警告：当前值超出推荐范围")
        else:
            self.coef_warning.setText("")
        
        if hasattr(self, 'compress_thread') and self.compress_thread is not None:
            self.compress_thread.update_quantization_coef(new_value)
        
        # 保存新的设置
        try:
            settings = {}
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
            
            settings['quantization_coef'] = new_value
            
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False)
        except Exception as e:
            print(f"保存设置失败：{e}")

    def on_replace_source_changed(self, state):
        """处理替换源文件选项变化"""
        try:
            settings = {}
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
            
            settings['replace_source'] = bool(state)
            
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False)
        except Exception as e:
            print(f"保存替换源文件设置失败：{e}")

    def moveEvent(self, event):
        """窗口移动时保存位置"""
        super().moveEvent(event)
        self.save_settings()

    def resizeEvent(self, event):
        """窗口大小改变时保存尺寸"""
        super().resizeEvent(event)
        self.save_settings()

    def handle_item_double_click(self, item, column):
        """处理树形项目双击事件"""
        # 获取存储的文件路径
        file_path = item.data(0, Qt.ItemDataRole.UserRole)  # 从第一列获取文件路径
        if not file_path:  # 如果是文件夹节点
            return
            
        if column in [0, 1]:  # 文件名列或缩略图列
            if os.path.exists(file_path):
                self.open_file(file_path)
        elif column == 6:  # 压缩后大小列
            base_name = os.path.splitext(file_path)[0]
            ext = os.path.splitext(file_path)[1]
            compressed_file = base_name + "_comp" + ext
            if os.path.exists(compressed_file):
                self.open_file(compressed_file)

    def open_file(self, file_path):
        """使用系统默认程序打开文件"""
        try:
            if platform.system() == 'Windows':
                os.startfile(file_path)
            elif platform.system() == 'Darwin':  # macOS
                subprocess.run(['open', file_path])
            else:  # Linux
                subprocess.run(['xdg-open', file_path])
        except Exception as e:
            print(f"打开文件失败：{e}")

    def toggle_expand_collapse(self):
        """切换展开/折叠状态"""
        # 检查第一个顶级项目的展开状态来决定操作
        is_expanded = False
        for i in range(self.tree.topLevelItemCount()):
            if self.tree.topLevelItem(i).isExpanded():
                is_expanded = True
                break
        
        # 根据当前状态执行相反操作
        if is_expanded:
            self.tree.collapseAll()
            self.expand_button.setText("展开全部")
        else:
            self.tree.expandAll()
            self.expand_button.setText("折叠全部")

    def toggle_thumbnails(self, state):
        """切换缩略图显示状态"""
        show_thumbnails = state == Qt.CheckState.Checked.value
        
        # 停止所有缩略图加载线程
        for thread in self.thumbnail_threads:
            thread.quit()
            thread.wait()
        self.thumbnail_threads.clear()
        
        # 调整缩略图列宽度
        self.tree.setColumnWidth(1, 120 if show_thumbnails else 0)
        
        if show_thumbnails:
            # 如果打开显示，检查并加载缺失的缩略图
            iterator = QTreeWidgetItemIterator(self.tree)
            while iterator.value():
                item = iterator.value()
                if not self.tree.itemWidget(item, 1):  # 如果没有缩略图
                    file_path = item.data(0, Qt.ItemDataRole.UserRole)
                    if file_path:  # 确保是文件项而不是文件夹
                        thread = ThumbnailLoader(file_path, item)
                        thread.thumbnail_ready.connect(self.set_thumbnail)
                        self.thumbnail_threads.append(thread)
                        thread.start()
                iterator += 1
        else:
            # 如果关闭显示，清除所有缩略图
            iterator = QTreeWidgetItemIterator(self.tree)
            while iterator.value():
                item = iterator.value()
                # 移除并删除缩略图控件
                widget = self.tree.itemWidget(item, 1)
                if widget:
                    self.tree.removeItemWidget(item, 1)
                    widget.deleteLater()  # 确保控件被正确删除
                iterator += 1
        
        # 保存设置
        self.save_settings()

def format_size(size_in_bytes):
    """格式化文件大小显示"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_in_bytes < 1024.0:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024.0
    return f"{size_in_bytes:.2f} PB"

if __name__ == "__main__":
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec()