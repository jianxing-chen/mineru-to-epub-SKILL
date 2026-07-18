---
name: mineru-to-epub
description: >
  Convert Mineru-processed PDF output to a Kindle-ready EPUB.
  Use when the user says "convert to EPUB", "make epub", "生成 epub",
  "精读转 epub", "mineru to kindle", or mentions turning a PDF book into an ebook.
  Supports automatic footnote pattern detection (via --profile),
  LLM-driven semantic labelling, multi-type footnote rendering,
  chapter navigation, and embedded images.
---

# Mineru → EPUB Converter (v2)

Convert Mineru-processed PDF output into a high-quality, Kindle-ready EPUB with popup footnotes, chapter navigation, and embedded images.

**Core principle**: The script makes ZERO semantic assumptions about footnotes. All semantic decisions — "Is ① a translator note or an author note?" — are made by the LLM agent following the guidance in this SKILL.md.

---

## Skill 脚本的定位

本 skill 的 `convert.py` 是一个**参考实现**，处理最常见的情况。它的设计哲学是：

1. **处理 80% 的通用场景** → 圈号脚注、章节标题、目录分段
2. **不做过度推断** → 不猜测不该猜的（脚注语义交给 Agent），不检测难以可靠检测的（内容模式自动识别目录）
3. **不追求 100% 覆盖** → 遇到特殊排版、罕见脚注格式、非标准目录结构时，在项目目录里写独立的预处理/后处理脚本，而不是把 `convert.py` 越改越复杂

**Agent 的行为准则：**

| 情况 | 做法 |
|------|------|
| 中文书，圈号脚注，有目录有部分 | `convert.py` 直接处理 ✅ |
| 英文书，上标数字脚注 | 换 footnote_config 模板即可 ✅ |
| 目录格式特殊（如表格型目录） | 写项目后处理脚本修复 EPUB |
| 脚注匹配率低（<80%） | 检查 Mineru 输出质量，必要时修复数据源 |
| 需要自定义 CSS / 排版 | `--css` 参数传入自定义样式 |

**原则：skill 脚本是积木，不是黑盒。可以拆解、参考、绕过；不要强行适配。**

---

## Complete Workflow

```
  1. --profile      脚本扫描所有脚注标记和正文，按字符模式聚类
                    └→ 输出 profile.json（纯观测数据，无语义标签）

  2. LLM 分析       Agent 阅读 profile.json + body_samples
                    └→ 判定每个 pattern 的语义（译者注/参考文献/作者注/...）
                    └→ 生成 footnote_config.json + chapter_config.json

  3. --convert      脚本接收两个 config，按配置提取→匹配→渲染
                    └→ 输出 EPUB
```

**Agent 必须严格遵循：先 profile → 再分析 → 确认后 convert。不得跳过 profile 直接 convert。**

---

## Step 1: Profile（零配置）

```bash
python3 "$SKILL_DIR/scripts/convert.py" \
  --profile \
  --parts part_dir1 part_dir2 part_dir3 \
  > profile_output.json
```

输出示例（《依恋三部曲•第一卷》）：

```json
{
  "detected_patterns": [
    {
      "id": "p1",
      "marker_chars": "①②③④",
      "marker_format": "circled_ideograph",
      "ref_count": 111,
      "body_count": 101,
      "body_samples": [
        "① 文中引用的论文如下：Robertson和Bowlby（1952）...",
        "① 带有弗洛伊德笔记的版本保存于哥伦比亚大学...",
        "② 在较早期时，弗洛伊德认为当接收到源于外部的刺激时..."
      ],
      "body_language": {"cjk": 0.695, "latin": 0.106, "digit": 0.05, "other": 0.149},
      "body_avg_length": 106.7
    }
  ],
  "total_pages": 414,
  "total_markers": 111,
  "total_bodies": 105
}
```

**关键字段说明：**

