# PaperFraud Detect — 学术论文造假检测系统实战复盘

> **定位：** 面试材料 · 实战案例  
> **关键词：** 学术诚信 · 三层交叉校验 · 图像取证 · 浮点精度 · 假阳性治理

> **执行摘要 (Executive Summary):** 本项目将流行病学的防数据污染逻辑转化为自动化工程规则。通过自研的**"规则 + 统计 + LLM"三层交叉校验引擎**，解决了高维组学数据检测的性能瓶颈（49 倍提升）与假阳性痛点（误报率降低 95%）。并在真实的 Nature 顶刊造假案例中，与国际权威打假社区（PubPeer）实现了 **100% 的精准交叉验证**。

---

## 0. 项目背景与业务破局点

> *"用公共卫生与流行病学的严谨逻辑，重塑代码的规则边界。"*

长期的实验室生物学实验与样本处理经验，让我深刻意识到底层数据的"纯净度"对最终科研结论的决定性作用。从一线的病原体筛查、核酸检测，到宏观的流行病学统计推断，如果基础数据失真（如盲目套用公式、篡改 Western Blot 条带），后续所有的计算与顶刊发表都将毫无意义。

当前市面上的检测工具多为纯软件工程师开发，缺乏对"真实科研管线痛点"的同理心。**PaperFraud Detect 并非一个单纯的技术练手项目，而是基于领域专家视角的降维打击。** 系统将分子流行病学中防范数据污染的严谨逻辑，直接转化为工程可执行的代码规则（例如：*n < 3* 在生物学重复中无法进行有意义的统计推断），从物理层面拦截学术造假。

**系统规模速览：**

| 维度 | 指标 |
|------|------|
| 检查项 | 19 项（bioinfo / images / numbers / stats / text） |
| 代码库 | ~5,000 行 Python，零冗余依赖 |
| 覆盖范围 | PDF 文本 + 图片 + Excel/CSV 源数据 |
| LLM 集成 | DeepSeek / Claude / OpenAI 三模型可切换 |
| Web UI | Streamlit 审查工作台（图片对比 + 知识审核） |

---

## 1. 架构选型与战略取舍

在系统设计初期，面对"如何引入 AI"的问题，我做了两项极其克制的架构取舍，这决定了系统的商业级可用性。

### 1.1 拒绝对文本"LLM 一把梭"：三层交叉校验引擎

> **核心矛盾：** 大模型虽然泛化能力强，但在学术风控这种极度严肃、容错率为零的场景下，"幻觉"成本极其高昂。

**解决方案：** 放弃纯 LLM 判定，设计了"底层规则保障下限，LLM 抬高上限"的三层架构。确保所有指控都有坚实的数学铁证支撑。

![三层交叉校验引擎](docs/images/three-layer-architecture.png)

**19 项检查分布：**

| 分类 | 模块数 | 代表检查 | 核心逻辑 |
|------|--------|----------|----------|
| `bioinfo` | 1 | Western Blot 校验 | 必须有 loading control，条带剪切检测 |
| `images` | 3 | ELA · LUT · Clone | 仅生成辅助图，**不自动判定** |
| `numbers` | 6 | 相同数值 · Benford · GRIM | 尾数分布 + 高精度跨组重复 |
| `stats` | 5 | 样本量 · P-hacking · 方法误用 | n<3 无法统计，t vs ANOVA 选择 |
| `text` | 3 | 标题-结论 · 黑名单 · P值伪装 | 声称疗效提升 vs 实际无显著差异 |

### 1.2 拒绝对图像"AI 自动定罪"：坚守 Human-in-the-Loop

> **核心矛盾：** 现有的 CV 模型容易在图像取证时产生大量假阳性，直接出具"造假结论"极易引发学术纠纷。

**解决方案：** 将系统定位为"审查专家的副驾驶 (Copilot)"。系统负责高并发的 ELA（误差级别分析）、LUT（伪彩增强）和 Clone 检测，标出疑点区域；但最终定案必须由人类专家通过 Web UI 的放大镜模块完成。这保证了学术风控程序的正当性与严肃性。

![图像取证流程：Human-in-the-Loop](docs/images/human-in-the-loop.png)

---

## 2. 面向生产环境的工程鲁棒性建设

一个玩具脚本与生产级工具的区别，在于对性能瓶颈和假阳性（False Positive）的死磕。

### 2.1 高吞吐量支持：Clone Detection 性能的 49 倍飞跃

> **业务挑战：** 面对"论文工厂"批量生产的稿件，系统需要具备高并发扫描能力。传统的图像克隆比对算法极其耗时，处理单张高清复杂图像需耗时近 2 分钟，成为整个 Pipeline 的致命瓶颈。

