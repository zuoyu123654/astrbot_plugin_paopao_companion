# 泡泡陪伴 · 给 AI 的配置手册

> 这份文档是给「另一个 AI」看的。用户会发一句：
> **「请帮我配置好 AstrBot 里的泡泡陪伴插件，并验证是否可用。」**
> 你照着下面的步骤做，做到最后一步的自检全部通过，就算配置完成。

---

## 0. 先搞清楚它是什么（30 秒）

陪伴能力 = **一个本地常驻核心 + 一个 AstrBot 插件（遥控器）**。

| 部分 | 是什么 | 在哪 |
|---|---|---|
| 核心 | 常驻 HTTP 服务：截屏/帧拼图、字幕 OCR、音频转写、TTS、模式状态机 | 默认 `http://127.0.0.1:9888` |
| 插件 | 只调核心的 HTTP 接口，不含采集 | `<AstrBot>/data/plugins/astrbot_plugin_paopao_companion/` |
| 外部依赖 | 文本模型 Key、本地 TTS、本地转写、rapidocr、yt-dlp、ffmpeg | 见第 2 节 |

**核心不跑，插件只能显示「核心没响应」。** 所以第一步永远是确认核心。

---

## 1. 前置检查

```powershell
# 1.1 核心在不在
(Invoke-WebRequest http://127.0.0.1:9888/health -UseBasicParsing -TimeoutSec 5).Content

# 1.2 体检（核心自带，一次看全）
(Invoke-WebRequest http://127.0.0.1:9888/doctor -UseBasicParsing -TimeoutSec 15).Content
```

`/doctor` 返回 `{"ok": bool, "checks": {名字: {ok, detail}}}`。
**`ok: false` 的就是缺的东西**，按第 2 节补。

---

## 2. 依赖（缺哪个补哪个）

| 依赖 | 干什么 | 怎么确认 | 缺了怎么办 |
|---|---|---|---|
| 文本模型 Key | 聊天 / 识屏 / 摘要 | `/doctor` 里 `api_key` | 在核心的配置里填 Key |
| 本地 TTS（如 GPT-SoVITS） | 把话念出来 | `tts` 项 | 启动 TTS 服务，并在核心配置里写端口 |
| 本地转写（ASR） | 直播/网课转文字 | `asr` 项 | 启动 ASR 服务（OpenAI 兼容 `/v1/audio/transcriptions` 即可） |
| rapidocr | 番剧/视频字幕 OCR | `ocr_venv` 项 | 见 2.1 |
| yt-dlp | 抓普通投稿的 CC 字幕 | `ytdlp` 项 | 见 2.2 |

`ffmpeg` 只有抽音频时才需要，没有也能跑字幕那条。

### 2.1 装 OCR（一次）

```powershell
$root = "$env:LOCALAPPDATA\paopao\ocrvenv"
if (-not (Test-Path "$root\Scripts\python.exe")) {
    py -3.12 -m venv $root
    & "$root\Scripts\python.exe" -m pip install -q rapidocr_onnxruntime pillow
}
& "$root\Scripts\python.exe" -c "import rapidocr_onnxruntime; print('ocr ok')"
```

> 坑：**别用带 `python312.zip` 的精简 Python 建 venv**（会遮住 stdlib 的 `dis`，
> 导致 numpy 导入失败）。用系统安装的 Python 或官方 Python。

### 2.2 装 yt-dlp（一次）

```powershell
$t = "$env:LOCALAPPDATA\paopao\ytdlp"
py -3.12 -m pip install -q --target $t yt-dlp
py -3.12 -c "import sys; sys.path.insert(0,'$t'); import yt_dlp; print('ytdlp', yt_dlp.version.__version__)"
```

---

## 3. 配置插件

### 3.1 装插件

目录：`<AstrBot>/data/plugins/astrbot_plugin_paopao_companion/`
必须有：`main.py`、`metadata.yaml`、`_conf_schema.json`、`requirements.txt`、`README.md`、`logo.png`。

### 3.2 配置项（WebUI → 插件 → 泡泡陪伴 → 配置）

| 键 | 默认 | 说明 |
|---|---|---|
| `gateway_url` | `http://127.0.0.1:9888` | 核心地址；改端口时和核心一起改 |
| `start_script` | 空 | 核心启动脚本路径（可选） |
| `auto_start` | `false` | 插件加载时是否自动拉起核心 |
| `timeout_seconds` | `12` | HTTP 超时 |

**没有必填项**。核心在，默认就能跑。

### 3.3 让插件生效

WebUI → 插件管理 → 泡泡陪伴 → 重载；或重启 AstrBot。
日志应出现：`[陪伴] 核心地址 http://127.0.0.1:9888（自动启动=False）`

---

## 4. 验证（一条一条过，全绿才算好）

在聊天里依次发：

| 输入 | 期望 |
|---|---|
| `陪伴` | 显示模式 + 今日识屏次数 + 帧缓冲 + 摘要字数 |
| `陪伴 医生` | 逐项 ✅/❌，最后一行「总体：全部就绪」 |
| `陪伴 模式 anime` | 「✅ 模式切到 anime（番剧）」 |
| `陪伴 模式 auto` | 切回自动 |
| `陪伴 说 我在看呢` | 「🔊 已送播 N 句」，并且真的响一声 |
| `陪伴 摘要` | 给一段摘要，或「这次没摘要：记录还不够多」——都算正常 |
| `帮我看一下我的屏幕` | 「👀 看过了（识屏计数 a → b）」——随口说就能触发，不用记命令 |

再补一条**不经过聊天**的验证（证明核心自己活着）：

```powershell
(Invoke-WebRequest http://127.0.0.1:9888/status -UseBasicParsing -TimeoutSec 8).Content
```
应返回 `mode`、`frames`（>0 表示帧缓冲在跑）、`precue`（>0 表示提示音缓存好了）。

**全部通过 = 配置完成。**

---

## 5. 故障排查

**`陪伴` 回「核心没响应」**
1. 手动起核心（按你部署方式，或者配好 `start_script` 后发 `陪伴 启动`）
2. 等 10 秒再试；还不行看核心的日志。

**`陪伴 医生` 里 TTS 是 ❌**
TTS 服务没起。核心照样工作，只是没声音。

**ASR 是 ❌**
转写服务没跑；直播/网课那档会退化（只看画面）。

**`ocr_venv` 是 ❌**
按 2.1 建，注意别用精简版 Python。

**`陪伴 说 ...` 说成功了但没声音**
检查系统默认输出设备、音量，以及 TTS 是否加载了目标音色。

**插件装好了但聊天里没反应**
1. 日志搜 `astrbot_plugin_paopao_companion` 有没有加载错误；
2. 群里可能被「只回 @」规则挡住，私聊最稳。

---

## 6. 不要做的事

- 不要把这个插件当采集器改——采集在核心。
- 不要把核心塞进 AstrBot 进程里（重启会连带断采集）。
- 不要用系统服务托管核心（服务拿不到桌面会话，截屏和音频环回都会失败）。
- 不要在没确认 `/health` 的情况下先怀疑插件。

---

## 7. 一句话总结

> 核心 = 发动机（默认 `127.0.0.1:9888`，采集全在这）；插件 = 遥控器（只管开关和体检）。
> 配置成功的判据只有一条：**`陪伴 医生` 全部 ✅，且 `陪伴 说` 能出声。**
