import os
import shutil
import time
import subprocess


"""
使用公式估算比特率（仅供参考）
有一个简单的估算公式：比特率（Mbps）=（分辨率宽度 × 分辨率高度 × 帧率 × 量化系数）/（1024×1024）。
其中量化系数可以根据视频质量要求来选择，一般在 0.07 - 0.15 之间。
例如，对于一个 1920×1080、30fps 的视频，如果希望画质较好，选择量化系数为 0.12，那么比特率大约为（1920×1080×30×0.12）/（1024×1024）≈7.3Mbps。
返回单位 bps
"""
def estimate_appropriate_bitrate(input_video_path):
    # 获取视频的分辨率和帧率
    command = f'ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate -of csv=p=0 "{input_video_path}"'
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"获取视频信息失败：{result.stderr}")
        return 0
    
    width, height, frame_rate = result.stdout.strip().split(',')
    frame_rate = float(frame_rate.split('/')[0]) / float(frame_rate.split('/')[1])
    print(f"分辨率：{width}x{height}, 帧率：{frame_rate}")

    # 计算比特率
    bitrate = (int(width) * int(height) * frame_rate * 0.12)
    original_bitrate = os.path.getsize(input_video_path)
    print(f"预估比特率：{bitrate/1024/1024:.2f}Mbps，原比特率：{original_bitrate/1024/1024:.2f}Mbps")
    return bitrate


def compress_videos_in_folder(folder_path, target_folder, target_bitrate="1000k"):
    """
    在指定文件夹下批量压缩视频，并保留修改时间，压缩后删除原文件，新文件按规则命名
    :param folder_path: 原视频所在文件夹路径
    :param target_folder: 压缩后视频存放的目标文件夹路径
    :param target_bitrate: 初始目标码率，可根据需求调整，默认1000k
    """
    if not os.path.exists(target_folder):
        os.makedirs(target_folder)

    video_extensions = ['.mp4', '.avi', '.mov', '.mkv']

    for root, dirs, files in os.walk(folder_path):
        for file in files:
            file_extension = os.path.splitext(file)[1].lower()
            if file_extension in video_extensions:
                input_video_path = os.path.join(root, file)
                file_name_without_extension = os.path.splitext(file)[0]
                output_video_name = file_name_without_extension + "_comp" + file_extension
                temp_video_name = file_name_without_extension + "_temp" + file_extension
                output_video_path = os.path.join(target_folder, output_video_name)
                temp_video_path = os.path.join(target_folder, temp_video_name)
                input_video_size = os.path.getsize(input_video_path)
                start_time = time.time()
                print(f"正在压缩：{input_video_path}，原文件大小：{input_video_size / 1024 / 1024:.2f}MB")

                # 估计合适码率
                appropriate_bitrate = estimate_appropriate_bitrate(input_video_path)
                if appropriate_bitrate == 0:
                    print(f"无法获取视频信息，跳过压缩：{input_video_path}")
                    continue

                # 使用ffmpeg进行视频压缩，设置合适的目标码率
                try:
                    os.system(f'ffmpeg -i "{input_video_path}" -b:v {appropriate_bitrate} "{temp_video_path}"')
                except Exception as e:
                    print(f"压缩视频失败：{e}")
                    continue

                # 获取原文件的修改时间戳（秒级）
                file_modify_time = os.path.getmtime(input_video_path)
                # 将时间戳转换为时间元组，再格式化为适合文件系统的时间格式
                file_modify_time_struct = time.localtime(file_modify_time)
                file_modify_time_str = time.strftime('%Y%m%d%H%M%S', file_modify_time_struct)

                # 设置压缩后文件的修改时间
                try:
                    os.utime(temp_video_path, (file_modify_time, file_modify_time))
                except Exception as e:
                    print(f"设置压缩后文件的修改时间失败：{e}")

                # 尝试复制原文件的权限等属性（可选，根据实际情况使用）
                try:
                    shutil.copystat(input_video_path, temp_video_path)
                except Exception as e:
                    print(f"复制原文件属性失败：{e}")

                # 删除原文件
                # try:
                #     os.remove(input_video_path)
                # except Exception as e:
                #     print(f"删除原文件失败：{e}")

                # 将临时文件重命名为目标文件
                try:
                    os.rename(temp_video_path, output_video_path)
                except Exception as e:
                    print(f"重命名临时文件失败：{e}")

                # 输出压缩信息
                output_video_size = os.path.getsize(output_video_path)
                end_time = time.time()
                print(f"压缩完成：{output_video_path}，新大小：{output_video_size / 1024 / 1024:.2f}MB，"
                      f"压缩比例：{output_video_size / input_video_size:.2%}，耗时：{end_time - start_time:.2f}秒")


if __name__ == "__main__":
    source_folder = "/Users/iwxyi/Test/视频"  # 替换为实际原视频所在文件夹路径
    target_folder = "/Users/iwxyi/Test/视频"  # 替换为目标文件夹路径
    compress_videos_in_folder(source_folder, target_folder)