| 字段 | 含义 | Agent 如何用 |
|------|------|-------------|
| `marker_format` | 标记的字符类型 | `circled_ideograph` / `superscript_digit` / `symbol` / `mixed` |
| `body_samples` | 脚注正文样本（前6条） | **最重要的判定依据**：阅读内容判断性质 |
| `body_language` | 正文语言占比 | cjk>0.7 → 倾向解释性注释；latin>0.7 → 倾向文献引用 |
| `body_avg_length` | 平均字符数 | >80字 → 倾向长注释；<30字 → 倾向短引用 |
| `pages_with_markers` / `pages_with_bodies` | 分布密度 | 评估脚注体系的覆盖广度 |

---

## Step 2: LLM 分析 —— Agent 行为指南

### 2.1 分析每个 pattern

Agent 收到 profile_output.json 后，**必须逐项检查每个 pattern** 并判定：

```
对于每个 pattern：
  1. 阅读 body_samples（前6条正文样本）
  2. 观察 body_language 占比
  3. 观察 body_avg_length
  4. 基于以下启发式给出 label
```

### 2.2 判定依据（启发式，非硬编码规则）

Agent 综合以下信号判定 pattern 的 label：

**信号观察清单：**

| 信号 | 倾向「解释性注释」 | 倾向「文献引用」 | 倾向「作者注」 |
|------|-------------------|-----------------|---------------|
| marker_format | circled_ideograph | superscript_digit | symbol (*,†,‡) |
| body_language.cjk | > 0.6 | < 0.2 | 不确定 |
| body_language.latin | < 0.1 | > 0.6 | 不确定 |
| body_avg_length | > 60 字 | < 30 字 | 中等 |
| body 内含中文解释 | 人物生卒年 (1xxx—1xxx)、"译者注"、"即..."、"指的是..." | — | — |
| body 内含引用格式 | — | "Author, Year"、"同上"、"et al." | — |
| body 内含作者口吻 | — | — | "我认为..."、"需要说明的是..." |

**如果 Agent 不确定：**
- 向用户展示 body_samples
- 请用户确认 label
- 不要猜测后静默跳过

### 2.3 生成 footnote_config.json

Agent 根据判定结果生成配置。**每个 pattern 必须包含以下字段：**

```json
{
  "types": [
    {
      "id": "circled",                          
      "label": "译者注",                         
      "marker_regex": "[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]+",  
      "marker_eq_regex": "\\^\\{([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]+)\\}",
      "body_prefix_regex": "[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]+",
      "multi_number_split": false,              
      "display_style": "circle",                
      "anchor_prefix": "fn_c_"                  
    }
  ],
  "display_order": ["circled"],                 
  "numbering": "per_chapter"                    
}
```

**字段说明：**

| 字段 | 用途 | 可选值 |
|------|------|--------|
| `id` | 内部标识 | 任意唯一字符串 |
| `label` | EPUB 中显示的章节标题 | 如 "译者注"、"参考文献"、"作者注" |
| `marker_regex` | 从文本中提取标记的正则 | 基于 `marker_chars` 构建 |
| `marker_eq_regex` | 从 equation_inline 提取标记 | 匹配 `^{...}` 包裹的格式 |
| `body_prefix_regex` | 识别脚注正文块的编号前缀 | 通常与 marker_regex 一致 |
| `multi_number_split` | 是否拆分多编号合并块 | digit 模式建议 `true`，circle 模式建议 `false` |
| `display_style` | 渲染样式 | `"circle"` — 保留原圈号 ①②③；`"bracket"` — 方括号 [1][2]；`"asterisk"` — 星号 *** |
| `anchor_prefix` | HTML id 前缀 | 各类型需不同，如 `"fn_c_"` / `"fn_n_"` / `"fn_s_"` |
| `display_order` | 章节末尾展示顺序 | 如 `["circled", "numbered"]` 表示译者注在前 |
| `numbering` | 编号作用域 | 当前固定 `"per_chapter"` |

### 2.4 常见脚注体系的配置模板

**A. 只有圈号（如《依恋三部曲》）：**
```json
{
  "types": [{
    "id": "circled", "label": "译者注",
    "marker_regex": "[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]+",
    "marker_eq_regex": "\\^\\{([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]+)\\}",
    "body_prefix_regex": "[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]+",
    "display_style": "circle", "anchor_prefix": "fn_c_"
  }],
  "display_order": ["circled"],
  "numbering": "per_chapter"
}
```