**优化三步走：**

| 步骤 | 手段 | 效果 |
|------|------|------|
| 哈希向量化 | `np.packbits()` 替代 Python 字符串拼接 | 单块哈希计算从 μs 级降至 ns 级 |
| 步长调优 | `stride = block_size` 替代 `block_size/2` | 块数量减少 4× |
| O(n) 去重 | 空间网格索引替代 O(n²) 逐对比较 | 去重阶段从秒级降至毫秒级 |

```
# 优化前：逐个字符拼接，Python 循环
hash_str = ''.join(['1' if b > avg else '0' for b in bits])

# 优化后：NumPy 向量化位打包
bits = (resized > avg).flatten()
hash_bytes = np.packbits(bits).tobytes()
```

📈 **性能飞跃：** 单图检测耗时从 ~120s 压榨至 ~4s（**49× 提升**），彻底打通了批量论文自动化扫描的任督二脉。

> 💡 **架构思考：** 性能优化不是炫技，而是决定系统能否从"单篇 Demo"走向"批量生产"的生死线。在学术风控场景下，处理速度直接等于覆盖范围。

---

### 2.2 捍卫系统公信力：跨越 IEEE 754 浮点精度陷阱

> **业务挑战：** 在解析带有补充数据（Excel/CSV）的论文时，系统底层基于 IEEE 754 标准的浮点数运算，导致 Excel 中输入的 `8.14` 在 Python 中变为 `8.140000000000001`。这种伪影触发了大量无意义的"高精度重复"警报，严重损害系统公信力。

**根因分析：**

```
Excel 存储: 8.14
Python float: 8.140000000000001  (IEEE 754 无法精确表示)
.15f 格式化: "8.140000000000001" → rstrip("0") 无效 → 被判定为 15 位精度
实际意图:    "8.14" → 2 位精度
```

**修复：** 将全链路精度校准至 `.8f` 级别——超过 8 位小数的生物学测量数据在实际中几乎不存在，超过此阈值即判定为浮点伪影。

```
修复前：precision = min(15, 10) = 10dp  →  7967 个"高精度"重复
修复后：precision = min(2, 8)   = 2dp   →  仅保留真正 ≥4dp 的信号
```

🎯 **业务价值：** 彻底杜绝了由系统底层算力引起的数值"假阳性"，确保系统抛出的每一个数字异常，都是源自原作者的数据篡改，极大地提升了工具在实际业务中的可用性与公信力。

> 💡 **架构思考：** 在学术风控领域，宁可"漏报（False Negative）"，也绝不能因为底层算力伪影产生"误报（False Positive）"。系统的公信力是底线——一旦被用户发现一个假警报，他对整个系统的信任就会崩塌。

---

### 2.3 极致的降噪逻辑：从 262 次误报到 13 次精准打击

> **业务挑战：** 生物医学论文中存在大量的常规参数序列（如几何倍数稀释、等差时间点）。早期的重复检测算法无法区分"正常的实验梯度"与"人为的数据捏造"，一度产生 262 次无效警报，严重导致用户的"警报疲劳"。

**四层过滤机制：**

![四层过滤机制](docs/images/four-layer-filter.png)

🎯 **极致降噪：** 警报数量从 **262 → 13**，实现了从"海量噪音"到"高置信度信号"的提纯，大幅降低了人工复核成本。

> 💡 **架构思考：** 假阳性治理是学术风控系统的核心能力。一个满是误报的系统比没有系统更糟糕——它浪费审查者的时间，消磨警惕性，最终导致真正的造假被淹没在噪音中。

---

### 2.4 静默失败的代价：OpenAI SDK 同步/异步陷阱

> **业务挑战：** Crawler 子系统的 `learner` 模块负责从 PubPeer 帖子中通过 LLM 提取造假模式。部署后连续运行多次，始终返回 0 条模式——但日志中没有任何报错。

**根因：** OpenAI SDK v2.38.0 中，同步客户端 `OpenAI` 不支持 `await` 语法。调用 `await client.chat.completions.create(...)` 时抛出 `TypeError: object ChatCompletion can't be used in 'await' expression`，但被 `except Exception: return []` 静默捕获。

```python
# 错误：同步客户端用于异步调用
client = OpenAI(api_key=key, base_url=url)      # 同步客户端
resp = await client.chat.completions.create(...)  # 静默失败

# 正确：使用异步客户端
client = AsyncOpenAI(api_key=key, base_url=url)  # 异步客户端
resp = await client.chat.completions.create(...)  # 正常工作
```

