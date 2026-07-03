# ECG AI 智能心电诊断平台

面向 **ESP32 + ADS1292R** 医疗级心电采集的实时监护与 AI 辅助判读系统。适用于临床演示、科研验证与院前筛查场景。

> **重要声明**：本软件为可视化与决策支持工具，不能替代执业医师的临床判断，更不能替代急救医疗。

## 项目仓库

源码：[GitHub 仓库](https://github.com/GanQiao1990/ecg_receiver_standalone-)

## 核心能力

| 模块 | 说明 |
|------|------|
| 实时心电条带 | 10 秒滚动窗口，临床纸格风格网格 |
| 心率与节律 | 采样率校准 + R 峰检测 + 固件心率融合 |
| 信号质量 | 信噪比与基线漂移综合评估 |
| AI 判读 | 多模型 OpenAI 兼容 API，结构化中文报告 |
| 数据录制 | CSV 导出，便于复盘与科研 |

## v3.0 重要修复：心率计数偏差

早期版本默认 **250 Hz** 采样率，而 ADS1292R 固件常见输出为 **500 Hz**，会导致：

- 计算出的 RR 间期减半 → **心率显示约为真实值的 2 倍**
- 或 R 波与 T 波被重复计数 → 同样出现 2 倍偏差

**v3.0 已统一修复：**

1. 默认采样率改为 **500 Hz**（可在 Settings → Display 调整）
2. 根据串口时间戳与到达速率 **自动估算** 实际采样率
3. R 峰检测采用 **0.42 s 不应期 + 幅值优先 NMS**，抑制 T 波误检
4. CSV 格式中的 **固件心率字段** 与算法结果智能融合
5. 界面显示 **「心率来源」**（固件 / R 峰 / 融合），便于医生核查

## 推荐界面

新用户请使用 **现代 Tkinter 诊断界面**（临床向布局）：

```bash
python launch_modern_gui.py
```

传统 PyQt 界面（兼容保留）：

```bash
python -m ecg_receiver.main
```

## 环境准备

### 1. Python 环境

建议 Python 3.8+，使用虚拟环境：

```bash
python -m venv .venv
source .venv/bin/activate   # Linux / macOS
# Windows: .venv\Scripts\activate
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 启动

在仓库根目录（含 `launch_modern_gui.py`）执行：

```bash
python launch_modern_gui.py
```

Windows 若默认 Python 路径不对，请显式指定：

```powershell
& "D:\Users\dell\anaconda3\python.exe" launch_modern_gui.py
```

## 标准操作流程

### 第一步：连接硬件

1. ESP32 + ADS1292R 通过 USB 连接电脑  
2. 启动 `launch_modern_gui.py`  
3. 在 **Device Control** 选择串口 → **Connect**  
4. 等待实时波形出现  

### 第二步：确认采样与心率

1. 观察 **采样率** 指标（默认 500 Hz）  
2. 若心率仍异常，打开 **Settings → Display**，将 Sample Rate 改为固件实际值（125 / 250 / 500 / 1000）  
3. 查看 **心率来源**：  
   - **固件心率**：来自 `DATA,...,hr,...` 字段，最可靠  
   - **R 峰检测**：纯算法估计  
   - **融合**：两者一致时取平均  

### 第三步：配置 AI 判读（可选）

1. 在 **大模型与 API** 选择模型预设  
2. 填写 **API Key**、**API URL**、**Model ID**  
3. 点击 **Setup API**，等待状态变为已连接  
4. 填写患者年龄、性别、症状（可选，提升判读针对性）  

### 第四步：执行分析

1. 采集至少 **10 秒** 稳定波形  
2. 点击 **分析心电图**  
3. 在 **Current** 标签查看：严重程度、置信度、关键发现、即时建议、随访与临床备注  
4. 需要周期性分析时，开启 **Auto Mode**（默认每 30 秒）  

### 第五步：录制与导出

- **Start Recording**：保存原始心电 CSV  
- **Export Report**：导出单次 JSON/CSV/TXT 报告  
- **Export All History**：导出历史判读记录  

## 硬件与串口

| 项目 | 默认值 |
|------|--------|
| 主控 | ESP32 |
| 前端 | ADS1292R |
| 波特率 | 57600 |
| 推荐采样率 | 500 Hz |

## 支持的数据格式

### 标准 CSV（推荐）

```text
DATA,timestamp_ms,ecg_value,resp_value,heart_rate,status
```

示例：

```text
DATA,1234567890,1024,512,75,OK
```

字段说明：

- `timestamp_ms`：用于自动估算采样率  
- `ecg_value`：心电采样值  
- `heart_rate`：固件计算心率（30–220 范围内自动采用）  

### 简单数值格式

每行一个采样值：

```text
-7
1024
1050
```

## 界面布局说明

```
┌─────────────────────────────────────────────────────────────┐
│  ECG AI 智能心电诊断 · 实时监护 · 采样率校准 · 大模型判读    │
├──────────────────────────────┬──────────────────────────────┤
│  实时心电条带（10s 滚动）      │  大模型与 API / 患者信息      │
│  心率 | 节律 | 质量 | 采样率   │  判读控制 / 结构化报告        │
│  设备连接 / 录制              │  历史记录 / 心电统计          │
└──────────────────────────────┴──────────────────────────────┘
```

## 设置项说明（Settings）

| 标签 | 关键参数 | 建议 |
|------|----------|------|
| Serial | 波特率、数据位 | 与固件一致 |
| Display | Sample Rate (Hz) | ADS1292R 通常 500 |
| Display | Time Window | 默认 10 秒 |
| Diagnosis | Auto Interval | 自动判读间隔（秒） |
| Data | Recording Directory | CSV 保存路径 |

## 常见问题

### 心率约为真实值 2 倍

1. 打开 Settings，将 Sample Rate 设为固件实际值（优先试 **500**）  
2. 若固件输出 `heart_rate` 字段，确认 CSV 格式正确  
3. 查看「心率来源」是否为 R 峰误检，必要时改善电极接触  

### 心率约为真实值 1/2

将 Sample Rate **调高**（例如 250 → 500）  

### 无串口

- 检查 USB 数据线是否支持数据传输  
- 安装 CH340 / CP210x 等驱动  
- Linux：`sudo usermod -aG dialout $USER` 后重新登录  

### 无波形

- 确认波特率 57600  
- 确认固件输出格式  
- 重新 Connect 并等待 5–10 秒  

### AI 判读失败

- 检查 API Key 与 URL  
- 确认网络可达  
- 确保已采集足够数据（建议 ≥10 s）  

### 界面无法启动

```bash
pip install -r requirements.txt
python -m py_compile launch_modern_gui.py
```

Linux 需安装 Tk：`sudo apt install python3-tk`

## 项目结构

```
ecg_artificial_intelligence/
├── launch_modern_gui.py          # 现代诊断界面入口
├── ecg_receiver/
│   ├── core/
│   │   ├── ecg_signal.py         # 采样率校准、R峰、心率（核心）
│   │   ├── llm_diagnosis.py      # 多模型 AI 判读
│   │   ├── serial_handler.py     # 串口通信
│   │   └── circular_buffer.py    # 环形缓冲
│   └── gui_tkinter/              # 临床诊断 UI
└── README_CN.md                  # 本说明
```

## 验证命令

```bash
python -m py_compile ecg_receiver/core/ecg_signal.py
python -m py_compile ecg_receiver/gui_tkinter/components/optimized_plotter.py
python -m py_compile ecg_receiver/core/llm_diagnosis.py
python -m py_compile ecg_receiver/gui_tkinter/main_window_modern.py
```

## 安全与合规

- 本系统为 **II 类软件概念演示**，未声明医疗器械注册  
- 所有 AI 输出须由持证医师审核  
- 出现胸痛、晕厥、呼吸困难等请立即拨打急救电话  

## 建议下一步

1. 连接设备，确认波形与心率来源稳定  
2. 与指夹式血氧仪或监护仪心率比对一次  
3. 再启用 AI 判读作为辅助参考  

---

*ECG AI v3.0 Clinical · 采样率校准版*