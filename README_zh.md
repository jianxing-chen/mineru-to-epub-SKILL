# Mineru → EPUB

[English](./README.md)

将 Mineru 处理后的 PDF 转换为 Kindle 可读的 EPUB，支持弹窗脚注、章节导航和内嵌图片。

## 解决什么问题

[Mineru](https://github.com/opendatalab/MinerU) 能从 PDF 中提取出高质量的 JSON 块树 + Markdown + 图片，但输出是按页面组织的——脚注散布在页面底部，编号每页重置。这个工具把页面级输出转化为回流式电子书：

- **自动发现脚注模式**（圈号 ①②③、上标数字 ¹²³、符号 *†‡），无需手动配置
- **跨页脚注匹配**——修复标记和正文落在不同页面的问题，支持 ±4 页滑动窗口
- **多类型脚注分区展示**——译者注、参考文献、作者注分别标注、各自独立编号
- **跨页段落合并**——自动修复因 PDF 分页导致的一句话断成两截
- **枚举序号过滤**——识别并过滤被误识别为脚注的列表序号（如段落开头的 ①②③）
- **LLM 语义判定**——Profile 输出纯观测数据，由 LLM Agent 判定每个 pattern 的语义标签

## 快速开始

```bash
# 1. 发现脚注模式（零配置）
python3 scripts/convert.py --profile --parts part1/ part2/ > profile.json

# 2. LLM 阅读 profile.json，判定每个 pattern 的语义，生成 footnote_config.json

# 3. 转换
python3 scripts/convert.py --convert \
  --parts part1/ part2/ \
  --footnote-config footnote_config.json \
  --config chapters_config.json \
  --output book.epub \
  --title "书名" --author "作者" \
  --cover-pdf original.pdf
```

## 通过 Mineru API 处理新 PDF

```bash
# 配置 API key（三种方式任选）
export MINERU_API_KEY="sk-xxx"      # 环境变量
# 或
echo "sk-xxx" > ~/.mineru_api_key   # 文件持久化
# 或
--api-key sk-xxx                     # 命令行参数

# 处理 PDF（>200 页自动拆分）
python3 scripts/mineru_api.py process book.pdf --output ./output/
```

优先级：`--api-key` > `MINERU_API_KEY` > `~/.mineru_api_key`

## Profile 输出示例

```json
{
  "detected_patterns": [
    {
      "id": "p1",
      "marker_format": "circled_ideograph",
      "ref_count": 111,
      "body_count": 101,
      "body_samples": [
        "① 文中引用的论文如下：Robertson和Bowlby（1952）…",
        "① 带有弗洛伊德笔记的版本保存于哥伦比亚大学…"
      ],
      "body_language": {"cjk": 0.695, "latin": 0.106},
      "body_avg_length": 106.7
    },
    {
      "id": "p2",
      "marker_format": "superscript_digit",
      "ref_count": 1913,
      "body_count": 2007,
      "body_language": {"cjk": 0.12, "latin": 0.88},
      "body_avg_length": 28.0
    }
  ]
}
```

## LLM 判定指南（在 SKILL.md 中）

Agent 根据以下信号判定每个 pattern 的标签：

| 信号 | 倾向「解释性注释」 | 倾向「文献引用」 |
|------|------------------|----------------|
| marker_format | circled_ideograph | superscript_digit |
| body_language | cjk > 0.6, latin < 0.1 | latin > 0.6 |
| body_avg_length | > 60 字 | < 30 字 |
| body 内容特征 | 含人物生卒年、术语解释 | 含 "Author, Year"、"同上" |

## 支持的脚注模式

| 标记 | 常见语义 | 显示样式 |
|------|---------|---------|
| ①②③ | 译者注 | ①（原生圈号） |
| ¹²³ | 参考文献 | [1]（方括号） |
| *†‡ | 作者注 | *（星号） |

脚本**不做任何语义假设**——所有标签由 LLM Agent 按 SKILL.md 指导判定。

## 输出示例

```
── 译者注 ──────────       ── 参考文献 ──────────
① 边陲福特主义是...        [1] Freestone, 2000.
② 威廉·布莱克(1757—       [2] Wohl, 1977, 234.
   1827), 英国诗人...      [3] 同上, 238.
```

## 算法要点

- **脚注匹配**：按 type 隔离匹配，圈号一对一（防止序号假阳性），数字共享匹配（处理 ibid 多处引用），±4 页滑动窗口
- **段落合并**：前段不以 。！？结尾 + 后段以短片段开头 → 合并为一个 `<p>`
- **枚举过滤**：段落开头的连续圈号序列（①②③④）跨页检测 → 剔除
- **多编号拆分**：Mineru 将多条脚注合并为单个 text 块时（如 `"85 同上…86 Fabian…"`），自动拆分

## 依赖

```bash
pip install pypdfium2 Pillow pikepdf requests
```

## 文件结构

```
scripts/
  convert.py        主工具（--profile + --convert）
  mineru_api.py     Mineru API 客户端（拆分 → 上传 → 轮询 → 下载）
  analyze.py        诊断分析器（脚注、章节、格式问题）
  format_fix.py     后处理（段落合并、标题去重）
SKILL.md            Agent 工作流指南
README.md           英文说明
README_zh.md        中文说明（本文件）
```