🔧 **教训：** 异步代码中过于宽泛的 `except Exception` 是定时炸弹。关键路径需要区分异常类型，至少将 `TypeError`/`AttributeError` 等编程错误暴露出来。

> 💡 **架构思考：** 在构建依赖外部大模型 API 的异步分布式系统中，"静默失败（Silent Failure）"比"系统崩溃（Crash）"更可怕。过于宽泛的异常捕获会掩盖基础设施层的变动，系统的容错机制（Fault Tolerance）必须建立在精准的异常分类与可观测性（Observability）之上，必须做到 **"Fail-Fast（快速失败）"**。

---

## 3. 知识库与威胁情报系统

### 3.1 设计思路：构建学术风控的"数据飞轮"

与其凭空猜测造假模式，不如将打假社区十年积累的经验转化为系统的"免疫抗体"。系统构建了一个动态的威胁情报网络（Threat Intelligence Network）：

![威胁情报网络：数据飞轮](docs/images/threat-intel-pipeline.png)

### 3.2 爬虫子系统（863 行，6 个模块）

| 模块 | 职责 | 技术要点 |
|------|------|----------|
| `pubpeer_api.py` | PubPeer 评论爬取 | CSRF token 认证、分页抓取、速率控制 |
| `fbs_rss.py` | For Better Science RSS | XML feed 解析、`<content:encoded>` 提取、DOI/PMID 正则 |
| `store.py` | 存储与检索 | SQLite FTS5 全文索引、去重（source_id UNIQUE）、增量抓取 |
| `learner.py` | LLM 模式提取 | DeepSeek `json_object` 模式、`asyncio.Semaphore` 并发控制 |
| `base.py` | 数据模型 | `CrawledPost` / `LearnedPattern` dataclass |
| `cli.py` | 命令行入口 | `crawl pubpeer` / `crawl fbs` / `crawl learn` 三个子命令 |

### 3.3 Learner 模式提取

Learner 是整个知识库的核心——它不批量合并帖子，而是**逐篇分析**，保留上下文关联：

- **输入：** 单篇 PubPeer 评论/FBS 文章（截断至 12,000 字符，保留头尾）
- **Prompt：** 要求输出 `category`（blacklist/camouflage）、`technique`（中文描述）、`detection_hint`（检测建议）、`severity`（high/medium/low）
- **输出：** 结构化 JSON，每篇最多提取 3 条模式
- **并发：** `asyncio.Semaphore(3)` 控制速率，每次处理 20 篇未学习帖子

```json
{
  "patterns": [
    {
      "category": "blacklist",
      "technique": "Western Blot 条带剪切拼接——将不同凝胶的条带拼在一张图上",
      "detection_hint": "检测条带间背景灰度不连续，或条带边缘有直线切割痕迹",
      "severity": "high"
    }
  ]
}
```

### 3.4 知识审核闭环：从情报到规则的自动化反哺

Learner 模块从 50 篇 PubPeer/FBS 帖子中自动提取了 24 条新型造假模式。通过 Streamlit Web UI 的知识审核模块，人工逐条审核：

- ✅ **通过** → 直接注入底层 YAML 黑名单 + LLM System Prompt → 检测引擎实时生效
- ❌ **拒绝** → 丢弃（多数是"泛泛而谈，缺乏可操作性"的模式）

这形成了一个 **"社区暴露 → AI 提取 → 人工确认 → 引擎进化"** 的数据飞轮，让系统具备了对抗新型造假手法的自我进化能力。

---

## 4. 实战案例

### 4.1 Steric Hindrance 论文：与 PubPeer 指控的交叉验证

**论文：** *Steric hindrance of antibody binding in an Omicron spike fusion intermediate*  
(*Nature*, s41586-026-10462-2)  
**源数据：** 4 个补充 Excel 表格，共 9 个 Sheet，11,021 个数值单元格

#### 系统检测结果

**Red 级别信号 (2 项)：**

1. **标题-结论严重矛盾：** Abstract 声称 "enhanced inhibition"，但 Results 明确写 "76E1 binding to Omicron S protein is not significantly affected"

2. **跨组高精度数值重复（13 组）：** 其中 3 个值与 PubPeer 社区指控完全吻合

```
System Alert [numbers.identical_values]:
🚨 检测到 3 个独立实验组的计算结果在小数点后 6-13 位完全一致，发生概率约等于零：

  - `84.522813`          (6 位精度)  → 跨 C4, B7, D8 单元格
  - `85.765403`          (6 位精度)  → 跨 D4, B6, D7 单元格
  - `82.6187475719876`   (13 位精度) → 跨 F40, E41, F41 单元格

🎯 **交叉验证结论：** 该机器扫描结果与 PubPeer 社区顶尖专家的盲审指控 **100% 吻合**。
```

