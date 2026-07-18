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

Agent 同时需生成章节配置（与 v1 格式兼容）。Profile 输出中不包含章节信息——Agent 需通过阅读 full.md 或 title blocks 来构建：

```json
{
  "chapters": [
    {"name": "封面", "page": 1, "is_cover": true},
    {"name": "前言", "page": 4},
    {"name": "第一章 观点", "page": 28}
  ],
  "skip_pages": [2, 3]
}
```

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