**B. 圈号 + 上标数字（如《明日之城》）：**
```json
{
  "types": [
    {
      "id": "circled", "label": "译者注",
      "marker_regex": "[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]+",
      "marker_eq_regex": "\\^\\{([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]+)\\}",
      "body_prefix_regex": "[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]+",
      "display_style": "circle", "anchor_prefix": "fn_c_"
    },
    {
      "id": "numbered", "label": "参考文献",
      "marker_regex": "\\^\\{(\\d+)\\}",
      "marker_eq_regex": "\\^\\{(\\d+)\\}",
      "body_prefix_regex": "\\d+",
      "multi_number_split": true,
      "display_style": "bracket", "anchor_prefix": "fn_n_"
    }
  ],
  "display_order": ["circled", "numbered"],
  "numbering": "per_chapter"
}
```

**C. 只有上标数字：**
```json
{
  "types": [{
    "id": "numbered", "label": "参考文献",
    "marker_regex": "\\^\\{(\\d+)\\}",
    "marker_eq_regex": "\\^\\{(\\d+)\\}",
    "body_prefix_regex": "\\d+",
    "multi_number_split": true,
    "display_style": "bracket", "anchor_prefix": "fn_n_"
  }],
  "display_order": ["numbered"],
  "numbering": "per_chapter"
}
```

### 2.5 生成 chapter_config.json

Agent 同时需生成章节配置。Profile 输出中不包含章节信息，Agent 必须系统地扫描 Mineru 输出来构建。**注意：Mineru 的 `title` 类型仅覆盖 `#`/`##` 级别的标题，而目录、部分标题、后记等常以 `paragraph` 或 `page_header` 形式存在，极易遗漏。**

#### 2.5.1 扫描方法（按优先级）

**方法一（首选）：从 `*_content_list_v2.json` 扫描**

这是最精确的方法，因为能直接获取每页每项的类型和文本：

```python
import json, os

# 对每个 part 目录：
for f in os.listdir(part_dir):
    if f.endswith("_content_list_v2.json"):
        with open(os.path.join(part_dir, f)) as fh:
            data = json.load(fh)  # list[page], 每页是 list[item]

for page_idx, page_items in enumerate(data):
    book_page = part_offset + page_idx  # part_offset 是 PDF 页码起点
    for item in page_items:
        t = item.get("type")      # "title" / "paragraph" / "page_header" / "page_footer"
        content = item.get("content", {})
        # 提取文本...
```

**方法二（辅助）：阅读 `full.md`**

仅用于交叉验证，不用于发现结构元素——因为 full.md 无页码标记且忽略了 item 类型。

#### 2.5.2 目录（TOC）的发现

中文书籍的目录几乎总是位于**前言和第一章之间**的 1-3 页内，且有以下特征：
- Mineru type 为 `paragraph`（非 `title`！）
- 连续多行短文本，每行包含 "第一章"/"第二章"… 或 "章……XX" 模式
- 通常包含页码数字（如 "第一章 丧失的创伤……002"）

**扫描策略：**
1. 先找到 `前言` 和 `第一章` 的 PDF 页码（通过扫描 title blocks）
2. 扫描这两页之间的所有 `paragraph` 文本
3. 如果连续 N 行匹配章节名+页码模式且 N ≥ 3 → 确认找到目录
4. 将目录本身作为一条 `{"name": "目录", "page": <起始页>}` 加入 chapters

**关键：目录还是书名页？** 目录页通常以 "目录" 字样开头（单独一行），书名页通常是竖排或居中大字。如果 Mineru 未能识别 "目录" 二字，通过内容模式判断：含 "第一章……XX" 格式的页 = 目录页。

**目录的 EPUB 内格式化**：`convert.py` 会自动为目录章节做分段排版——章节名含 `目录`/`目錄`/`Contents`/`Table of Contents`/`目次` 时，目录正文会被拆行并标注 CSS 类（Part 标题 → `toc-part`，后记/参考文献 → `toc-section`，其余 → `toc-chapter`）。中英文 Part 和后记模式均支持。**这是通用最佳实践，不是强制行为**——如果某本书的目录格式很特殊（表格型、非标准排版），Agent 应写项目后处理脚本修复 EPUB，而不是修改 `convert.py` 本身。

