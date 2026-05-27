# PaperFraud Detect

学术论文造假检测系统。针对生命科学论文，提供 18 项自动化检测——规则引擎 + 统计检验 + LLM 定性分析的三层交叉校验架构。

## 快速开始

```bash
# 1. 安装
cd paperfraud && pip install -e ".[dev,web]"

# 2. 配置 API Key（可选，不配则只跑规则 + 统计检查）
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY 或其他 LLM Key

# 3. 检测一篇论文
paperfraud check papers/your-paper.pdf --extract-images --review --output-dir output/demo

# 4. 打开 Web 审查工作台
streamlit run paperfraud/web/app.py --server.port 8502 -- --report output/demo/report.json
```

## 论文输入格式

把待检测的论文放到 `papers/` 目录中。支持：

| 格式 | 说明 |
|------|------|
| `.pdf` | 学术论文 PDF |
| `.docx` | Word 文档（需 `pip install ".[docx]"`） |

目录结构示例：
```
papers/
├── paper1.pdf                    # 单篇 PDF
├── paper2/                       # 带补充材料的论文
│   ├── paper2.pdf
│   └── data.csv                  # 自动用于 Benford/GRIM 检测
└── paper3.docx                   # Word 格式
```

详见 [`papers/README.md`](papers/README.md)。

## 检测能力

### 数值校验
| 检查项 | 说明 |
|--------|------|
| statcheck | 从 APA 格式提取统计量，用 scipy 反算 P 值，标记不一致 |
| GRIM | 均值粒度一致性检验——样本量 × 均值必须是可能的整数和 |
| 尾数偏好 | 卡方检验末尾数字分布，检测人为凑整（0/5 偏好） |
| 小数位一致性 | 检测同一表格中小数位数异常统一 |
| 组间算术关系 | 检测组间差值/比值是否精确到不自然（如 CV < 1%） |
| 本福特定律 | 首位数字分布偏离 Benford 分布 |

### 统计方法
| 检查项 | 说明 |
|--------|------|
| 样本量功效 | 基于精确 t 分布计算最小可检测效应量，n < 3 直接标红 |
| P 值分布 | 检测 p 值是否聚集在 0.05 附近（p-hacking 信号） |
| 效应量分析 | 检查报告中是否只有 P 值而无效应量 |
| 方法论检查 | 正态性声称、伪重复、统计谬误、方法误用 |

### 图像取证
| 检查项 | 说明 |
|--------|------|
| LUT 伪彩映射 | 对灰度图施加伪彩，暴露拼接/擦除痕迹 |
| ELA 误差分析 | 重压缩误差热力图，定位修改区域 |
| 克隆检测 | pHash 滑动窗口检测图像内复制粘贴 |

> 图像检测仅生成辅助图，不自动判定——需要人工在 Web UI 中审查。

### 文本模式
| 检查项 | 说明 |
|--------|------|
| 黑名单词 | 检测"novel""breakthrough""remarkable"等过度宣称词汇 |
| P 值伪装话术 | "marginally significant""trending towards significance"等 |
| 标题-结论差距 | 检测标题宣称与实际结论之间的落差 |

### 生物信息学
| 检查项 | 说明 |
|--------|------|
| Western Blot QC | 检测是否报告了 loading control（GAPDH/β-actin/Ponceau 等） |

### LLM 定性审查
| 检查项 | 说明 |
|--------|------|
| 跨信号关联 | 综合所有检测结果，判断是否存在系统性造假模式 |
| PubPeer 风格报告 | 生成类似 PubPeer 评论的审查意见 |

支持的 LLM 提供商：DeepSeek（推荐，便宜）、Anthropic Claude、OpenAI GPT。

## 架构

```
PDF/DOCX → 文本/图片提取 (PyMuPDF / python-docx)
  → 18 项检测模块
      ├─ 规则引擎：硬编码学科常识（样本量/P 值/Western Blot）
      ├─ 统计分析：scipy 精确分布（GRIM/Benford/statcheck/p-curve）
      └─ 图像取证：OpenCV 辅助图（ELA/LUT/Clone）
  → 结果聚合 → 风险评分
  → LLM 定性审查（可选）
  → JSON 报告 + Web 审查工作台
```

## CLI 命令

```bash
# 单篇检测
paperfraud check paper.pdf --extract-images --review --output-dir output/demo

# 仅提取图片（不跑检测）
paperfraud extract-images paper.pdf -o images/

# LLM 二次审查已有报告
paperfraud review output/demo/report.json

# 启动 Web UI
streamlit run paperfraud/web/app.py --server.port 8502 -- --report output/demo/report.json
```

## 批量处理

```bash
# 递归扫描 papers/ 目录，处理所有 PDF 和 DOCX
python3 scripts/batch_run.py "papers/" --extract-images -o results.json

# 仅处理 PDF
python3 scripts/batch_run.py "papers/" --formats pdf

# 限制数量
python3 scripts/batch_run.py "papers/" --max-papers 10
```

## 添加自定义检测规则

黑名单词和 P 值伪装话术通过 YAML 文件配置，可以直接编辑：

- `paperfraud/checks/text/blacklist.yaml` — 添加/删除过度宣称词汇
- `paperfraud/checks/text/camouflage.yaml` — 添加/删除 P 值伪装话术正则

编辑后即时生效，无需改 Python 代码。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 检测边界

- 本工具提供**风险信号检测**，不提供"造假"的最终结论
- 18 项检查各有盲区——个别漏检不代表论文无问题，标红也不等于确实造假
- 图像检测结果仅为辅助判据，不做自动判定
- 最终的造假判定需要领域专家结合专业知识人工裁决

## 环境要求

- macOS
- Python >= 3.9

## License

MIT
