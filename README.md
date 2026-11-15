# 抱拙投资研究

从[原有技能](SKILL.md)和[产业投资研究体系](references/industry-investment-research.md)出发，沿康波长期结构、朱格拉设备投资、企业盈利和现金、市场定价持续研究。

开发入口：[AGENTS.md](AGENTS.md)。完整流程与数据格式：[研究开发工作流](references/development-workflow.md)。

## 日常命令

只需 Python 3.9+ 标准库；在仓库根目录运行：

```sh
# 查看已有版本
python3 scripts/research.py list

# 新建草稿，日期由研究任务明确指定；已有同日版本会被拒绝覆盖
python3 scripts/research.py new 002008-hans-laser --ticker 002008.SZ --as-of 2026-11-16 --name 大族激光

# 只检查本次更新的模型
python3 scripts/research.py check references/research/companies/002008-hans-laser/2026-11-15-model.json --strict

# 检查全部模型并输出计算结果
python3 scripts/research.py check --strict --json

# 开发工具变更后的统一验证入口
make check
```

草稿中的未知值保持空白；采集证据、填入模型、人工复核后才将 `status` 改为 `reviewed`。严格检查拒绝未完成草稿。脚本不联网、不更新行情，也不证明来源真实或投资判断正确。

## 方法与已有案例

- [投资决策与复盘模板](references/decision-template.md)、[产业研究底稿](references/industry-research-workbook.md)。
- [思瑞浦研究](references/research/companies/688536-3peak/2026-11-15.md)。
- [大族激光研究](references/research/companies/002008-hans-laser/2026-11-15.md)。
- [文章索引](references/research/08-article-index.md)；正文检索：`python3 scripts/article_library.py search '资本支出|朱格拉' --limit 5`。

更新研究时保留旧日期版本。现有两种历史模型由兼容适配器复算；新草稿使用统一格式，不批量改写历史材料。
