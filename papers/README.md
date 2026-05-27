# papers/ — 待检测论文目录

把需要检测的论文文件放到这里，支持以下格式：

## 支持的格式

| 格式 | 说明 |
|------|------|
| `.pdf` | 学术论文 PDF（主格式） |
| `.docx` | Word 文档（需 `pip install ".[docx]"`） |

## 目录结构

### 单篇论文（简单）

```
papers/
└── paper.pdf
```

运行：
```bash
paperfraud check papers/paper.pdf --extract-images --review --output-dir output/paper
```

### 带补充材料的论文

当论文有补充数据（CSV/TSV）时，放在同名子目录中，**数据文件会自动关联**：

```
papers/
└── nature-2024/
    ├── nature-2024.pdf       # 论文正文
    ├── figure-data.csv       # 图表原始数据（自动用于 Benford/GRIM 检测）
    └── supplementary.tsv     # 补充表
```

运行：
```bash
paperfraud check papers/nature-2024/nature-2024.pdf --extract-images --output-dir output/nature-2024
# data.csv 会自动被检测到并用于数值校验
```

### 多篇论文批量处理

```
papers/
├── paper1.pdf
├── paper2/
│   ├── paper2.pdf
│   └── data.csv
├── paper3.docx
└── batch/
    ├── batch1.pdf
    └── batch2.pdf
```

运行：
```bash
python3 scripts/batch_run.py "papers/" --extract-images -o results.json
```

`batch_run.py` 会**递归扫描**所有子目录，自动识别 `.pdf` 和 `.docx` 文件。

## 注意事项

- 论文文件名不要包含特殊字符
- 补充数据文件必须与论文放在同一目录中才能自动关联
- 也可以手动指定数据文件：`paperfraud check paper.pdf --data-file data.csv`
- 检测结果保存在 `output/` 目录中（已加入 `.gitignore`，不会提交到 GitHub）
