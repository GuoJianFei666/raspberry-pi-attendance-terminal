#!/usr/bin/env python3
from gpiozero import TonalBuzzer
from time import sleep

# 初始化一个无源蜂鸣器对象，连接到BCM编码的17号引脚
tb = TonalBuzzer(17)

# 定义乐谱：每个元组包含（音名, 时长/秒）
# 音名支持如 'C4', 'D#4' 等形式
tune = [
    ('C4', 0.2), ('D4', 0.2), ('E4', 0.2), ('F4', 0.2),
    ('G4', 0.2), ('A4', 0.2), ('B4', 0.2), ('C5', 0.4),
    (None, 0.2),  # None 表示休止
    ('C5', 0.2), ('B4', 0.2), ('A4', 0.2), ('G4', 0.2),
    ('F4', 0.2), ('E4', 0.2), ('D4', 0.2), ('C4', 0.4),
]

def play_tune():
    for note, duration in tune:
        print(f"Playing: {note}")  # 打印当前播放的音符
        tb.play(note)  # 播放这个音符
        sleep(duration)  # 等待音符的时长
    tb.stop()  # 演奏完毕，停止

try:
    print("开始播放音乐...")
    play_tune()
    print("播放完毕。")

except KeyboardInterrupt:
    # 当按下 Ctrl+C 时，优雅地停止蜂鸣器
    tb.stop()
    print("\n播放被中断。")