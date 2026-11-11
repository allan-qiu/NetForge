# NetForge V2.4.0

面向网络技术备考的多人练习平台，包含学员端、管理后台、题库与错题本、模拟考试、排行榜及可选的 DeepSeek 学习分析。

本源码包附带 9 道原创示例题，便于体验。可以在管理后台的「题库管理」中使用 [Excel 导入模板](docs/question-import-template.xlsx) 预检、导入并发布有权使用的题目。GitHub 仓库可托管此项目源码。运行网站需要 Python 服务和可写的 SQLite 数据目录。

## 本地启动

需要 Python 3.11 或更高版本。运行时只用 Python 标准库。在解压目录运行以下命令，先把管理员密码和注册码替换为你自己的值。首次启动时，服务会用它们初始化账号和注册入口。

Windows PowerShell：

```powershell
$env:NETFORGE_ADMIN_USERNAME = "admin"
$env:NETFORGE_ADMIN_PASSWORD = "replace-with-a-long-unique-password"
$env:NETFORGE_REGISTRATION_CODE = "replace-with-your-registration-code"
python .\server.py
```

macOS / Linux：

```bash
export NETFORGE_ADMIN_USERNAME=admin
export NETFORGE_ADMIN_PASSWORD='replace-with-a-long-unique-password'
export NETFORGE_REGISTRATION_CODE='replace-with-your-registration-code'
python3 server.py
```

- [学员端](http://127.0.0.1:8787/)使用注册码注册。
- [管理后台](http://127.0.0.1:8787/admin)使用首次启动设置的管理员账号登录。
- 试用模拟考试时，可选 5 道题。题库管理支持继续增加题目。
- 数据默认写入 `data/`，可通过 `NETFORGE_DATA_DIR` 指定位置。配置项见 [.env.example](.env.example)。本地命令直接使用环境变量，示例文件不会被自动加载。
- AI 功能需要在启动服务前设置 `DEEPSEEK_API_KEY`。其余练习功能可独立使用。

## 测试

```text
python -m unittest discover -s tests -q
node tests/test_document_dialog.js
node tests/test_personal_analysis.js
```

Node.js 只用于前端测试。接口细节见 [V2_API_CONTRACT.md](V2_API_CONTRACT.md)。

## Ubuntu 部署

在计划用于此应用的 Ubuntu 24.04 服务器上，从项目目录运行 `sudo bash deploy/install.sh`。脚本配置 Nginx 和 systemd，使用 `/var/lib/netforge` 保存数据，升级前会备份并验证已有数据库。首次安装生成的管理员配置和注册码保存在 `/etc/netforge.env`。公网使用时，先配置域名和 HTTPS，再在环境文件中启用 `NETFORGE_COOKIE_SECURE=1`。