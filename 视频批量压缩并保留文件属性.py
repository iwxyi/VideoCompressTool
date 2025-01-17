import os
import shutil
import time
import subprocess
import json
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QPushButton, QVBoxLayout,
    QWidget, QLabel, QFileDialog, QHBoxLayout, QSpinBox,
    QDoubleSpinBox, QCheckBox, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QStyle, QProgressBar, QMessageBox,
    QStatusBar, QTreeWidgetItemIterator
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QSize, QTimer
)
from PyQt6.QtGui import QColor, QPixmap
import platform
import datetime
import sqlite3  # 添加 sqlite3 导入


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

    def __init__(self, folder_path, target_folder, delete_source, quantization_coef, tree_widget):
        super().__init__()
        self.folder_path = folder_path
        self.target_folder = target_folder
        self.delete_source = delete_source
        self.quantization_coef = quantization_coef
        self.tree = tree_widget
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
        
        # 收集选中的文件
        def collect_checked_files(item):
            if item.checkState(0) != Qt.CheckState.Unchecked:  # 处理选中和部分选中的项目
                # 如果是文件
                if item.childCount() == 0:
                    file_path = item.data(0, Qt.ItemDataRole.UserRole)
                    if file_path and os.path.splitext(file_path)[1].lower() in video_extensions:
                        rel_path = os.path.relpath(os.path.dirname(file_path), self.folder_path)
                        files_to_process.append((file_path, rel_path))
                # 如果是文件夹，递归处理选中的子项目
                for i in range(item.childCount()):
                    collect_checked_files(item.child(i))

        # 从树形控件的根节点开始收集选中的文件
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            if item.parent() is None:  # 只处理顶层项目
                collect_checked_files(item)
            iterator += 1

        # 处理收集到的文件
        for file_path, rel_path in files_to_process:
            if not self.is_running:
                break

            try:
                # 检查文件是否存在
                if not os.path.exists(file_path):
                    print(f"文件不存在：{file_path}")
                    error_info = {
                        "file_name": os.path.basename(file_path),
                        "status": "文件不存在",
                        "error": True,
                        "compression_time": datetime.datetime.now().isoformat()
                    }
                    window = self.parent()
                    if window:
                        window.save_compression_history(file_path, error_info)
                    self.progress_signal.emit(error_info)
                    continue

                try:
                    file = os.path.basename(file_path)
                    input_video_path = file_path
                    
                    # 定义输出文件路径，保持原有目录结构
                    file_name_without_extension = os.path.splitext(file)[0]
                    file_extension = os.path.splitext(file)[1]
                    output_video_name = file_name_without_extension + "_comp" + file_extension
                    
                    # 创建目标子文件夹（如果不存在）
                    target_subfolder = os.path.join(self.target_folder, rel_path) if rel_path != '.' else self.target_folder
                    if not os.path.exists(target_subfolder):
                        os.makedirs(target_subfolder)
                    
                    output_video_path = os.path.join(target_subfolder, output_video_name)
                    
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
                    # 0.95 是比较合适的，但是 0.94 这种压缩后可能比例也就小 1%，不如多算一点
                    if current_bitrate and appropriate_bitrate >= current_bitrate * 0.9:
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
                        "file_path": file_path,  # 添加完整文件路径
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
                            '-movflags', '+faststart',  # 添加 faststart 标志以支持流媒体和快速预览
                            '-tag:v', 'avc1',  # 使用 avc1 标签代替 H264，提高兼容性
                            '-progress', 'pipe:1',  # 输出进度到管道
                            '-nostats',  # 禁用默认统计信息
                            '-loglevel', 'error',  # 只显示错误信息
                            '-y',  # 自动覆盖
                            '-pix_fmt', 'yuv420p',  # 使用更通用的像素格式
                            output_video_path
                        ]
                        creation_flags = subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0
                        self.current_process = subprocess.Popen(
                            command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL,
                            universal_newlines=True,
                            creationflags=creation_flags,
                            bufsize=1
                        )

                        # 读取进度信息
                        last_progress_time = time.time()
                        while self.current_process.poll() is None and self.is_running:
                            # 使用select来实现非阻塞读取
                            if platform.system() != 'Windows':
                                import select
                                reads, _, _ = select.select([self.current_process.stdout], [], [], 0.1)
                                if not reads:
                                    # 检查是否超过30秒没有进度更新
                                    if time.time() - last_progress_time > 30:
                                        print("压缩进程可能已经卡住，正在终止...")
                                        self.current_process.terminate()
                                        break
                                    continue
                            
                            line = self.current_process.stdout.readline()
                            if not line and self.current_process.poll() is not None:
                                break
                            
                            if line:
                                last_progress_time = time.time()
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

                        # 检查进程是否正常结束
                        return_code = self.current_process.poll()
                        if return_code is None:
                            self.current_process.terminate()
                            print("压缩进程被终止")
                            return
                        elif return_code != 0:
                            stderr_output = self.current_process.stderr.read()
                            print(f"压缩失败，错误码：{return_code}，错误信息：{stderr_output}")
                            progress_data.update({"status": "压缩失败"})
                            self.progress_signal.emit(progress_data)
                            return

                        if not self.is_running:
                            if os.path.exists(output_video_path):
                                os.remove(output_video_path)
                            return

                        # 检查压缩结果
                        if os.path.exists(output_video_path):
                            output_video_size = os.path.getsize(output_video_path)
                            end_time = time.time()
                            
                            # 更新状态为"计算SSIM中"
                            progress_data.update({
                                "compressed_size": output_video_size,
                                "compression_ratio": output_video_size / input_video_size,
                                "time_taken": end_time - start_time,
                                "status": "计算SSIM中"
                            })
                            self.progress_signal.emit(progress_data)
                            
                            # 计算SSIM并获取带数值的影响程度描述
                            ssim = self.calculate_ssim(input_video_path, output_video_path)
                            impact_level = self.get_impact_level(ssim)

                            # 保存压缩信息
                            window = self.parent()
                            if window:
                                compression_info = {
                                    "file_name": os.path.basename(file_path),
                                    "duration": progress_data.get("duration"),
                                    "original_size": input_video_size,
                                    "original_bitrate": current_bitrate / 1024 / 1024 if current_bitrate else 0,
                                    "target_bitrate": appropriate_bitrate / 1024 / 1024,
                                    "compressed_size": output_video_size,
                                    "compression_ratio": output_video_size / input_video_size,
                                    "impact_level": impact_level,
                                    "status": "完成",
                                    "compression_time": datetime.datetime.now().isoformat()
                                }
                                window.save_compression_history(file_path, compression_info)
                            
                            # 更新状态为"复制属性中"
                            progress_data.update({
                                "status": "复制属性中"
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

                except Exception as e:
                    print(f"压缩视频失败：{e}")
                    error_info = {
                        "file_name": os.path.basename(file_path),
                        "status": f"压缩失败：{str(e)}",
                        "error": True,
                        "compression_time": datetime.datetime.now().isoformat()
                    }
                    window = self.parent()
                    if window:
                        window.save_compression_history(file_path, error_info)
                    self.progress_signal.emit(error_info)
                    continue

            except Exception as e:
                print(f"处理文件失败：{e}")
                error_info = {
                    "file_name": os.path.basename(file_path),
                    "status": f"处理失败：{str(e)}",
                    "error": True,
                    "compression_time": datetime.datetime.now().isoformat()
                }
                # 立即保存错误信息
                window = self.parent()
                if window:
                    window.save_compression_history(file_path, error_info)
                else:
                    print(f"无法保存错误信息：window is None")
                
                self.progress_signal.emit(error_info)
                continue

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
            # 使用ffmpeg复制所有元数据和流信息，但排除可能导致问题的数据流
            command = [
                'ffmpeg', '-i', input_path,  # 输入为原始文件
                '-i', output_path,  # 压缩后的文件
                '-map', '1:v',  # 使用第二个输入的视频流（压缩后的）
                '-map', '1:a?',  # 复制所有音频流（如果存在）
                '-map', '0:s?',  # 从原始文件复制字幕流（如果存在）
                '-map_metadata', '0',  # 使用第一个输入的元数据
                '-metadata', f'creation_time={time.strftime("%Y-%m-%dT%H:%M:%S.000000Z")}',  # 保持创建时间
                '-movflags', '+faststart+use_metadata_tags',  # 优化元数据处理
                '-c', 'copy',  # 仅复制，不重新编码
                '-y',  # 覆盖输出文件
                '-ignore_unknown',  # 忽略未知流
                f"{os.path.splitext(output_path)[0]}_temp{os.path.splitext(output_path)[1]}"  # 临时文件
            ]
            
            # 执行命令
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"ffmpeg命令执行失败：{result.stderr}")
                # 尝试使用更简单的方式重试
                return self.simple_copy_metadata(input_path, output_path)
            
            # 替换原文件
            try:
                temp_path = f"{os.path.splitext(output_path)[0]}_temp{os.path.splitext(output_path)[1]}"
                
                # 在Windows系统中，需要先删除目标文件
                if platform.system() == 'Windows' and os.path.exists(output_path):
                    os.remove(output_path)
                
                os.rename(temp_path, output_path)
                
                # 复制文件时间属性和其他文件系统属性
                shutil.copystat(input_path, output_path)
                
                return True
            except Exception as e:
                print(f"替换文件失败：{e}")
                return False
                
        except Exception as e:
            print(f"复制元数据失败：{e}")
            return self.simple_copy_metadata(input_path, output_path)
        finally:
            # 清理可能存在的临时文件
            try:
                temp_path = f"{os.path.splitext(output_path)[0]}_temp{os.path.splitext(output_path)[1]}"
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception as e:
                print(f"清理临时文件失败：{e}")

    def simple_copy_metadata(self, input_path, output_path):
        """使用更简单的方式复制元数据（作为备选方案）"""
        try:
            command = [
                'ffmpeg', '-i', input_path,
                '-i', output_path,
                '-map', '1:v',
                '-map', '1:a?',
                '-map_metadata', '0',
                '-c', 'copy',
                '-y',
                f"{os.path.splitext(output_path)[0]}_temp{os.path.splitext(output_path)[1]}"
            ]
            
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode == 0:
                temp_path = f"{os.path.splitext(output_path)[0]}_temp{os.path.splitext(output_path)[1]}"
                if platform.system() == 'Windows' and os.path.exists(output_path):
                    os.remove(output_path)
                os.rename(temp_path, output_path)
                shutil.copystat(input_path, output_path)
                return True
                
            return False
        except Exception as e:
            print(f"简单复制元数据失败：{e}")
            return False

    def save_compression_history(self, file_path, compression_info):
        """保存压缩历史到SQLite数据库"""
        try:
            # 如果是文件不存在或无需压缩的状态，不保存
            if compression_info.get('status') in ["文件不存在", "无需压缩"]:
                return

            cursor = self.conn.cursor()
            
            # 检查是否已存在记录
            cursor.execute('SELECT status FROM compression_history WHERE file_path = ?', (file_path,))
            existing_record = cursor.fetchone()
            
            # 如果记录存在且状态为"完成"，只在新状态也是"完成"时才更新
            if existing_record and existing_record[0] == "完成" and compression_info.get('status') != "完成":
                return

            # 准备数据
            data = {
                'file_path': file_path,
                'file_name': os.path.basename(file_path),
                'duration': compression_info.get('duration', ''),
                'original_size': compression_info.get('original_size', 0),
                'original_bitrate': compression_info.get('original_bitrate', 0),
                'target_bitrate': compression_info.get('target_bitrate', 0),
                'compressed_size': compression_info.get('compressed_size', 0),
                'compression_ratio': compression_info.get('compression_ratio', 0),
                'impact_level': compression_info.get('impact_level', ''),
                'status': compression_info.get('status', ''),
                'compression_time': datetime.datetime.now().isoformat()
            }

            # 清理空值和0值
            data = {k: v for k, v in data.items() if v not in [None, '', 0]}

            # 构建SQL语句
            fields = ', '.join(data.keys())
            placeholders = ', '.join(['?' for _ in data])
            values = tuple(data.values())

            # 使用REPLACE语法进行插入或更新
            sql = f'REPLACE INTO compression_history ({fields}) VALUES ({placeholders})'
            cursor.execute(sql, values)
            self.conn.commit()

        except Exception as e:
            print(f"保存压缩历史失败：{e}")

    def load_compression_history(self):
        """从SQLite数据库加载压缩历史"""
        try:
            cursor = self.conn.cursor()
            cursor.execute('SELECT * FROM compression_history')
            rows = cursor.fetchall()
            
            # 转换为字典格式
            history = {}
            columns = [description[0] for description in cursor.description]
            
            for row in rows:
                record = dict(zip(columns, row))
                file_path = record.pop('file_path')  # 移除并获取文件路径
                history[file_path] = record
            
            return history
        except Exception as e:
            print(f"加载压缩历史失败：{e}")
            return {}

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

class VideoInfoWorker(QThread):
    """异步获取视频信息的工作线程"""
    info_ready = pyqtSignal(str)
    
    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path
        
    def run(self):
        try:
            # 获取文件大小
            file_size = os.path.getsize(self.file_path)
            size_str = self.format_size(file_size)
            
            command = [
                'ffprobe',
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height,r_frame_rate,duration,bit_rate',
                '-of', 'json',
                self.file_path
            ]
            
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode == 0:
                info = json.loads(result.stdout)
                if 'streams' in info and info['streams']:
                    stream = info['streams'][0]
                    
                    # 获取分辨率
                    width = stream.get('width', 'N/A')
                    height = stream.get('height', 'N/A')
                    resolution = f"{width}x{height}" if width != 'N/A' else 'N/A'
                    
                    # 获取帧率
                    fps = 'N/A'
                    if 'r_frame_rate' in stream:
                        try:
                            num, den = map(int, stream['r_frame_rate'].split('/'))
                            fps = f"{num/den:.2f}"
                        except:
                            pass
                    
                    # 获取时长
                    duration = 'N/A'
                    if 'duration' in stream:
                        try:
                            duration = f"{float(stream['duration']):.2f}"
                        except:
                            pass
                            
                    # 获取比特率
                    bitrate = 'N/A'
                    if 'bit_rate' in stream:
                        try:
                            bitrate = f"{int(stream['bit_rate'])/1024/1024:.2f}"
                        except:
                            pass
                    
                    # 生成信息文本
                    info_text = f"分辨率: {resolution} | 帧率: {fps} fps | 时长: {duration}s | 大小: {size_str} | 比特率: {bitrate} Mbps"
                    self.info_ready.emit(info_text)
                else:
                    self.info_ready.emit("无法获取视频信息")
        except Exception as e:
            print(f"获取视频信息失败：{e}")
            self.info_ready.emit("获取视频信息失败")
    
    def format_size(self, size):
        """格式化文件大小显示"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # 初始化数据库
        self.init_database()
        # 先定义所有需要的方法
        self.init_methods()
        
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
        self.tree.setHeaderLabels(['文件'])
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        # 启用复选框
        self.tree.setColumnCount(1)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        # 添加这一行来启用复选框
        self.tree.setItemsExpandable(True)
        self.tree.setAlternatingRowColors(True)
        
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
        
        # 添加全选按钮
        self.select_all_button = QPushButton("全选")
        self.select_all_button.clicked.connect(self.select_all_items)
        self.select_all_button.setFixedWidth(60)
        options_layout.addWidget(self.select_all_button)
        
        # 添加反选按钮
        self.invert_selection_button = QPushButton("反选")
        self.invert_selection_button.clicked.connect(self.invert_selection)
        self.invert_selection_button.setFixedWidth(60)
        options_layout.addWidget(self.invert_selection_button)
        
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

        # 最后再加载设置
        self.load_settings()

        self.temp_files = []  # 用于跟踪临时文件

        # 设置树形控件接收键盘事件
        self.tree.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # 连接键盘事件处理函数
        self.tree.keyPressEvent = self.tree_key_press_event

        # 添加状态栏
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        
        # 创建状态栏的标签
        self.video_info_label = QLabel()
        self.selection_info_label = QLabel()
        
        # 添加标签到状态栏
        self.statusBar.addWidget(self.video_info_label)
        self.statusBar.addPermanentWidget(self.selection_info_label)
        
        # 连接树形控件的选择变化信号
        self.tree.itemSelectionChanged.connect(self.update_status_bar)
        self.tree.itemChanged.connect(self.update_selection_count)

        # 用于存储当前的视频信息工作线程
        self.current_info_worker = None

    def init_methods(self):
        """初始化所有需要的方法"""
        def save_compression_history(self, file_path, compression_info):
            """保存压缩历史到SQLite数据库"""
            try:
                # 如果是文件不存在或无需压缩的状态，不保存
                if compression_info.get('status') in ["文件不存在", "无需压缩"]:
                    return

                cursor = self.conn.cursor()
                
                # 检查是否已存在记录
                cursor.execute('SELECT status FROM compression_history WHERE file_path = ?', (file_path,))
                existing_record = cursor.fetchone()
                
                # 如果记录存在且状态为"完成"，只在新状态也是"完成"时才更新
                if existing_record and existing_record[0] == "完成" and compression_info.get('status') != "完成":
                    return

                # 准备数据
                data = {
                    'file_path': file_path,
                    'file_name': os.path.basename(file_path),
                    'duration': compression_info.get('duration', ''),
                    'original_size': compression_info.get('original_size', 0),
                    'original_bitrate': compression_info.get('original_bitrate', 0),
                    'target_bitrate': compression_info.get('target_bitrate', 0),
                    'compressed_size': compression_info.get('compressed_size', 0),
                    'compression_ratio': compression_info.get('compression_ratio', 0),
                    'impact_level': compression_info.get('impact_level', ''),
                    'status': compression_info.get('status', ''),
                    'compression_time': datetime.datetime.now().isoformat()
                }

                # 清理空值和0值
                data = {k: v for k, v in data.items() if v not in [None, '', 0]}

                # 构建SQL语句
                fields = ', '.join(data.keys())
                placeholders = ', '.join(['?' for _ in data])
                values = tuple(data.values())

                # 使用REPLACE语法进行插入或更新
                sql = f'REPLACE INTO compression_history ({fields}) VALUES ({placeholders})'
                cursor.execute(sql, values)
                self.conn.commit()

            except Exception as e:
                print(f"保存压缩历史失败：{e}")

        def load_compression_history(self):
            """从SQLite数据库加载压缩历史"""
            try:
                cursor = self.conn.cursor()
                cursor.execute('SELECT * FROM compression_history')
                rows = cursor.fetchall()
                
                # 转换为字典格式
                history = {}
                columns = [description[0] for description in cursor.description]
                
                for row in rows:
                    record = dict(zip(columns, row))
                    file_path = record.pop('file_path')  # 移除并获取文件路径
                    history[file_path] = record
                
                return history
            except Exception as e:
                print(f"加载压缩历史失败：{e}")
                return {}

        # 将方法绑定到实例
        self.save_compression_history = save_compression_history.__get__(self)
        self.load_compression_history = load_compression_history.__get__(self)

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
            
            # 保存树形控件的状态
            tree_state = {
                'expanded': [],  # 展开的节点路径列表
                'checked': [],   # 选中的节点路径列表
                'partially_checked': []  # 部分选中的节点路径列表
            }
            
            def save_item_state(item):
                item_path = item.data(0, Qt.ItemDataRole.UserRole)
                if not item_path:
                    return
                    
                # 保存展开状态
                if item.isExpanded():
                    tree_state['expanded'].append(item_path)
                
                # 保存选中状态
                check_state = item.checkState(0)
                if check_state == Qt.CheckState.Checked:
                    tree_state['checked'].append(item_path)
                elif check_state == Qt.CheckState.PartiallyChecked:
                    tree_state['partially_checked'].append(item_path)
                
                # 递归处理子项目
                for i in range(item.childCount()):
                    save_item_state(item.child(i))
            
            # 遍历所有顶层项目
            for i in range(self.tree.topLevelItemCount()):
                save_item_state(self.tree.topLevelItem(i))
            
            settings.update({
                'last_folder': self.source_folder,
                'quantization_coef': self.coef_spin.value(),
                'replace_source': self.replace_source_cb.isChecked(),
                'show_thumbnail': self.show_thumbnail_cb.isChecked(),
                'tree_state': tree_state,
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

    def save_tree_state(self):
        """单独保存树形控件的状态到tree_state.json"""
        try:
            tree_state = {
                'expanded': [],  # 展开的节点路径列表
                'checked': [],   # 选中的节点路径列表
                'partially_checked': [],  # 部分选中的节点路径列表
                'scroll_position': {  # 添加滚动位置
                    'horizontal': self.tree.horizontalScrollBar().value(),
                    'vertical': self.tree.verticalScrollBar().value()
                }
            }
            
            # 遍历所有项目（包括子项目）
            iterator = QTreeWidgetItemIterator(self.tree)
            while iterator.value():
                item = iterator.value()
                item_path = item.data(0, Qt.ItemDataRole.UserRole)
                if item_path:
                    # 保存展开状态
                    if item.isExpanded():
                        tree_state['expanded'].append(item_path)
                    
                    # 保存选中状态
                    check_state = item.checkState(0)
                    if check_state == Qt.CheckState.Checked:
                        tree_state['checked'].append(item_path)
                    elif check_state == Qt.CheckState.PartiallyChecked:
                        tree_state['partially_checked'].append(item_path)
                
                iterator += 1
            
            # 保存到单独的文件
            with open('tree_state.json', 'w', encoding='utf-8') as f:
                json.dump(tree_state, f, ensure_ascii=False, indent=4)
                print(f"保存树形控件状态：展开 {len(tree_state['expanded'])} 项，"
                      f"选中 {len(tree_state['checked'])} 项，"
                      f"部分选中 {len(tree_state['partially_checked'])} 项，"
                      f"滚动位置 {tree_state['scroll_position']}")
            
        except Exception as e:
            print(f"保存树形控件状态失败：{e}")

    def restore_tree_state(self):
        """从tree_state.json恢复树形控件的状态"""
        try:
            if os.path.exists('tree_state.json'):
                with open('tree_state.json', 'r', encoding='utf-8') as f:
                    tree_state = json.load(f)
                    expanded_paths = set(tree_state.get('expanded', []))
                    checked_paths = set(tree_state.get('checked', []))
                    partially_checked_paths = set(tree_state.get('partially_checked', []))
                    scroll_position = tree_state.get('scroll_position', {'horizontal': 0, 'vertical': 0})
                    
                    print(f"正在恢复树形控件状态：展开 {len(expanded_paths)} 项，"
                          f"选中 {len(checked_paths)} 项，"
                          f"部分选中 {len(partially_checked_paths)} 项，"
                          f"滚动位置 {scroll_position}")
                    
                    # 暂时阻止项目变化信号
                    self.tree.blockSignals(True)
                    
                    # 遍历所有项目（包括子项目）
                    iterator = QTreeWidgetItemIterator(self.tree)
                    while iterator.value():
                        item = iterator.value()
                        item_path = item.data(0, Qt.ItemDataRole.UserRole)
                        
                        if item_path:
                            # 恢复展开状态
                            if item_path in expanded_paths:
                                item.setExpanded(True)
                            
                            # 恢复选中状态
                            if item_path in checked_paths:
                                item.setCheckState(0, Qt.CheckState.Checked)
                            elif item_path in partially_checked_paths:
                                item.setCheckState(0, Qt.CheckState.PartiallyChecked)
                            else:
                                item.setCheckState(0, Qt.CheckState.Unchecked)
                        
                        iterator += 1
                    
                    # 恢复信号
                    self.tree.blockSignals(False)
                    
                    # 恢复滚动位置
                    QTimer.singleShot(100, lambda: self.restore_scroll_position(scroll_position))
                    
                    # 更新展开/折叠按钮的文本
                    any_expanded = False
                    iterator = QTreeWidgetItemIterator(self.tree)
                    while iterator.value():
                        if iterator.value().isExpanded():
                            any_expanded = True
                            break
                        iterator += 1
                    
                    self.expand_button.setText("折叠全部" if any_expanded else "展开全部")
                    
                    # 使用QTimer延迟更新选中数量，确保在UI更新后执行
                    QTimer.singleShot(200, self.update_selection_count)
                    
        except Exception as e:
            print(f"恢复树形控件状态失败：{e}")

    def restore_scroll_position(self, scroll_position):
        """恢复滚动位置"""
        try:
            self.tree.horizontalScrollBar().setValue(scroll_position.get('horizontal', 0))
            self.tree.verticalScrollBar().setValue(scroll_position.get('vertical', 0))
        except Exception as e:
            print(f"恢复滚动位置失败：{e}")

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
            "缩略图",
            "时长", "文件大小", "当前比特率",
            "目标比特率", "压缩后大小", "体积比例", "影响程度", "状态"
        ]
        
        self.tree.setColumnCount(len(headers))
        self.tree.setHeaderLabels(headers)
        
        # 设置图标大小
        icon_size = QSize(16, 16)  # 设置为16x16像素
        self.tree.setIconSize(icon_size)
        
        # 设置文件名列的默认宽度和其他列的自适应
        self.tree.setColumnWidth(0, 400)  # 设置文件名列宽为400像素
        self.tree.setColumnWidth(1, 120 if self.show_thumbnail_cb.isChecked() else 0)  # 缩略图列
        
        # 其他列自适应内容
        for i in range(2, self.tree.columnCount()):
            self.tree.header().setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        
        # 第一列（文件夹/文件名）可以伸展
        self.tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Interactive
        )
        
        # 加载压缩历史
        compression_history = self.load_compression_history()
        
        def add_items_recursively(parent_path, parent_item=None):
            items = sorted(os.listdir(parent_path))
            for item_name in items:
                item_path = os.path.join(parent_path, item_name)
                
                # 创建新项目
                if parent_item is None:
                    tree_item = QTreeWidgetItem(self.tree)
                else:
                    tree_item = QTreeWidgetItem(parent_item)
                
                # 设置项目文本和数据
                tree_item.setText(0, item_name)
                tree_item.setData(0, Qt.ItemDataRole.UserRole, item_path)
                
                # 启用复选框
                tree_item.setFlags(tree_item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                tree_item.setCheckState(0, Qt.CheckState.Unchecked)
                
                if os.path.isdir(item_path):
                    # 文件夹处理代码...
                    folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
                    tree_item.setIcon(0, folder_icon)
                    add_items_recursively(item_path, tree_item)
                else:
                    # 只处理视频文件
                    if os.path.splitext(item_name)[1].lower() in video_extensions:
                        # 设置文件图标
                        file_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
                        tree_item.setIcon(0, file_icon)
                        
                        # 从历史记录中恢复信息
                        if item_path in compression_history:
                            history = compression_history[item_path]
                            # 恢复所有表格字段
                            tree_item.setText(2, str(history.get('duration', '')))  # 时长
                            tree_item.setText(3, format_size(history.get('original_size', 0)))  # 原始大小
                            tree_item.setText(4, f"{history.get('original_bitrate', 0):.2f}Mbps")  # 原始比特率
                            tree_item.setText(5, f"{history.get('target_bitrate', 0):.2f}Mbps")  # 目标比特率
                            
                            if history.get('compressed_size'):
                                tree_item.setText(6, format_size(history.get('compressed_size')))  # 压缩后大小
                            
                            if history.get('compression_ratio'):
                                ratio = history.get('compression_ratio')
                                tree_item.setText(7, f"{ratio*100:.1f}%")  # 压缩比例
                            
                            tree_item.setText(8, history.get('impact_level', ''))  # 影响程度
                            tree_item.setText(9, history.get('status', '等待压缩'))  # 状态
                            
                            # 如果压缩已完成，设置文本颜色为灰色
                            if history.get('status') == '完成':
                                for col in range(tree_item.columnCount()):
                                    tree_item.setForeground(col, QColor(128, 128, 128))
                        else:
                            # 新文件，设置初始状态为空
                            tree_item.setText(9, "")  # 修改这里，初始状态为空
                        
                        # 只在开关打开时加载缩略图
                        if self.show_thumbnail_cb.isChecked():
                            thread = ThumbnailLoader(item_path, tree_item)
                            thread.thumbnail_ready.connect(self.set_thumbnail)
                            self.thumbnail_threads.append(thread)
                            thread.start()
                    else:
                        # 移除非视频文件的项目
                        if parent_item:
                            parent_item.removeChild(tree_item)
                        else:
                            index = self.tree.indexOfTopLevelItem(tree_item)
                            self.tree.takeTopLevelItem(index)
        
        # 从源文件夹开始递归添加项目
        if self.source_folder:
            add_items_recursively(self.source_folder)
        
        # 连接项目变化信号
        self.tree.itemChanged.connect(self.on_item_changed)
        
        # 重新启用树形控件的更新
        self.tree.setUpdatesEnabled(True)
        
        # 更新完文件列表后，更新展开/折叠按钮的文本
        for i in range(self.tree.topLevelItemCount()):
            if self.tree.topLevelItem(i).isExpanded():
                self.expand_button.setText("折叠全部")
                break
        else:
            self.expand_button.setText("展开全部")

        # 在文件列表更新完成后恢复状态
        self.restore_tree_state()

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
        
        # 更新选中文件的状态为"等待压缩"
        def update_selected_items(item):
            if item.checkState(0) != Qt.CheckState.Unchecked:  # 处理选中和部分选中的项目
                # 如果是文件
                if item.childCount() == 0:
                    file_path = item.data(0, Qt.ItemDataRole.UserRole)
                    if file_path and os.path.splitext(file_path)[1].lower() in ['.mp4', '.avi', '.mov', '.mkv']:
                        item.setText(9, "等待压缩")
                # 如果是文件夹，递归处理选中的子项目
                for i in range(item.childCount()):
                    update_selected_items(item.child(i))
        
        # 从树形控件的根节点开始更新状态
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            if item.parent() is None:  # 只处理顶层项目
                update_selected_items(item)
            iterator += 1
        
        self.source_path_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        
        self.compress_thread = VideoCompressThread(
            self.source_folder, 
            self.source_folder, 
            self.replace_source_cb.isChecked(),
            self.coef_spin.value(),
            self.tree
        )
        self.compress_thread.setParent(self)
        self.compress_thread.progress_signal.connect(self.update_progress)
        self.compress_thread.finished_signal.connect(self.compression_finished)
        self.compress_thread.start()

    def stop_compression(self):
        if hasattr(self, 'compress_thread'):
            # 立即更新界面状态
            self.source_path_button.setEnabled(True)
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            
            # 更新正在压缩的文件状态为"停止压缩"
            iterator = QTreeWidgetItemIterator(self.tree)
            while iterator.value():
                item = iterator.value()
                cell_text = item.text(9)
                if cell_text == "正在压缩" or cell_text.startswith("正在压缩"):  # 匹配"正在压缩"和"正在压缩 XX%"
                    item.setText(9, "停止压缩")
                    current_file = item.data(0, Qt.ItemDataRole.UserRole)  # 这里获取的是完整路径
                    # 如果有临时文件，删除它
                    if current_file:
                        name, ext = os.path.splitext(current_file)
                        temp_file = f"{name}_comp{ext}"
                        try:
                            if os.path.exists(temp_file):
                                os.remove(temp_file)
                                print(f"已删除未完成的临时文件：{temp_file}")
                        except Exception as e:
                            print(f"删除临时文件失败：{e}")
                iterator += 1
            
            # 停止压缩线程
            self.compress_thread.stop()
            self.compress_thread = None

    def update_progress(self, data):
        """更新树形结构中的压缩进度"""
        # 查找对应的项目
        items = []
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            # 使用完整路径而不是仅文件名来匹配
            if item.data(0, Qt.ItemDataRole.UserRole) == data.get("file_path", ""):
                items.append(item)
            iterator += 1

        if not items:
            return
            
        item = items[0]  # 使用找到的第一个匹配项
        file_path = item.data(0, Qt.ItemDataRole.UserRole)  # 获取完整文件路径
        
        # 如果是错误状态，保存错误信息并返回
        if data.get("error"):
            item.setText(9, data["status"])
            self.save_compression_history(file_path, {
                "status": data["status"],
                "error": True,
                "compression_time": datetime.datetime.now().isoformat()
            })
            return

        # 更新各列信息并准备历史记录数据
        history_data = {
            "file_name": os.path.basename(file_path),
            "compression_time": datetime.datetime.now().isoformat()
        }
        
        # 更新并记录各项数据
        if "duration" in data:
            duration_str = f"{float(data['duration']):.2f}" if data['duration'] != "未知" else "未知"
            item.setText(2, f"{duration_str} 秒" if duration_str != "未知" else "未知")
            history_data["duration"] = duration_str
        
        if "original_size" in data:
            item.setText(3, format_size(data["original_size"]))
            history_data["original_size"] = data["original_size"]
        
        if "original_bitrate" in data:
            item.setText(4, f"{data['original_bitrate']:.2f} Mbps")
            history_data["original_bitrate"] = data["original_bitrate"]
        
        if "target_bitrate" in data:
            item.setText(5, f"{data['target_bitrate']:.2f} Mbps")
            history_data["target_bitrate"] = data["target_bitrate"]
        
        # 处理压缩后的信息
        if data.get("skip_compression"):
            item.setText(6, "-")
            item.setText(7, "-")
            history_data["skip_compression"] = True
        else:
            if "compressed_size" in data:
                compressed_size = data["compressed_size"]
                item.setText(6, format_size(compressed_size))
                history_data["compressed_size"] = compressed_size
                
                if "original_size" in data:
                    ratio = compressed_size / data["original_size"]
                    ratio_text = f"{ratio:.1%}"
                    item.setText(7, ratio_text)
                    history_data["compression_ratio"] = ratio
        
        # 更新影响程度
        if "impact_level" in data:
            item.setText(8, data["impact_level"])
            history_data["impact_level"] = data["impact_level"]
        
        # 更新状态
        item.setText(9, data["status"])
        history_data["status"] = data["status"]
        
        # 当压缩完成时保存历史记录
        if data["status"] in ["完成", "完成(属性复制失败)"]:
            self.save_compression_history(file_path, history_data)

    def compression_finished(self):
        """压缩完成后的处理"""
        self.source_path_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        # 移除文件夹刷新
        # self.update_file_list()

    def closeEvent(self, event):
        """程序关闭时的处理"""
        # 关闭数据库连接
        try:
            if hasattr(self, 'conn'):
                self.conn.close()
        except Exception as e:
            print(f"关闭数据库连接失败：{e}")
            
        # 保存其他设置
        self.save_tree_state()
        self.save_settings()
        event.accept()

    def cleanup_temp_files(self):
        """清理所有临时文件"""
        if not hasattr(self, 'source_folder') or not self.source_folder:
            return
        
        # 查找并删除所有临时文件
        for root, dirs, files in os.walk(self.source_folder):
            for file in files:
                # 检查是否是临时压缩文件（以_comp结尾的视频文件）
                name, ext = os.path.splitext(file)
                if (name.endswith('_comp') and 
                    ext.lower() in ['.mp4', '.avi', '.mov', '.mkv']):
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
                json.dump(settings, f, ensure_ascii=False, indent=4)
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
            if platform.system() == 'Darwin':  # macOS
                # 使用系统默认播放器打开
                subprocess.run(['open', file_path])
            elif platform.system() == 'Windows':
                os.startfile(file_path)
            else:  # Linux
                subprocess.run(['xdg-open', file_path])
            print(f"正在预览视频：{file_path}")
        except Exception as e:
            print(f"预览视频失败：{e}")

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

    def add_folder_to_tree(self, path, parent=None):
        """添加文件夹到树形控件，包含复选框"""
        if parent is None:
            item = QTreeWidgetItem(self.tree)
        else:
            item = QTreeWidgetItem(parent)
            
        item.setText(0, os.path.basename(path))
        item.setData(0, Qt.ItemDataRole.UserRole, path)
        # 设置复选框
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        item.setCheckState(0, Qt.CheckState.Checked)  # 默认选中
        
        if os.path.isdir(path):
            # 设置文件夹图标
            item.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
            for child in sorted(os.listdir(path)):
                child_path = os.path.join(path, child)
                if os.path.isfile(child_path):
                    if any(child.lower().endswith(ext) for ext in ['.mp4', '.avi', '.mov', '.mkv']):
                        child_item = QTreeWidgetItem(item)
                        child_item.setText(0, child)
                        child_item.setData(0, Qt.ItemDataRole.UserRole, child_path)
                        # 设置文件图标
                        child_item.setIcon(0, self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon))
                        # 设置复选框
                        child_item.setFlags(child_item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                        child_item.setCheckState(0, Qt.CheckState.Checked)  # 默认选中
                elif os.path.isdir(child_path):
                    self.add_folder_to_tree(child_path, item)

        return item

    def on_item_changed(self, item, column):
        """处理项目选中状态变化"""
        # 阻止信号以避免递归
        self.tree.blockSignals(True)
        
        # 如果改变的是选中状态
        if column == 0:
            # 获取当前选中状态
            check_state = item.checkState(0)
            
            # 更新所有子项目
            for i in range(item.childCount()):
                child = item.child(i)
                child.setCheckState(0, check_state)
            
            # 递归更新所有父项目的状态
            def update_parent_state(item):
                parent = item.parent()
                if parent:
                    # 检查所有兄弟项目的状态
                    all_checked = True
                    all_unchecked = True
                    partial_checked = False
                    
                    for i in range(parent.childCount()):
                        child_state = parent.child(i).checkState(0)
                        if child_state == Qt.CheckState.Checked:
                            all_unchecked = False
                        elif child_state == Qt.CheckState.Unchecked:
                            all_checked = False
                        elif child_state == Qt.CheckState.PartiallyChecked:
                            all_checked = False
                            all_unchecked = False
                            partial_checked = True
                            break
                    
                    # 设置父项目的状态
                    if all_checked:
                        parent.setCheckState(0, Qt.CheckState.Checked)
                    elif all_unchecked:
                        parent.setCheckState(0, Qt.CheckState.Unchecked)
                    else:
                        parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
                    
                    # 递归更新上层父项目
                    update_parent_state(parent)
            
            # 开始递归更新父项目状态
            update_parent_state(item)
        
        # 恢复信号
        self.tree.blockSignals(False)

        # 更新选中数量
        self.update_selection_count()

    def tree_key_press_event(self, event):
        """处理树形控件的键盘事件"""
        if event.key() == Qt.Key.Key_Space:
            # 获取当前选中的项目
            current_item = self.tree.currentItem()
            if current_item:
                file_path = current_item.data(0, Qt.ItemDataRole.UserRole)
                if file_path and os.path.isfile(file_path):
                    # 检查是否为视频文件
                    ext = os.path.splitext(file_path)[1].lower()
                    if ext in ['.mp4', '.avi', '.mov', '.mkv']:
                        self.preview_video(file_path)
        else:
            # 保持原有的键盘事件处理
            QTreeWidget.keyPressEvent(self.tree, event)

    def preview_video(self, file_path):
        """预览视频文件"""
        try:
            if platform.system() == 'Darwin':  # macOS
                # 使用系统默认播放器打开
                subprocess.run(['open', file_path])
            elif platform.system() == 'Windows':
                os.startfile(file_path)
            else:  # Linux
                subprocess.run(['xdg-open', file_path])
            print(f"正在预览视频：{file_path}")
        except Exception as e:
            print(f"预览视频失败：{e}")

    def select_all_items(self):
        """全选或取消全选所有项目"""
        self.tree.blockSignals(True)  # 暂时阻止信号以提高性能
        
        # 检查当前是否全部选中
        all_checked = True
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            if item.childCount() == 0:  # 只检查文件项目
                if item.checkState(0) != Qt.CheckState.Checked:
                    all_checked = False
                    break
            iterator += 1
        
        # 根据当前状态决定是全选还是取消全选
        new_state = Qt.CheckState.Unchecked if all_checked else Qt.CheckState.Checked
        
        def set_check_state(item, state):
            item.setCheckState(0, state)
            for i in range(item.childCount()):
                set_check_state(item.child(i), state)
        
        # 遍历所有顶层项目
        for i in range(self.tree.topLevelItemCount()):
            set_check_state(self.tree.topLevelItem(i), new_state)
        
        self.tree.blockSignals(False)  # 恢复信号
        # 手动触发一次更新
        if self.tree.topLevelItemCount() > 0:
            self.on_item_changed(self.tree.topLevelItem(0), 0)

    def invert_selection(self):
        """反选所有项目"""
        self.tree.blockSignals(True)  # 暂时阻止信号以提高性能
        
        def invert_check_state(item):
            # 只反选文件项目，不反选文件夹
            if item.childCount() == 0:  # 如果是文件
                current_state = item.checkState(0)
                new_state = Qt.CheckState.Unchecked if current_state == Qt.CheckState.Checked else Qt.CheckState.Checked
                item.setCheckState(0, new_state)
            
            # 递归处理子项目
            for i in range(item.childCount()):
                invert_check_state(item.child(i))
                
            # 如果是文件夹，根据子项目状态更新自身状态
            if item.childCount() > 0:
                checked_count = 0
                total_files = 0
                
                def count_files(folder_item):
                    nonlocal checked_count, total_files
                    for i in range(folder_item.childCount()):
                        child = folder_item.child(i)
                        if child.childCount() == 0:  # 如果是文件
                            total_files += 1
                            if child.checkState(0) == Qt.CheckState.Checked:
                                checked_count += 1
                        else:  # 如果是文件夹
                            count_files(child)
                
                count_files(item)
                
                if total_files > 0:
                    if checked_count == total_files:
                        item.setCheckState(0, Qt.CheckState.Checked)
                    elif checked_count == 0:
                        item.setCheckState(0, Qt.CheckState.Unchecked)
                    else:
                        item.setCheckState(0, Qt.CheckState.PartiallyChecked)
        
        # 遍历所有顶层项目
        for i in range(self.tree.topLevelItemCount()):
            invert_check_state(self.tree.topLevelItem(i))
        
        self.tree.blockSignals(False)  # 恢复信号

    def update_status_bar(self):
        """更新状态栏显示当前选中视频的信息"""
        # 停止当前正在运行的工作线程（如果有）
        if self.current_info_worker is not None:
            self.current_info_worker.quit()
            self.current_info_worker.wait()
            self.current_info_worker = None
        
        selected_items = self.tree.selectedItems()
        if not selected_items:
            self.video_info_label.setText("")
            return
            
        current_item = selected_items[0]
        if current_item.childCount() > 0:  # 如果是文件夹
            self.video_info_label.setText("")
            return
            
        file_path = current_item.data(0, Qt.ItemDataRole.UserRole)
        if not file_path or not os.path.exists(file_path):
            self.video_info_label.setText("")
            return
        
        # 创建并启动新的工作线程
        self.current_info_worker = VideoInfoWorker(file_path)
        self.current_info_worker.info_ready.connect(self.video_info_label.setText)
        self.current_info_worker.start()

    def update_selection_count(self, item=None, column=None):
        """更新选中视频数量显示"""
        checked_count = 0
        iterator = QTreeWidgetItemIterator(self.tree)
        while iterator.value():
            item = iterator.value()
            if item.childCount() == 0 and item.checkState(0) == Qt.CheckState.Checked:  # 只统计选中的文件
                checked_count += 1
            iterator += 1
        
        self.selection_info_label.setText(f"已选择: {checked_count} 个视频")

    def init_database(self):
        """初始化SQLite数据库"""
        try:
            self.conn = sqlite3.connect('compression_history.db')
            cursor = self.conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS compression_history (
                    file_path TEXT PRIMARY KEY,
                    file_name TEXT,
                    duration TEXT,
                    original_size INTEGER,
                    original_bitrate REAL,
                    target_bitrate REAL,
                    compressed_size INTEGER,
                    compression_ratio REAL,
                    impact_level TEXT,
                    status TEXT,
                    compression_time TEXT
                )
            ''')
            self.conn.commit()
        except Exception as e:
            print(f"初始化数据库失败：{e}")

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