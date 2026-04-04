# ECG Receiver Standalone 中文说明

这是一个面向 ESP32 + ADS1292R ECG 设备的实时心电可视化与 AI 辅助诊断项目。

如果你是第一次接触这个仓库，建议先阅读这份中文说明，再启动图形界面连接设备、查看实时波形，并检查结构化诊断结果。

## 项目仓库

源码地址：[GitHub 仓库](https://github.com/GanQiao1990/ecg_receiver_standalone-)

## 这个程序能做什么

- 显示实时心电图，带有临床风格网格和 10 秒滚动窗口。
- 显示心率、节律规则性、信号质量和波形幅度等关键指标。
- 将心电片段发送给 AI 诊断服务，生成结构化结论和建议。
- 将采集到的心电数据保存为 CSV 文件，方便后续分析。

## 我应该使用哪个界面？

如果你是新用户，建议优先使用现代 Tkinter 诊断界面。

- 现代 Tkinter 诊断界面：最适合医生查看实时波形和诊断摘要。
- Kivy 界面：适合希望获得更广泛跨平台兼容性的用户。
- 传统 PyQt 界面：保留兼容性用途，不建议新用户优先使用。

## 快速开始

### 1. 创建 Python 环境

建议使用 Python 3.8 及以上版本。最简单的方法是创建虚拟环境。

```bash
python -m venv .venv
source .venv/bin/activate
```

在 Windows 上，请使用 PowerShell 或命令提示符对应的激活命令。

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

如果你愿意，也可以直接运行启动脚本，让它自动补装缺失的 GUI 依赖。

### 3. 启动界面

现代诊断界面：

```bash
python launch_modern_gui.py
```

Kivy 界面：

```bash
python launch_kivy_gui.py
```

传统 PyQt 界面：

```bash
python -m ecg_receiver.main
```

## 新用户操作流程

1. 启动图形界面。
2. 选择心电设备对应的串口。
3. 点击 Connect，等待实时波形出现。
4. 如果需要 AI 诊断，输入 API Key 和 API URL。
5. 等待几秒钟后点击 Analyze ECG。
6. 如果希望自动分析，可启用 Auto Mode，每 30 秒自动分析一次。

不输入 API Key 也可以使用实时可视化功能。AI 诊断需要有效的 API Key 和网络连接。

## 医生能看到什么

现代诊断界面会把最重要的信息放在最容易阅读的位置：

- 10 秒实时心电图，带临床风格的纸样网格。
- 心率、节律标签、信号质量和波形幅度，直接显示在监视区旁边。
- 结构化诊断面板，包括严重程度、置信度、关键发现、即时建议、随访建议和临床备注。
- 诊断历史标签页，方便对比前后结果。

## 硬件要求

- ESP32 开发板。
- ADS1292R 心电前端或兼容的心电信号源。
- USB 连接线。
- 能把串口数据输出到电脑的心电固件。

默认串口设置通常适用于常见的演示固件：

- 波特率：57600
- 数据源：串口逐行输出

## 支持的数据格式

串口读取器支持两种常见格式：

### 标准 CSV 格式

```text
DATA,timestamp,ecg_value,resp_value,heart_rate,status
```

示例：

```text
DATA,1234567890,1024,512,75,OK
```

### 简单数字格式

每一行一个心电采样值：

```text
-7
-6
-5
1024
1050
```

如果你的固件使用其他格式，需要调整核心代码里的串口解析逻辑。

## AI 诊断设置

1. 打开现代诊断界面。
2. 输入 API Key。
3. 检查界面中的 API URL，必要时改成你服务商提供的地址。
4. 点击 Setup API。
5. 等待连接状态变为可用后再执行分析。

## 录制数据

点击 Start Recording 可以把接收到的心电数据保存为 CSV 文件。这样便于之后复查，也适合交给其他医生一起分析。

## 常见问题排查

### 没有串口可选

- 确认 ESP32 已经正确连接。
- 检查 USB 线是否支持数据传输，不只是充电。
- 安装对应开发板的 USB 驱动。
- 在 Linux 上，把当前用户加入 dialout 组，然后重新登录。

### 看不到心电数据

- 确认设备正在按预期波特率发送串口数据。
- 检查固件输出格式是否与上面支持的格式之一一致。
- 重新连接设备，并等待几秒钟让缓冲区填满。

### AI 诊断失败

- 检查 API Key 是否正确。
- 检查 API URL 是否正确。
- 确认电脑可以正常访问互联网。
- 确认已经收集到足够的心电数据后再执行分析。

### 现代界面无法启动

- 重新安装依赖：`pip install -r requirements.txt`
- 如果 Linux 系统缺少 Tk 支持，请安装发行版对应的 Tk 软件包。
- 如果你只想先看界面，可以改用 `python launch_kivy_gui.py`。

## 测试命令

下面这些命令可以帮助你检查项目是否正常：

```bash
python test_connection.py
python test_diagnosis.py
python validate_installation.py
```

## 项目结构

- `launch_modern_gui.py`：现代医生查看型 Tkinter 启动器。
- `launch_kivy_gui.py`：Kivy 启动器。
- `ecg_receiver/core`：串口处理、缓冲区、录制和性能监控。
- `ecg_receiver/gui_tkinter`：面向诊断的现代桌面界面。
- `ecg_receiver/gui_kivy`：轻量级 Kivy 界面。
- `ecg_receiver/gui`：传统 PyQt 界面。

## 安全说明

本软件是一个可视化与辅助判断工具，不能替代医生的临床判断，也不能替代紧急医疗救治。

## 建议的下一步

先启动现代 Tkinter 诊断界面，连接设备，确认实时波形和信号指标稳定后，再依赖 AI 诊断摘要。