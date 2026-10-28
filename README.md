# dsh-pdf-toolkit

DeepSeek Harness 通用 PDF 插件。包含 **6 个模型工具、21 个处理操作、1 个技能、独立 Python 程序和测试**。没有写死文档、金额、字体或用户路径，也不包含用户的 PDF。

## 安装

需要 Node.js 20+、Python 3.10+。已在 macOS / Python 3.12 / DeepSeek Harness **0.2.1-rc.3** 实测。

从 GitHub 克隆，或解压源码包后进入插件目录：

```sh
git clone git@github.com:ayuana6691/dsh-pdf-tookit.git
cd dsh-pdf-tookit
npm run setup
dsh plugin --profile desktop add "file:$(pwd)"
```

`setup` 通过 pip 安装 Python 依赖到独立共享环境：

- macOS/Linux：`~/.local/share/dsh-pdf-toolkit/venv`（支持 `XDG_DATA_HOME`）。
- Windows：`%LOCALAPPDATA%\dsh-pdf-toolkit\venv`。

插件会自动找到这个环境，插件被 Harness 复制到安装目录后也能使用。**不需要 npm install，不依赖宿主 npm 包，不使用 Codex 的运行时。** 如果使用打包的 `.tgz`，先从源码包运行 setup，再执行：

```sh
dsh plugin --profile desktop add "file:/absolute/path/dsh-pdf-toolkit-1.0.0.tgz"
```

终端/网页使用相应 profile 代替 desktop。新建会话后让模型执行 `pdf_doctor`，再加载 `pdf-toolkit` 技能。插件没有自动改动现有会话或全局提示词。

卸载：`dsh plugin --profile desktop remove dsh-pdf-toolkit`。共享 Python 环境保留供重装使用。

### 自定义运行时

```sh
node scripts/setup.mjs --python /path/to/python3 --venv /path/to/pdf-venv
```

然后在插件条目的 config 设置 `python: /path/to/pdf-venv/bin/python`（Windows 为 `Scripts/python.exe`），或在启动 Harness 前设置 `DSH_PDF_PYTHON`。配置查找顺序：`config.python` → 环境变量 → 插件本地 `.venv` → 共享环境 → 系统 Python。

默认单次操作超时 `timeoutMs: 180000`，JSON 输出上限 `maxOutputBytes: 8388608`。取消和超时会终止所属 Python 进程。批量文档可以调整超时或按页处理。

## 能做什么

| 类别 | 操作 |
|---|---|
| 检查 | 页数、尺寸、旋转、字体、图像、元数据、表单、依赖诊断 |
| 提取 | 文本 TXT/JSON、表格 CSV/JSON、嵌入图片 |
| 页面编排 | 合并、拆分、选页、重排、重复页、删除其余页、旋转、裁切 |
| 编辑 | 文字水印、页码、元数据、表单填写/扁平化、文字替换、区域涂销 |
| 生成 | 纯文本创建 PDF、图片合成 PDF |
| 文件处理 | 无损内容流压缩和去重、AES-256 加密、密码解密 |
| 验证 | PNG 渲染、像素差分、局部修改区域掩码、保存后重开和逐页渲染 |

六个 Harness 工具：`pdf_doctor`、`pdf_inspect`、`pdf_extract`、`pdf_process`、`pdf_render`、`pdf_compare`。

模型可通过内置 `skill` 工具加载 `pdf-toolkit`；插件卸载时工具和技能随 Cordis 生命周期移除。

## 使用

直接在 Harness 对话中提出需求，例如：

- “把这两个 PDF 合并，保留输入顺序。”
- “只保留第 3、1、2 页并按这个顺序输出。”
- “提取所有表格，分别保存 CSV。”
- “将表单 customer 字段填成 Example Name，保留可编辑性。”
- “替换文中的指定短语，保持字体和颜色，并检查修改范围外的差异。”

也可独立使用 Python，stdin 接收 JSON，stdout 返回 JSON：

```sh
python /path/to/dsh-pdf-toolkit/python/pdf_toolkit.py < request.json
```

`request.json`：

```json
{
  "action": "rotate",
  "options": {
    "input": "/path/source.pdf",
    "output": "/path/rotated.pdf",
    "pages": "1,3-5",
    "angle": 90
  }
}
```

完整参数见 [操作参考](skills/pdf-toolkit/references/operations.md)，检查方法见 [验证指南](skills/pdf-toolkit/references/verification.md)。

## 文件结构

```text
index.js                  Cordis 插件：注册工具和技能，校验入参
runner.js                 Python 子进程、超时、取消、JSON 协议
cordis.patch.yml          Harness 插件安装入口
python/pdf_toolkit.py     通用处理程序，21 个操作
python/requirements.txt   经测试的 Python 依赖版本
scripts/setup.mjs         独立 Python 环境安装程序
skills/pdf-toolkit/       技能与操作/校验参考
test/                    合成 PDF 回归测试及真实 Harness 集成测试
```

## 验证

```sh
npm test
npm run test:host
```

可用 `DSH_PDF_PYTHON` 指向测试 Python，`DSH_BIN` 指向 Harness，`DSH_PDF_TEST_PACKAGE` 指向 `.tgz` 来验证完整安装流程。宿主测试使用隔离临时 profile 与本地脚本模型，不读取用户凭据，不调用外部模型，不修改现用 profile。

功能测试会即时生成合成 PDF、表单、表格和图片，不需要个人文档。测试覆盖全部操作，以及非修改区域像素一致、拒绝覆盖源文件、越界页码、替换溢出、取消/超时等。

## 适用范围

这是通用 PDF 工具集，不是完整的 Acrobat 或 Office 排版引擎。扫描件可渲染和编排，但**不内置 OCR**；表格提取结果受原布局影响。文字替换支持单行、同一字体样式的文本层；缺字、竖排、跨行及复杂字形需要另外处理。默认不会为塞入长文字而自动改字号或换字体，也不会自动重排相邻文字。

源文件不会覆盖。编辑输出不会保留数字签名的有效性，也不承诺维持 PDF/A、无障碍结构或所有第三方扩展。压缩不保证体积变小。涂销操作不是元数据/附件的全面清理。

## 实现依据与许可证

- [Harness 工具编写接口](https://github.com/deepseek-ai/deepseek-harness/blob/f3803c96974ad4a94195fb97e5c637e9c2a3afe3/docs/cookbook/adding-a-tool.md)
- [Harness 技能注册接口](https://github.com/deepseek-ai/deepseek-harness/blob/f3803c96974ad4a94195fb97e5c637e9c2a3afe3/docs/subsystems/skills.md)
- [PyMuPDF 页面操作](https://pymupdf.readthedocs.io/en/latest/page.html)
- [pypdf 表单处理](https://pypdf.readthedocs.io/en/stable/user/forms.html)

插件自有代码采用 MIT；依赖独立安装并遵循各自许可证，PyMuPDF 为 AGPL/商业双许可。详见 LICENSE。
