# Windows 本机演示通道

演示地址为 <http://127.0.0.1:18080/>。服务器上的 Spug 只监听服务器本机 `127.0.0.1:8080`；本机使用现有用户 SSH 密钥转发。当前示例连接 `root@47.104.88.147`，密钥路径 `%USERPROFILE%\.ssh\miaoyinju_rsa`。更换环境时先调整 `Spug-Tunnel.ps1` 中对应值。

将本目录的三个脚本放在固定目录，双击 `Open-Spug.cmd`。脚本为当前 Windows 用户注册并启动 `Spug-Demo-Tunnel-18080` 任务，使用 Interactive／Limited 运行级别，不保存密码；之后登录 Windows 时自动启动。任务动作包含脚本绝对路径，部署后应保留该目录。

任务独立于开发命令会话，SSH 每 15 秒保活、连续 3 次无响应后退出；退出后等待 5 秒重连。任务本身没有运行时间上限，异常退出由 Windows 最多重启 3 次。重复启动忽略新实例，脚本另用本机会话互斥锁避免重复连接。正常断线重连循环不限制次数。电脑离线／休眠期间无法访问；网络恢复后重试，页面请求可能需要刷新。

`Open-Spug.cmd` 和任务动作仅为各自 PowerShell 进程指定 RemoteSigned 策略，以运行本地脚本；不会修改系统或用户的全局执行策略。SSH 仍验证现有主机指纹，仅绑定本机回环地址。脚本没有打开服务器公网 Web 端口。

诊断：

```powershell
Get-ScheduledTask -TaskName Spug-Demo-Tunnel-18080
Get-ScheduledTaskInfo -TaskName Spug-Demo-Tunnel-18080
Get-Content "$env:TEMP\spug-tunnel-watchdog.log" -Tail 20
Invoke-WebRequest http://127.0.0.1:18080/ -UseBasicParsing
```

日志超过 1 MiB 时轮换，保留当前及上一份。手动启动可执行 `Start-ScheduledTask -TaskName Spug-Demo-Tunnel-18080`，或再次双击启动器。

停用时在 Windows 任务计划程序中禁用并结束 `Spug-Demo-Tunnel-18080`。如仍有 SSH 子进程，先用 `Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 18080 -State Listen` 获取 PID，核实对应 `ssh.exe` 命令行确为本通道后只结束该 PID。需要移除时，删除该计划任务即可，原 SSH 密钥、服务器和应用数据均保留。

## 2026-10-23 故障及验证

两次故障现场均为本机 18080 无监听／无 SSH 进程，服务器应用与数据库 healthy、HTTP 200、无 OOM／重启。前一次命令会话的 SSH 返回 `Connection to 47.104.88.147 closed by remote host`；服务器无 channel／unused-connection 超时设置。无法从现有日志确定连接被关闭的具体环节，已确定旧启动方式没有监督与自动重连。

2026-10-23 13:40（UTC+8）在用户电脑安装上述任务，Windows PowerShell 5.1 实际执行和语法检查通过。主动结束经过 PID、命令行及父进程核验的转发子进程，日志记录 SSH 退出后 5 秒发起重连，新 PID 接管端口，HTTP 200；监督进程 PID 保持不变。再次启动任务仍只有一个监听，任务为 Running、IgnoreNew、登录触发启用、时间上限 PT0S。监督进程父级为 Windows `svchost.exe`，独立于工具命令会话。浏览器重新加载面板任务页面正常。

本次只修改本机访问通道及启动器，服务器镜像仍为 `spug-baota:2350559`，没有重启服务器、应用或修改客户面板。未模拟系统注销／登录、整机重启或真实网络中断；登录触发设置已经核对，恢复测试为主动结束 SSH 子进程。
