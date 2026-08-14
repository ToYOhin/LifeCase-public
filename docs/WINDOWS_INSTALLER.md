# Windows Installer

LifeCase 提供两个独立的 Windows 可执行文件：

- `LifeCaseSetup.exe`：当前用户安装器；
- `LifeCase.exe`：安装后的应用启动器。

安装器无需管理员权限，默认安装到 `%LOCALAPPDATA%\Programs\LifeCase`，创建桌面和开始菜单快捷方式，并登记到“应用和功能”。启动器初始化用户数据目录、启动单一 loopback API/Web 服务并打开浏览器。卸载默认保留 `%LOCALAPPDATA%\LifeCase`。

## 构建

先构建自包含应用：

```powershell
.\scripts\build-windows-app.ps1
```

再构建安装器：

```powershell
.\scripts\build-windows-installer.ps1 -ApplicationRoot <app-dist-directory>
```

脚本使用项目锁定依赖和 PyInstaller 打包，不改变 API、Provider 默认值或业务行为。构建产物不应提交到 Git；正式分发时应同时发布 SHA-256 校验文件。

## 验收

```powershell
.\scripts\test-windows-installer.ps1 -SetupExe <LifeCaseSetup.exe>
```

验收覆盖安装、快捷方式、启动、健康检查、Web 响应、停止、卸载和默认数据保留。测试使用隔离的当前用户目录，不需要管理员权限。

## 安全边界

- 安装器和启动器只接受受控的应用与数据目录；
- 服务只监听 loopback；
- 应用目录与可变用户数据分离；
- 卸载不会静默删除用户数据；
- 未签名构建可能触发 Windows SmartScreen“未知发布者”提示。
