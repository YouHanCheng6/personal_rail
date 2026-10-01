# 沿线 · 交通查询

一个本地运行的火车、飞机、汽车资料查询工具。输入出发地、目的地和日期，分别查看班次、席别、公开价格与来源。无需大模型，也不读取 DeerFlow 的模型配置。

![沿线列车背景](personal_rail/static/images/morning-train.webp)

## 使用

需要 Python 3.11 或更高版本。克隆仓库后，在仓库根目录执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

Windows 使用 `python -m venv .venv`，然后执行 `.venv\Scripts\python -m pip install -r requirements.txt` 和 `.venv\Scripts\python run.py`。

打开 http://127.0.0.1:2036/ 。终端保持运行，Ctrl+C 停止。默认只监听本机。

## 查询内容

- 火车：所选两地的直达车次、平台现成铁路换乘及各段全部席别。输入“龙川”会查询龙川、龙川西；输入明确车站则保留车站范围。保留平台原有换乘，不自行拼接车次，不与飞机或汽车组合。
- 飞机：目的地没有目录内机场时，查询选定半径内已接入的机场城市，包括粤东周边梅州、惠州、揭阳等。每条显示实际起降机场；机场距离为直线距离，不是道路里程。不自动组合后续地面交通。
- 汽车：只查输入两地的汽车资料。此公开时刻入口未提供指定日期查询；其他日期只作为参考资料。不会把参考页空列表说成所选日期无车。不查询其他城市的汽车作为替代；未取得数据与没有运营班次是两回事。缺到达时间或日期不符的资料明确标注。

每页25条，只是分页，其他已取得的资料仍可翻页查看或导出。支持编号/站名筛选、出发时间和已公布价格排序。没有路线打分、预算淘汰、精选名额、40条结果上限或模型推荐。

## 可选地图配置

将根目录 `.env.example` 复制为 `personal_rail/.env`，填写高德 **Web服务 Key** 的 `AMAP_WEB_KEY`。密钥只在后端使用，不上传仓库。地图用于具体地点定位和机场位置核实；无 Key 时仍能用目录内城市或车站查询，机场地理信息会标注未核实。没有大模型配置步骤。

## 动态航班页面

完整动态列表需要 Playwright 浏览器运行环境：

```sh
.venv/bin/python -m pip install -r requirements-browser.txt
PLAYWRIGHT_BROWSERS_PATH=personal_rail/.local/browsers .venv/bin/python -m playwright install chromium
```

PowerShell 先执行 `$env:PLAYWRIGHT_BROWSERS_PATH='personal_rail/.local/browsers'`，再执行对应 Python 的 `-m playwright install chromium`。动态页面不可用时，明确显示仅取得的初始部分资料，不伪称已覆盖全部航班。

## 数据范围

使用去哪儿公开日期页、同程公开航班页、携程公开汽车时刻页以及官方公告索引。查询遵守来源访问规则，缓存通常10分钟；同源限频可能使多个机场方向等待数分钟。可随时取消，已取得的资料保留。

“全部”仅指本次已接入来源取得并通过日期/字段核验的记录，不是全国全部运营班次或可售库存保证。机票起价不等于完整含税成交价；缺失价格不会按零元展示。新界面不规划换乘、不订票、不登录票务账号。

## 开发与数据

`app.py` 提供 `/api/search` 和流式进度；`lookup.py` 只确定查询范围并归集资料；`service.py`、`dated.py`、`multimodal.py` 负责原有采集。`static/` 无构建依赖。

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest personal_rail/tests -q
```

Windows 使用相应 `.venv\Scripts\python` 路径。开发检查还包括 Ruff 和 `node --check personal_rail/static/app.js`。

旧 Agent、路线组合、推荐评分、接驳规划和后台监测代码已移除。`.local/` 中既有缓存、历史记录及旧监测文件保留；旧监测不会自动执行。`.env`、`.local`、私有地址与查询导出均不应提交到公共仓库。

## 当前已知限制

- 查询过程先汇总各来源，结束时统一去重，因此中途计数可能高于最终结果。
- 汽车公开时刻入口尚不支持指定日期查票；其他日期只作参考，不据此判断所选日期是否有车。
- 仅显示平台提供的铁路换乘，不自动生成平台未列出的换乘。