#### 2.5.3 部分（Part）标题的发现

学术书籍常分多个"部分"（第一部分/第二部分… 或 Part I/Part II…）。这些标题：
- Mineru type 通常为 `paragraph`（极少是 `title`）
- 独占一页或半页，文字简短（如 "第二部分" + "成人的哀悼"）
- 位于目录指示的章节边界前

**扫描策略：**
1. 从目录内容中提取部分标记（如 "第一部分 | 观察，概念，争论"）
2. 在每个部分的第一章之前 1-2 页扫描 `paragraph` 文本
3. 匹配到 "第X部分" 或 "Part X" 后，将整行作为 `{"name": "第X部分 …", "page": <页码>}` 加入

#### 2.5.4 后记/附录/其他非标题结构元素

- **后记 / 后记 / Afterword / Epilogue**：常作为 `page_header` 出现在正文末尾、参考文献之前。扫描最后几章的 `page_header` 内容。
- **附录 / Appendix**：在目录中有体现，扫描对应页码的 `title` 或 `paragraph`。
- **参考文献 / Bibliography**：通常是最后一章，`title` 类型，较易识别。

#### 2.5.5 目录作为章节标题的权威来源

**关键原则：目录中的标题是权威的。** Mineru 的 title block 可能因 PDF 排版问题遗漏副标题或截断标题。一旦发现目录，Agent 必须：

1. 从目录中解析出完整的章节列表和对应页码（注意：目录页码是**印刷页码**，非 PDF 页码，仅用于排序验证）
2. 将 Mineru 检测到的 title 与目录进行交叉比对：
   - 标题不一致 → 以目录为准
   - 目录有但 Mineru 未检测到 → 补充
   - Mineru 检测到但目录没有 → 可能是误检（如页眉、章节内子标题）

#### 2.5.6 页码映射

- **PDF 页码** = `part_offset + page_idx`（0-indexed），用于 `chapter_config.json`
- **印刷页码**（目录中出现的数字）仅用于验证排序，不写入 config
- `skip_pages` 用于跳过 CIP 数据、空白页等前置页面

#### 2.5.7 章节配置模板

```json
{
  "chapters": [
    {"name": "封面", "page": 1, "is_cover": true},
    {"name": "中文版序一", "page": 4},
    {"name": "前言", "page": 19},
    {"name": "目录", "page": 23},
    {"name": "第一部分 观察，概念，争论", "page": 25},
    {"name": "第一章 丧失的创伤", "page": 26},
    ...
    {"name": "后记", "page": 481},
    {"name": "参考文献", "page": 483}
  ],
  "skip_pages": [2, 3]
}
```

**注意事项：**
- 封面必须设置 `"is_cover": true`
- 章节必须按 PDF 页码升序排列
- `skip_pages` 列出应跳过的 PDF 页码（如前几页的 CIP/版权信息）
- 如果全书无 Part 划分、无目录、无后记，则省略对应条目

#### 2.5.8 生成后自检清单

Agent 在写出 chapter_config.json 后，必须逐项确认：

- [ ] 目录已识别且加入（如果原书有目录）
- [ ] 部分标题（第一部分/第二部分…）已加入（如果目录中有体现）
- [ ] 所有章节标题已与目录交叉比对，不一致的已修正
- [ ] 后记/附录已扫描并加入（如果原书有）
- [ ] 参考文献已加入
- [ ] 所有 `page` 值为 PDF 页码（非印刷页码）
- [ ] 章节按页码升序排列，无跳页或重复

---

## Step 3: Convert

```bash
python3 "$SKILL_DIR/scripts/convert.py" \
  --convert \
  --parts part_dir1 part_dir2 part_dir3 \
  --footnote-config footnote_config.json \
  --config chapter_config.json \
  --output book.epub \
  --title "书名" \
  --author "作者" \
  --cover-pdf original.pdf
```

**输出验证指标：**
```
译者注: 98 refs / 98 bodies       ← 各类型独立统计
参考文献: 520 refs / 520 bodies
Chapters: 30 | Images: 2
✅ All footnote links valid
```

---

## Quick Start（单本书完整流程）