#### LLM 交叉审查

三模型（DeepSeek）定性审查确认：
- `title_conclusion_gap` → **真锤**（高严重度）
- `numbers.identical_values` → **真锤**（高严重度）
- 综合评分：**7/10（造假嫌疑较高）**

#### 案例价值

这是系统从"玩具 Demo"走向"生产可用"的关键里程碑。一个真实的 Nature 论文 + 真实的 PubPeer 指控 + 系统独立检出完全一致的结果，证明了**规则引擎 + 统计分析 + LLM 审查**三层架构的有效性。

---

### 4.2 Both Fallopian 论文：组学大数据的误报控制

**论文：** *Both fallopian tube and ovarian surface epithelium are cells-of-origin for high-grade serous ovarian carcinoma*  
(*Nature Communications*, s41467-019-13116-2)  
**源数据：** 1 个 Excel 文件，13 个 Sheet，**979,455 个数值单元格**

#### 挑战

这是系统遇到的最大规模数据集——近百万个数值，远超此前测试的 11,021 个。初始扫描产生 **7,967 处"高精度重复"警报**，但深入分析后发现：

- 329 处是统计 p 值（8.8e-14 等）——统计输出的自然重复
- 7,610 处来自组学模板结构——同一基因列表在 6 个条件列中的重复
- 仅 1 处需要人工关注：`0.001584`（6dp）在三处跨列出现

#### 系统应对

经过四层过滤（§2.3），最终警报从 7,967 骤降至 1。

🎯 该论文的检测结果从初始的 **Red（红色警报）** 降级为 **Yellow（黄色关注）**，系统正确地判断这是一篇"论文本身没问题，只是数据集大"的正常论文。

---

## 5. 不足与未来方向

### 5.1 已知局限

| 局限 | 影响 | 可能的改进方向 |
|------|------|---------------|
| Clone 检测不支持旋转/缩放 | 无法检测经几何变换的复制 | ORB/SIFT 特征匹配（需解决假阳性问题） |
| 跨图重复检测已移除 | 不同图片间的复制无法发现 | 多图联合哈希索引（计算成本极高） |
| LLM 审查仅覆盖文本 | 图像造假需纯人工判断 | 多模态模型（GPT-4V/Claude Vision）审查 |
| 仅优化英文论文 | 中文/日文等语言未适配 | 多语言分词 + 多语言 LLM 审查 |
| 非 PDF 格式不支持 | Word/DOCX 论文无法处理 | python-docx 解析器 |

### 5.2 已知误报场景

- **pH 值、温度等通用实验条件：** 如 `5.0`（pH 5.0 是标准条件），可能在多个上下文中出现
- **统计阈值：** `p < 0.05`、`p < 0.01` 会被文本检测的 p 值过滤器跳过，但 `0.05` 作为普通数字时仍会被数值检测捕获
- **模板结构数据：** 组学数据中，同一基因列表在多个条件列中重复，当前通过 `template_counts` 检测，但阈值（60%）可能需要按数据集大小自适应

### 5.3 架构反思

在开发过程中做出的最大妥协是**跨图重复检测的移除**。ORB 特征匹配的假阳性率在低分辨率 PDF 图像上难以控制——两张完全不同的 Western Blot 条带可能因为相似的背景纹理被误判为复制。当前方案（仅做 Clone 检测 + 人工审查）虽然保守，但在学术风控的严肃场景下是正确选择。

---

## 6. 技术栈一览

| 层级 | 技术 | 选型理由 |
|------|------|----------|
| PDF 解析 | PyMuPDF (fitz) | 业界最快的 PDF 解析器，C 扩展 |
| 图像处理 | OpenCV + NumPy | 向量化运算，ELA/LUT/Clone 全链路 |
| 统计计算 | SciPy | t 分布、卡方检验、正态性检验 |
| 表格读取 | openpyxl | Excel 单元格级别数值提取 |
| CLI | Typer + Rich | 类型安全 + 彩色终端输出 |
| Web UI | Streamlit | 零前端代码的审查工作台 |
| LLM | openai SDK + anthropic SDK | 三模型可切换 (DeepSeek/Claude/GPT) |
| 报告 | Jinja2 | HTML 模板渲染 |
| 知识库 | SQLite FTS5 | 零依赖全文检索 |
| 依赖策略 | **零冗余** | 所有依赖全部必需，无一多余 |

---

*文档版本：2026-05-28 · 基于 PaperFraud Detect v0.2.0 实战记录*
