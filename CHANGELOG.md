# Changelog

## [0.2.0] — Unreleased

### Added
- Word (.docx) 格式支持（`python-docx` 可选依赖）
- `papers/` 输入目录，自动递归扫描子目录
- `.env.example` 模板
- YAML 数据驱动黑名单配置（`blacklist.yaml`、`camouflage.yaml`）
- 案例学习文档（`docs/examples.md`）
- 开发者贡献指南（`CONTRIBUTING.md`）
- 批量处理 `--formats` 参数支持格式过滤
- CLI 自动检测同目录补充数据文件（CSV/TSV）

### Changed
- README 重写：含架构图、检测能力表、快速开始、输入格式说明
- 引擎 `parse_pdf` 改为 `parse_paper`，自动根据扩展名分发 PDF/DOCX 解析器
- 批量处理 `batch_run.py` 改为递归扫描，支持混合格式
- 环境要求统一为 macOS

## [0.1.0] — 2026-05-26

### Added
- 初始发布：18 项检测模块
- PDF 解析（PyMuPDF）
- CLI（Typer）：check / review / serve / extract-images
- Streamlit Web 审查工作台
- LLM 定性审查（DeepSeek / Anthropic / OpenAI）
- 批量处理工具
- 169 项单元测试
