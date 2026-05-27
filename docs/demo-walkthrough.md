# 实操教程：从零开始打假一篇论文

本教程带你走完整流程——下载配置 → 选一篇论文 → 跑检测 → 审查图像 → 用 AI 辅助深挖。

## 1. 环境准备

```bash
# 克隆仓库
git clone https://github.com/guandu52-oss/paperfraud.git
cd paperfraud

# 安装（含 Web UI 和开发依赖）
pip install -e ".[dev,web]"

# 如果需要 Word (.docx) 支持
pip install -e ".[docx]"
```

## 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，至少填入 DEEPSEEK_API_KEY（推荐，便宜快速）
```

验证配置：

```bash
paperfraud doctor
# 输出绿色 ✅ 表示一切就绪
```

## 3. 准备待检测论文

把论文放到 `papers/` 目录：

```bash
# 单篇
cp ~/Downloads/suspicious-paper.pdf papers/

# 带补充材料的论文（CSV/TSV 数据会被自动关联）
mkdir papers/paper2
cp ~/Downloads/paper2.pdf papers/paper2/
cp ~/Downloads/supplementary.csv papers/paper2/
```

支持的格式：`.pdf`（PDF 论文）、`.docx`（Word 文档）。

## 4. 第一次检测

```bash
# 基础检测（仅统计+规则，不需要 API Key）
paperfraud check papers/suspicious-paper.pdf --extract-images

# 完整检测（含 LLM 审查 + 自动打开 Web UI）
paperfraud check papers/suspicious-paper.pdf \
  --extract-images \
  --review \
  --output-dir output/demo
```

终端会显示：
- 解析进度（PDF 页数、提取图片数）
- 18 项检测执行状态
- LLM 审查结果（如果配置了 API Key）
- 欺诈风险评分

检测完成后自动打开浏览器进入 Web 审查工作台。

## 5. Web 审查工作台

Web UI 提供以下功能：

### 检测总览
点击 `📊 检测总览` 查看所有检测项的结果表格，按级别分色：

- 🔴 Red：高风险信号
- 🟠 Orange：中风险信号
- 🟡 Yellow：低风险信号
- 🟢 Green：通过
- ⚪ Error：无法执行

### 图像取证
点击 `🖼️ 图像取证` 进入可视化审查：

**LUT 伪彩映射** — 用 Fire/Iron、Royal、Viridis 三种伪彩色放大肉眼难以察觉的编辑痕迹：
1. 选择一张图片
2. 左侧是原始图（或 Fire 伪彩色参考图）
3. 在左侧图片上拖拽画框选中可疑区域
4. 右侧同步放大显示各伪彩色版本
5. 拼接/修图区域在伪彩色下会呈现不自然的颜色断层

**克隆区域检测** — 红色连线标记像素级相似的区域，用于发现 copy-paste 伪造。

**ELA 误差水平分析** — 利用 JPEG 压缩差异高亮可能来自外部的粘贴内容。

### 人工审查工作台
`🔍 人工审查工作台` 提供双图比对工具：
- **Sync 同步放大镜**：两图同步缩放，对照细节
- **Blink 闪烁对比**：原位交替，拼接边缘最易察觉
- **Diff 差异混合**：像素级减法热力图
- **LUT 伪彩映射**：伪彩色放大灰度断层

### 交互问答
`💬 交互问答` 让你直接对话 AI，针对检测报告深挖：
- "为什么 sample_size 被判定为可疑？"
- "有没有跨信号的关联证据？"
- "哪个信号最可能是假阳性？"

### LLM 审查 + PubPeer 草稿
`🤖 LLM 审查` 显示 AI 的综合判断和逐信号分析。
`📝 PubPeer 草稿` 可下载 AI 生成的中文审稿意见，直接发表到 PubPeer。

## 6. 批量处理

```bash
# 批量检测 papers/ 目录下所有论文
python3 scripts/batch_run.py "papers/" --extract-images -o results.json

# 只检测 PDF
python3 scripts/batch_run.py "papers/" --formats pdf -o results.json

# 同时检测 PDF 和 DOCX
python3 scripts/batch_run.py "papers/" --formats pdf,docx -o results.json
```

批量处理完成后，可以用 `serve` 命令统一浏览所有报告：

```bash
paperfraud serve output/ --port 8501
```

## 7. 自定义检测规则

编辑 YAML 配置，无需改代码：

### 添加新的黑名单词汇
编辑 `paperfraud/checks/text/blacklist.yaml`：
```yaml
absolute:
  - "definitely proves"
  - "absolutely confirms"
  # 添加你的发现...
overclaim:
  - "first ever demonstration"
  # 添加你的发现...
```

### 添加新的 P 值伪装话术
编辑 `paperfraud/checks/text/camouflage.yaml`：
```yaml
patterns:
  - regex: "marginally significant|marginally non-significant"
    explanation: "边缘显著话术 — 回避 p>0.05 的事实"
```

修改后重新运行检测即可生效。

## 8. 导出报告

Web UI 的 `📥 导出报告` 页面支持下载：
- **Markdown 报告**：适合存档和分享
- **JSON 报告**：完整数据，可用于后续分析和 `paperfraud review` 重新审查

## 检测边界（重要）

本工具的检测范围：
- ✅ 已发表论文的公开统计数据和图像
- ✅ 统计方法论一致性（样本量、p 值、效应量）
- ✅ 数理矛盾（非整数均值、尾数偏好、p-hacking）
- ✅ 图像编辑痕迹（克隆、拼接、重压缩）

本工具**不能**检测：
- ❌ 内部自洽的完全伪造数据
- ❌ 论文工厂专业出品（无拼接、无复用）
- ❌ 跨论文的概念矛盾（同一团队前后论文互相矛盾）
- ❌ 未公开原始数据的论文

**所有信号仅供参考，最终判断需人工复核。**
