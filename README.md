# PaperFraud Detect

学术论文造假检测 CLI 工具 + Web 审查工作台。针对生命科学论文，提供 18 项自动化检测——不是 ChatGPT 套壳，是规则引擎 + 统计检验 + LLM 定性分析的三层交叉校验架构。

## 快速开始

```bash
# 安装
cd paperfraud && pip install -e ".[dev,web]"

# 一条命令跑通全流程（含 LLM 审查）
export DEEPSEEK_API_KEY="sk-..."
python3 -m paperfraud.cli check paper.pdf --extract-images --review --output-dir output/demo

# 启动 Web 审查工作台
streamlit run paperfraud/web/app.py --server.port 8502 -- --report output/demo/report.json
```

## 检测能力

| 类别 | 检查项 | 说明 |
|------|--------|------|
| 数值校验 | statcheck, GRIM, 尾数偏好, 小数位一致性, 组间算术, 本福特定律 | 统计量反算 + 粒度检验 |
| 统计方法 | 样本量功效, p 值分布, 效应量分析, 伪重复 | 基于 scipy 精确 t 分布 |
| 图像取证 | LUT 伪彩, ELA 误差分析, 克隆检测 | 仅生成辅助图，人工判定 |
| 文本模式 | 黑名单词, p 值伪装话术, 统计术语滥用 | 正则 + 学科知识库 |
| 生信检查 | Western blot 质量控制 | loading control 检测 |
| LLM 审查 | 跨信号关联, PubPeer 草稿 | DeepSeek/Anthropic/OpenAI |

## 架构

```
PDF → 文本/图片提取 → 规则引擎 (14条) → 统计分析 (scipy) → LLM 审查 → 风险报告
                              ↘ 常识规则库 ↗
```

详见 [设计文档](docs/design.md)。

## 环境要求

- Python >= 3.9
- macOS / Linux
- LLM 审查需设置 `DEEPSEEK_API_KEY`、`ANTHROPIC_API_KEY` 或 `OPENAI_API_KEY`

## 批量处理

```bash
python3 scripts/batch_run.py "pdfs/" --extract-images -o results.json
```

## License

MIT
