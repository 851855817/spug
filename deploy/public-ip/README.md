# 使用公网 IP 访问 Spug

本实例入口：<https://47.104.88.147/>。沿用原 Spug 账户和密码；公网 IP 与本机 `127.0.0.1:18080` 属于不同浏览器来源，需要重新登录。HTTP 自动跳转 HTTPS，不需要域名或本机 SSH 转发。

## 部署结构

宿主机 Nginx 监听 TCP 80／443，转发到原 Docker 映射 `127.0.0.1:8080`。原 Spug 容器、应用版本、数据卷、数据库、worker 和本机 SSH 入口保留。不要把 8080、9001、9002 或数据库端口改成公网监听。

- `nginx-bootstrap.conf`：初始仅开放 HTTP ACME 验证和连通探针，其他路径返回 404。
- `nginx.conf`：最终 HTTPS 代理、HTTP 跳转、ACME 验证、API／WebSocket 和流式上传配置。
- `spug-ip-certificate.service`／`.timer`：每 6 小时检查续期，随机延迟最多 15 分钟，开机补跑错过的日历触发。
- `spug-nginx-reload`：安装到 Certbot 的 deploy hook 目录，续签成功后先检查配置再 reload Nginx。

新增访问日志只记录请求方法、路径、状态等，不记录查询参数；外层覆盖客户端传入的 X-Forwarded-For，内层 Spug 继续沿用原 IP 校验。WebSocket 的拒绝访问也可以验证代理路由，不能据此声称已测试真实远程终端。

## 首次配置顺序

以下命令只适用于本实例已核实的布局；其他服务器需要检查现有 Nginx、端口和配置，不能覆盖其他站点。先保存应用版本、容器／进程、运行文件、业务摘要和宿主机入口前态。

1. 安装宿主机 Nginx 和 `python3-venv`，避免连带升级或重启原 Docker 服务。
2. 安装 bootstrap 到 `/etc/nginx/sites-available/spug-public-ip.conf`，在 `sites-enabled` 启用；该实例原先没有宿主机 Nginx，仅移除默认启用站点链接。创建 `/var/lib/spug-acme/.well-known/acme-challenge`。
3. 用独立虚拟环境安装经核实支持 IP 的 Certbot；本次使用 5.8.0，系统自带旧客户端不适用。
4. 从外部验证 `http://47.104.88.147/_spug_public_probe` 确实返回 `spug-public-ip-ready`，不能只根据 TCP 能连接判断安全组。
5. 先执行以下签发命令附加 `--dry-run`，成功后移除该参数签发正式证书：

```sh
/opt/spug/public-access/certbot-venv/bin/certbot certonly \
  --webroot -w /var/lib/spug-acme \
  --ip-address 47.104.88.147 --required-profile shortlived \
  --cert-name spug-public-ip --agree-tos \
  --register-unsafely-without-email --non-interactive
```

证书与私钥分别由 Certbot 保存在 `/etc/letsencrypt/live/spug-public-ip/fullchain.pem` 和 `privkey.pem`；私钥不得复制到仓库。IP 证书有效期短，必须启用自动续期。未设置电子邮件，续签故障需要通过服务状态或日志查看。

6. 将最终 `nginx.conf` 暂存为候选，使用完整 Nginx 配置执行 `nginx -t`。保留 bootstrap 副本后替换当前站点；再次语法检查成功才 reload。失败恢复原站点并重新检查。
7. 安装续期文件并验证：

```sh
install -m 644 spug-ip-certificate.service /etc/systemd/system/
install -m 644 spug-ip-certificate.timer /etc/systemd/system/
install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
install -m 750 spug-nginx-reload /etc/letsencrypt/renewal-hooks/deploy/
systemd-analyze verify /etc/systemd/system/spug-ip-certificate.service \
  /etc/systemd/system/spug-ip-certificate.timer
systemctl daemon-reload
systemctl enable --now spug-ip-certificate.timer
/opt/spug/public-access/certbot-venv/bin/certbot renew \
  --cert-name spug-public-ip --non-interactive --dry-run --run-deploy-hooks \
  --no-random-sleep-on-renew
```

从 Windows 传输 hook 时保持 LF 换行，使用 `sh -n` 及可执行权限检查。没有可执行 deploy hook 时，证书文件虽可续签，Nginx 仍可能继续提供旧证书。service 关闭 Certbot 内置的最长 8 分钟随机等待，避免超过 5 分钟超时；错峰由 timer 的 15 分钟随机延迟承担。

## 验收与日常检查

从外网验证 HTTPS 的 IP 身份和证书信任、HTTP 跳转、首页、静态文件、版本、登录入口、受保护 API 拒绝未登录访问及 WebSocket 路由。不要使用跳过证书校验的请求作为 HTTPS 验收。未使用真实账户登录或执行客户任务的测试需要明确记录其范围。

```sh
nginx -t
systemctl is-active nginx spug-ip-certificate.timer
systemctl list-timers spug-ip-certificate.timer
systemctl status spug-ip-certificate.service --no-pager
journalctl -u spug-ip-certificate.service --since yesterday --no-pager
```

`certbot renew` 在证书未到续期窗口时正常跳过；timer 活跃表示调度已启用，不等于未来每次续期一定成功。云安全组需要 TCP 80／443；80 保留给 HTTP 跳转和后续 ACME 验证。服务器关机、IP 变化或拦截 ACME 路径会影响续签。

## 停用公网入口

本次部署前宿主机没有 Nginx，所以可单独停用本次入口：

```sh
systemctl disable --now spug-ip-certificate.timer
systemctl disable --now nginx.service
```

停止前确认宿主机 Nginx 后续没有承载其他站点。原 `127.0.0.1:8080` 服务及本机 SSH 转发仍可使用。无需重建容器、回退应用或恢复数据库。配置前态与聚合保全证据存放在 `/opt/spug/public-access/` 的私有基线目录，不能公开该目录。