```bash
# 1. Profile
python3 convert.py --profile --parts part1 part2 part3 > profile.json

# 2. Agent 分析 profile.json → 生成 footnote_config.json + chapter_config.json

# 3. Convert
python3 convert.py --convert \
  --parts part1 part2 part3 \
  --footnote-config footnote_config.json \
  --config chapter_config.json \
  --output book.epub \
  --title "书名" --author "作者" \
  --cover-pdf original.pdf
```

---

## Understanding the Mineru Data Structure

- **`full.md` lacks footnote bodies** — footnote content is only in `*_content_list_v2.json`
- **Two marker formats**: `equation_inline` (`^{①}`, `^{2}`) and plain text-embedded
- **Page-based numbering** restarts each page; the script renumbers per-chapter
- **Cross-page footnotes**: a ref near the page bottom may have its body on the next page
- **Multi-number blocks**: Mineru sometimes concatenates several footnote bodies into one text item (e.g., `"85 同上… 86 Fabian Society…"`) — set `multi_number_split: true` for digit patterns
- **Broken paragraphs**: Mineru may split a paragraph across two pages, leaving a sentence tail orphaned on the next page. The script auto-detects and merges these: if paragraph A ends mid-sentence (no 。！？) and paragraph B starts as a continuation (short tail fragment, or starts mid-sentence), they are merged into one `<p>`.

### Mineru Item Types 与章节结构的关系

⚠️ **关键认知：Mineru 用 `type` 字段标注每项内容的性质，但不同类型的"地位"不对等：**

| Mineru type | 典型内容 | 是否一定是结构元素？ |
|-------------|----------|---------------------|
| `title` | `#` / `##` 级标题 | ✅ 通常是章节标题 |
| `paragraph` | 正文段落 | ⚠️ **目录、部分标题、书名页也可能是 paragraph！** |
| `page_header` | 页眉 | ⚠️ **后记、部分标题可能是 page_header！** |
| `page_footer` | 页脚 | 页码或出版社名，可忽略 |
| `page_footnote` | 页末脚注正文 | 用于 footnote 匹配 |
| `page_number` | 页码数字 | 可忽略 |
| `equation_interline` | 行间公式 | 可忽略 |
| `table` | 表格 | 可忽略 |

**为什么目录和部分标题会被 Mineru 标识为 `paragraph`？**
- PDF 排版中，目录和部分标题通常不使用标准标题样式（无大纲级别标记）
- Mineru 的标题识别依赖 PDF 的大纲/书签结构，而非视觉排版
- 结果：目录、部分标题页、后记等在视觉上是"结构页"，但在 Mineru 输出中是"普通段落"

**因此，Agent 构建 chapter_config.json 时绝不能仅扫描 `title` 类型的项，必须同时扫描 `paragraph` 和 `page_header` 来发现目录、部分标题、后记等隐藏的结构元素。详见 §2.5。**

## match_footnotes 算法说明

脚本使用跨页滑动窗口匹配（±4页），支持同一编号多次引用同一出处（sharing），支持多编号正文拆分（multi_number_split）。这些对 Agent 透明，无需手动配置。

## Dependencies

```bash
pip install pypdfium2 Pillow pikepdf requests
```

## Mineru API Key

API key 通过环境变量或文件配置（避免暴露在命令行和 shell 历史中）：

```bash
# 方式 1: 环境变量（推荐）
export MINERU_API_KEY="sk-xxx"

# 方式 2: 文件（持久化）
echo "sk-xxx" > ~/.mineru_api_key
```

优先级：`--api-key` 参数 > `MINERU_API_KEY` 环境变量 > `~/.mineru_api_key` 文件。

API 客户端位于 `scripts/mineru_api.py`，支持 PDF 自动拆分（>200页）→ 上传 → 轮询 → 下载全流程。

## File Map

```
SKILL.md                          ← This file
scripts/
  convert.py                      ← Main script (--profile + --convert)
  mineru_api.py                   ← MinerU API client (split → upload → poll → download)
  analyze.py                      ← Diagnostic analyzer (footnotes, chapters, format issues)
  format_fix.py                   ← Post-processing fixes (paragraph merge, heading dedup)
```
