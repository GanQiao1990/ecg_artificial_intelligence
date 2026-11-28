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

## 信号采集与心率计算原理

### 芯片与硬件链路

| 环节 | 说明 |
|------|------|
| 前端芯片 | Texas Instruments **ADS1292R**：24-bit ΔΣ ADC，双通道，片上 PGA 增益 6，VREF = 2.4 V |
| ADC 采样率 | CONFIG1 寄存器 DR 位可选 125 / 250 / 500 / 1000 / 2000 / 4000 / 8000 SPS；本项目固件典型 **500 SPS** |
| 串口流率 | UART 输出速率可能与 ADC 速率不同（固件可抽取，如 500 SPS ADC → 50 Hz CSV）；**心率计算必须使用串口流率而非 ADC 速率** |
| 数值格式 | ProtoCentral 固件输出 `int16 = 24-bit 码字 >> 8`；每计数 ≈ **12.2 µV**（gain=6, VREF=2.4 V）；物理换算 `µV = Code × 2·VREF / (Gain·2²⁴) × 10⁶` |
| 串口协议 | `DATA,<timestamp_ms>,<ecg>,<resp>,<hr>,<status>`，波特率 57600；也兼容纯数值逐行格式 |

### 采样率确认与自动调整

1. **配置值**：默认 500 Hz（`DEFAULT_SAMPLE_RATE`），可被固件档案（`FIRMWARE_PROFILES`）覆盖。
2. **自动估计**（`SampleRateTracker`，Web 端默认 `auto` 模式，`ECG_SAMPLE_RATE_MODE=configured` 可关闭）：
   - **时间戳法**：取 `DATA` 行 timestamp_ms 的中位数间隔 → `F_s = 1000/Δt`，再对齐到 {50, 125, 250, 500, 1000} Hz；
   - **到达速率法**：统计串口行到达速率（需 ≥200 样本、≥2 s 窗口）；
   - 优先级：时间戳估计 > 到达估计 > 配置值；估计值统一 snap 到最近的标准档位，抑制抖动误判。
3. **心率交叉校正**（`correct_sample_rate_from_hr`）：当固件心率与 R 峰计算心率相差约 2×/½× 时，判定采样率配置错误，自动取 `rate×2` 或 `rate÷2` 并 snap，重新检测——这是"心率约 2 倍/一半"问题的兜底修复。

### 信号处理链（`ecg_signal.py`）

1. **原始码值 → µV**：按固件档案 `uv_per_count`（ProtoCentral int16 路径 ≈12.2 µV/计数）。
2. **基线去除**：约 0.6 s 滑动平均作基线并相减，消除漂移。
3. **FFT 带通滤波**（`filter_ecg_band`）：0.5–40 Hz 保留 ECG 能量带，反射填充抑制边缘伪影。
4. **市电干扰检测与陷波**（`detect_mains_interference`）：50/60 Hz 频点需同时满足 ① 谱峰/邻域中位数 > 6×；② 峰值幅度 ≥ **18 µV**（防止 QRS 谐波梳在 ~60 Hz 形成的 ~12 µV 细线被误判为市电）。命中后记录 `mains_interference_hz`，质量评级封顶 Fair。
5. **高频噪声占比**（`hf_noise_ratio`）：>40 Hz 谱功率占比，供 LLM 评估信号可信度。

### 心率检测与计算

1. **R 峰检测**（`detect_r_peaks`，作用于滤波后信号）：去基线 → 包络（绝对值+滑动平滑）→ 幅值排序非极大值抑制，**0.42 s 不应期**抑制 T/P 波重复计数；自动处理正负极性。
2. **心率计算**（`heart_rate_from_peaks`）：`HR = 60 / median(RR)`；RR 变异系数 CV → 节律分级（<0.08 规则 / <0.16 轻度不齐 / ≥0.16 不齐）。
3. **伪差/过计数防护**（双签名门控）：
   - **谐波伪迹**：包络中存在比检测 RR 更快的周期成分（比值 1.7–3.2），典型为肌颤/电极运动伪差梳状脉冲，R 峰不应期可能按二分之一或三分之一间隔欠采样；
   - **T 波双计**：被检峰中约 25–75% 为宽峰（T 波落在不应期外被计为心搏）；
   - 命中后回退到 **0.5–3 Hz 心脏成分自相关估计**（`slow_component`，`heart_rate_method_detail` 通常为 `acf`）。此时低频率峰可能是 P/T 波或伪差振铃，不能作为 RR 节律证据，因此节律报告为 **Undetermined**，`rr_variability` 置空。
4. **回退置信度**：归一化自相关峰输出为 `heart_rate_acf_correlation`，并映射为 `heart_rate_confidence`（≥0.25 高 / ≥0.10 中 / 更低为低）；低置信度时质量降为 Poor，所有回退修正信号质量最高仅 Fair。`artifact_period_sec` 输出检测到的周期伪差间隔。
5. **心率分区**（`hr_zone`，成人静息参考）：<50 严重心动过缓 / 50–60 心动过缓(偏低) / 60–100 正常 / 100–120 窦性心动过速可能 / 120–150 心动过速 / >150 严重心动过速；输出 `heart_rate_zone`、中文标签与异常标志。
6. **来源融合**（`resolve_heart_rate`）：固件 `hr` 字段与算法心率一致时融合；一般情况下大幅不一致优先相信固件心率作为采样率兜底，**但伪差修正触发时例外**——固件心率同样来自受污染前端信号，不能推翻已修正的低频心率。`heart_rate_source` 记录实际取值来源。

### 送入 LLM 的结构化指标

`preprocess_for_llm` 将全部计算字段（心率/方法/分区/节律 CV/质量/市电/高频噪声/QRS 数/滤波后波形片段）序列化进 prompt，LLM 被要求直接采用系统心率、勿自行重算，并在信号质量差时降低 confidence。

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

## Windows EXE 打包

在 Windows 10/11 电脑上双击运行：

```bat
build_exe.bat
```

成功后分发 `dist\ECG_AI_Diagnosis_Windows.zip`，或分发整个 `dist\ECG_AI_Diagnosis\` 文件夹。发布包内已包含运行依赖、`.env` 配置、数据目录和医护人员说明文档。

更多细节见 [PACKAGING_WINDOWS_CN.md](PACKAGING_WINDOWS_CN.md)。

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
