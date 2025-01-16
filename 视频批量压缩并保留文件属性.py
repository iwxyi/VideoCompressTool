import os
import shutil
import time
import subprocess
import json
from PyQt6.QtWidgets import (QApplication, QMainWindow, QPushButton, QVBoxLayout, 
                            QHBoxLayout, QWidget, QFileDialog, QTableWidget, 
                            QTableWidgetItem, QLabel, QCheckBox, QHeaderView,
                            QDoubleSpinBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
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
        self.quantization_coef = quantization_coef  # 保存量化系数
        self.is_running = True
        self.current_process = None

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
                    
                    # 更新最终结果
                    progress_data.update({
                        "impact_level": impact_level,
                        "status": "完成"
                    })
                    self.progress_signal.emit(progress_data)
                    
                    # 完成所有分析后，如果启用了删除源文件选项，再删除源文件
                    if self.delete_source:
                        try:
                            os.remove(input_video_path)
                            print(f"已删除源文件：{input_video_path}")
                        except Exception as e:
                            print(f"删除源文件失败：{e}")
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

def format_size(size_in_bytes):
    """将字节大小转换为适当的单位（MB或GB）"""
    size_in_mb = size_in_bytes / (1024 * 1024)
    if size_in_mb >= 1024:
        return f"{size_in_mb/1024:.2f} GB"
    return f"{size_in_mb:.2f} MB"

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("视频批量压缩工具")
        self.setMinimumSize(800, 600)
        
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
        self.coef_spin.setRange(0.07, 0.15)
        self.coef_spin.setSingleStep(0.01)
        self.coef_spin.setDecimals(2)
        coef_layout.addWidget(coef_label)
        coef_layout.addWidget(self.coef_spin)
        coef_layout.addStretch()
        layout.addLayout(coef_layout)

        # 表格
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels([
            "文件名", "时长", "文件大小", "当前比特率",
            "目标比特率", "压缩后大小", "压缩比例", "影响程度", "状态"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        # 删除源文件选项（移到表格后面）
        self.delete_source_cb = QCheckBox("压缩后删除源文件")
        layout.addWidget(self.delete_source_cb)

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
        self.settings_file = "settings.json"
        self.load_settings()

        self.temp_files = []  # 用于跟踪临时文件

    def load_settings(self):
        """加载设置"""
        try:
            with open(self.settings_file, 'r', encoding='utf-8') as f:
                settings = json.load(f)
                self.source_folder = settings.get('last_folder', '')
                self.coef_spin.setValue(settings.get('quantization_coef', 0.12))
                if self.source_folder:
                    self.source_path_label.setText(f"源文件夹：{self.source_folder}")
                    self.update_file_list()
        except (FileNotFoundError, json.JSONDecodeError):
            self.source_folder = ''
            self.coef_spin.setValue(0.12)  # 默认值

    def save_settings(self):
        """保存设置"""
        settings = {
            'last_folder': self.source_folder,
            'quantization_coef': self.coef_spin.value()
        }
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False)
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
        files = []
        for root, dirs, filenames in os.walk(self.source_folder):
            for filename in filenames:
                if os.path.splitext(filename)[1].lower() in video_extensions:
                    file_path = os.path.join(root, filename)
                    mod_time = os.path.getmtime(file_path)
                    files.append((file_path, mod_time))

        # 按修改时间排序，最新的文件在前
        files.sort(key=lambda x: x[1], reverse=True)

        self.table.setRowCount(len(files))
        for i, (file_path, mod_time) in enumerate(files):
            self.table.setItem(i, 0, QTableWidgetItem(os.path.basename(file_path)))
            
            # 更新文件大小
            size_in_bytes = os.path.getsize(file_path)
            formatted_size = format_size(size_in_bytes)
            self.table.setItem(i, 2, QTableWidgetItem(formatted_size))
            
            # 初始化其他列为空
            self.table.setItem(i, 1, QTableWidgetItem(""))  # 时长
            self.table.setItem(i, 3, QTableWidgetItem(""))  # 当前比特率
            self.table.setItem(i, 4, QTableWidgetItem(""))  # 目标比特率
            self.table.setItem(i, 5, QTableWidgetItem(""))  # 压缩后大小
            self.table.setItem(i, 6, QTableWidgetItem(""))  # 压缩比例
            self.table.setItem(i, 7, QTableWidgetItem(""))  # 影响程度
            self.table.setItem(i, 8, QTableWidgetItem("等待压缩"))  # 状态

    def start_compression(self):
        if not self.source_folder:
            return
        
        self.source_path_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.coef_spin.setEnabled(False)  # 压缩时禁用量化系数调整
        
        self.compress_thread = VideoCompressThread(
            self.source_folder, 
            self.source_folder, 
            self.delete_source_cb.isChecked(),
            self.coef_spin.value()  # 传递量化系数
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
            self.coef_spin.setEnabled(True)
            
            # 更新正在压缩的文件状态为"停止压缩"
            for row in range(self.table.rowCount()):
                status_item = self.table.item(row, 8)
                if status_item and status_item.text() == "正在压缩":
                    self.table.setItem(row, 8, QTableWidgetItem("停止压缩"))
            
            # 停止压缩线程
            self.compress_thread.stop()
            # 不等待线程结束
            self.compress_thread = None

    def update_progress(self, data):
        """更新表格中的压缩进度"""
        items = self.table.findItems(data["file_name"], Qt.MatchFlag.MatchExactly)
        if not items:
            return
            
        row = items[0].row()
        
        # 如果是错误状态，只更新状态列
        if data.get("error"):
            self.table.setItem(row, 8, QTableWidgetItem(data["status"]))  # 状态列索引改为8
            return
        
        # 更新各列信息（所有列索引减1）
        if "duration" in data:
            duration_str = f"{float(data['duration']):.2f} 秒" if data['duration'] != "未知" else "未知"
            self.table.setItem(row, 1, QTableWidgetItem(duration_str))
        if "original_size" in data:
            self.table.setItem(row, 2, QTableWidgetItem(format_size(data["original_size"])))
        if "original_bitrate" in data:
            self.table.setItem(row, 3, QTableWidgetItem(f"{data['original_bitrate']:.2f} Mbps"))
        if "target_bitrate" in data:
            self.table.setItem(row, 4, QTableWidgetItem(f"{data['target_bitrate']:.2f} Mbps"))
        
        # 如果是跳过压缩的情况，清空压缩后的信息列
        if data.get("skip_compression"):
            self.table.setItem(row, 5, QTableWidgetItem("-"))
            self.table.setItem(row, 6, QTableWidgetItem("-"))
        else:
            if "compressed_size" in data:
                self.table.setItem(row, 5, QTableWidgetItem(format_size(data["compressed_size"])))
            if "compression_ratio" in data:
                self.table.setItem(row, 6, QTableWidgetItem(f"{data['compression_ratio']:.2%}"))
        
        # 更新影响程度列
        if "impact_level" in data:
            self.table.setItem(row, 7, QTableWidgetItem(data["impact_level"]))
        
        # 状态列移到最后
        self.table.setItem(row, 8, QTableWidgetItem(data["status"]))

    def compression_finished(self):
        self.source_path_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.coef_spin.setEnabled(True)  # 压缩完成后启用量化系数调整
        self.update_file_list()

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

if __name__ == "__main__":
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec()