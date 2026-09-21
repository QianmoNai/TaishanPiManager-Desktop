# v2.33 RGB 控制插件验证记录

- 新建独立版本目录：`TaishanPiManager-Desktop-v2.33`，基于 v2.32 复制，未修改旧版本。
- 插件中心新增“RGB 灯控制”入口。
- 支持读取板载 RGB 当前状态。
- 支持红、绿、蓝三个通道独立亮灭。
- 支持关闭、红、绿、蓝、黄、青、紫、白八种预设颜色。
- 通过 ADB 控制板端 `/sys/class/leds/rgb-led-r/g/b/brightness`。
- 每次写入前将 LED trigger 设为 `none`，避免系统触发器覆盖手动状态。
- 已通过 `python -m py_compile` 语法校验。
- 已通过 `python -m unittest test_rgb_control.py`：3 项测试通过。
- 完整 Qt 界面测试需要当前环境安装 PySide6 后执行。
- v2.33 PyInstaller 打包成功，生成 `dist/TaishanPiManager.exe`，并复制到版本根目录。\n- 打包后的 EXE 已启动检查，进程返回无错误退出码信息。
