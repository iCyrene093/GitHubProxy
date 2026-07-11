# GitHub Release Proxy

一个只允许代理 GitHub Release 的轻量 Web 程序：

- 用户可通过浏览器访问后台白名单中的 GitHub Release 页面。
- 用户可通过代理下载白名单范围内 Release 中的文件。
- 程序会拒绝代理访问非 GitHub Release 页面、非 Release 下载文件、包含点路径片段的 URL，以及未加入白名单范围的 Release。
- 提供后台管理页面添加或删除允许访问的仓库 Release 链接。

## 本地运行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export ADMIN_PASSWORD='请替换为强密码'
export SECRET_KEY='请替换为随机字符串'
flask --app app run --host 0.0.0.0 --port 8000
```

访问：

- 前台：`http://服务器IP:8000/`
- 后台：`http://服务器IP:8000/admin`

后台只接受以下形式的链接；其中 `/releases` 表示允许整个仓库的 Release，`/releases/tag/{tag}` 只允许对应标签：

- `https://github.com/owner/repo/releases`
- `https://github.com/owner/repo/releases/latest`
- `https://github.com/owner/repo/releases/tag/v1.0.0`

## Linux 服务器部署脚本

在服务器上安装代码后，使用 root 执行：

```bash
sudo ADMIN_PASSWORD='请替换为强密码' PORT=8000 ./scripts/deploy-linux.sh
```

脚本会完成以下工作：

1. 安装 Python、venv、pip、git。
2. 创建系统用户 `githubproxy`。
3. 将程序复制到 `/opt/github-release-proxy/app`。
4. 创建虚拟环境并安装依赖。
5. 将敏感环境变量写入仅 root 可读的 `/etc/github-release-proxy.env`。
6. 写入并重启 `systemd` 服务 `github-release-proxy.service`。

常用运维命令：

```bash
sudo systemctl status github-release-proxy.service
sudo systemctl restart github-release-proxy.service
sudo journalctl -u github-release-proxy.service -f
```

如需使用 Nginx 反向代理，可将域名转发到 `http://127.0.0.1:8000`。

## 关键环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ADMIN_PASSWORD` | `admin` | 后台登录密码，生产环境必须修改。 |
| `SECRET_KEY` | 无 | Flask Cookie 签名密钥；未设置时应用会拒绝启动，部署脚本会在安装 Python 后自动生成。 |
| `GITHUB_PROXY_DB` | `./data/github_proxy.sqlite3` | 白名单数据库路径。 |
| `PORT` | `8000` | 开发模式监听端口。 |
| `REQUEST_TIMEOUT` | `30` | 请求 GitHub 的超时时间（秒）。 |

## 安全边界

本程序只会把后台白名单中的 GitHub Release 页面加入可访问范围。`/releases` 白名单允许该仓库的所有 Release；`/releases/tag/{tag}` 只允许对应标签页面及同标签的下载文件。下载接口只接受 `https://github.com/{owner}/{repo}/releases/download/{tag}/{file}` 形式的链接，并且会拒绝包含 `.` 或 `..` 路径片段的 URL。代理页面会在服务端展开 GitHub Release 的资产列表片段，然后重写允许的 Release/下载链接；页面中的其他链接会被替换为不可跳转的 `#blocked`。后台增删白名单需要登录会话中的 CSRF token。
