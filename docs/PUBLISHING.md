# 把 BusLens 发布到 GitHub

假设本地已经跑通：`python -m unittest discover -s tests -t .` 全绿。
以下命令都在项目根目录执行。

---

## 0. 一次性准备

```bash
git config --global user.name  "Your Name"
git config --global user.email "you@example.com"
git config --global init.defaultBranch main
ssh -T git@github.com          # 用 SSH 推送时先验证
```

HTTPS 方式需要 Personal Access Token；SSH 方式需要先把公钥上传到 GitHub。

---

## 1. 初始化仓库

```bash
git init -b main
git status                 # 确认没有 __pycache__ / .venv
```

如果 `git status` 里出现 `__pycache__`，说明 `.gitignore` 没生效
（Windows 上常见的原因是文件名被存成了 `.gitignore.txt`）。

---

## 2. 检查 .gitignore 与 LICENSE

- `.gitignore`：已排除字节码、虚拟环境、打包产物、IDE 配置、临时导出。
- `.gitattributes`：`* text=auto eol=lf`，避免 CRLF 噪音；`*.png binary` 防止图片被当文本处理。
- `LICENSE`：MIT，年份与署名可改成自己。

```bash
git check-ignore -v buslens/__pycache__/x.pyc   # 有输出即被忽略
```

---

## 3. 首次提交

```bash
git add .
git commit -m "feat: BusLens 1.0.0 - embedded bus signal workbench

- edge-driven decoders for I2C, SPI, UART, PWM and WS2812
- auto-baud recovery from pulse statistics + stop-bit validation
- I2C transaction grouping, clock-stretch stats and register inference
- virtual 24C02 / TMP102 slaves with open-drain wired-AND simulation
- canvas waveform UI with zoom, cursors and inline byte annotations
- zero dependencies, 45 unit tests, GitHub Actions CI"
```

---

## 4. 建远程仓库并推送

```bash
gh auth login
gh repo create buslens --public --source=. --remote=origin \
   --description "Embedded bus signal workbench: decode I2C/SPI/UART/PWM/WS2812 captures into transactions, registers and timing verdicts"

git push -u origin main
```

网页方式：New repository → 名称 `buslens` → **不要**勾选 Add README/.gitignore/license
（本地已有）→ Create → 再按页面提示 `git remote add origin ... && git push -u origin main`。

> 若远程已有提交导致分叉，用 `git pull --rebase origin main`，**不要** `git push -f`。

---

## 5. 发布 v1.0.0

```bash
git tag -a v1.0.0 -m "v1.0.0: first public release"
git push origin v1.0.0
gh release create v1.0.0 --title "BusLens v1.0.0" --notes-file docs/release-notes.md
```

Release 描述建议包含：三行亮点、一张工作台截图、支持的协议表、快速开始命令。

仓库 About 建议填写：

- Description：`Embedded bus signal workbench — I2C/SPI/UART/PWM/WS2812 decoders, virtual slaves, zero dependencies`
- Topics：`embedded` `microcontroller` `logic-analyzer` `protocol-decoder` `i2c`
  `spi` `uart` `ws2812` `python` `vcd` `firmware` `testbench`

---

## 6. 发布后自检

- [ ] 在空目录 `git clone` 后按 README 的步骤能跑起来
- [ ] `python -m unittest discover -s tests -t .` 全绿
- [ ] `python run.py serve` 打开后默认示例能出波形
- [ ] 逐个点开 7 个示例，无控制台报错
- [ ] 「虚拟设备」页发起一次读取，波形能刷新
- [ ] 导入 `examples/capture_i2c_eeprom.vcd` 能正常解码
- [ ] Release 有 tag、说明与截图
- [ ] README 顶部徽章、clone 地址、pyproject 里的 URL 都指向真实仓库

---

## 7. 常见坑

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `CONNECT tunnel failed, response 502` | 代理瞬时故障 / github.com 被拦 | 先原样重试一次；仍失败就检查网络与代理 |
| 推送被拒 `fetch first` | 远程有你本地没有的提交 | `git pull --rebase origin main` |
| 中文文件名乱码 | 未开启 UTF-8 路径 | `git config core.quotepath false` |
| `filemode` 变更刷屏 | Windows/Linux 权限位差异 | `git config core.filemode false` |
| 仓库体积暴涨 | 误提交抓包/VCD/截图 | 补 `.gitignore`，`git rm -r --cached <path>` |
| 提交里带密钥 | token 写进代码 | 立即吊销，再用 `git filter-repo` 清理 |
