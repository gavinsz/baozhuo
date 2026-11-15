# Harness: 抱拙投资研究

目标：在既有投资体系上持续开发研究工具，形成有日期、有来源、可复算的公司研究。

触发：在本仓库新增、更新或复核投资研究，或修改文章检索、财务模型与验证工具时，使用 `$baozhuo-contribute`，或直接读取 `.agents/skills/baozhuo-contribute/SKILL.md`。

入口与约束：

- 投资方法以 `SKILL.md` 及其新增研究体系为基础；保留原文。普通研究任务只新增案例，不改写原技能。
- 开发流程、数据契约与检查边界见 `references/development-workflow.md`。
- 默认单线程；只有用户或适用指令明确要求委派时才使用子代理，先划分文件所有权。
- 原始材料与历史案例不覆盖；财报事实、管理层指引、研究假设和未知项分别表达。
- 本仓库不自动下单、推送投资指令或对外发布研究。

实际验证命令（Python 3.9+ 标准库，无需安装依赖）：

```sh
python3 scripts/research.py list
python3 scripts/research.py check --strict
python3 -m unittest discover -s tests -v
make check
```

仅修改研究案例时先运行指定模型的 `check --strict`；修改脚本或模型契约时运行 `make check`。通过机械检查仍需人工核对来源、周期传导与结论。

中间产物需要保留时使用 `_workspace/00_input`、`01_explore`、`02_plan`、`03_outputs`、`04_validation`；重跑写新运行目录，不覆盖旧结果。

| 日期 | 变更 | 范围 | 原因 |
| --- | --- | --- | --- |
| 2026-11-15 | 新建投资研究开发工作流 | 仓库技能、研究CLI、验证及测试 | 将既有研究方法落实为可重复执行的流程 |
