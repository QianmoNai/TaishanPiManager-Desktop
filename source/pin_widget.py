"""Responsive native Qt panel for GPIO/I2C/SPI/PWM."""
from pathlib import Path
import sys
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QComboBox, QSpinBox, QCheckBox, QPlainTextEdit, QSizePolicy, QLabel


PINOUT_IMAGE = (Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent) / "pinout-taishanpi.png"


def _linux_gpio(name):
    bank, bit = name.split("_", 1)
    controller = int(bank[4:]); offset = (ord(bit[0]) - ord("A")) * 8 + int(bit[1:])
    return controller * 32 + offset


# GPIO-capable physical pins from the TaishanPi 40-pin header drawing.
HEADER_I2C_BUSES = (
    ("排针 3/5 · I2C2_M0 · SDA GPIO0_B6 / SCL GPIO0_B5", "i2c-2"),
    ("排针 27/28 · I2C3_M1 · SDA GPIO3_B6 / SCL GPIO3_B5", "i2c-3"),
)
HEADER_SPI = "排针 19/21/23/24 · SPI3_M1 · MOSI GPIO4_C3 / MISO GPIO4_C5 / CLK GPIO4_C2 / CS0 GPIO4_C6"
HEADER_PWM = (
    ("排针 12 · PWM14_M0 · GPIO3_C4 · pwmchip2", "pwmchip2"),
    ("排针 32 · PWM15_IR_M0 · GPIO3_C5 · 未发现对应控制器", "pwmchip2"),
    ("排针 33/35 · PWM8_M0 · GPIO3_B1/B2 · pwmchip0", "pwmchip0"),
)

HEADER_GPIO_PINS = (
    (7, "GPIO1_A4", "GPIO"), (8, "GPIO3_B7", "UART3_TX_M1"), (10, "GPIO3_C0", "UART3_RX_M1"),
    (11, "GPIO3_A1", "GPIO"), (12, "GPIO3_C4", "PWM14_M0"), (13, "GPIO3_A2", "GPIO"),
    (15, "GPIO3_A3", "GPIO"), (16, "GPIO3_A4", "GPIO"), (18, "GPIO3_A5", "GPIO"),
    (19, "GPIO4_C3", "SPI3_MOSI_M1"), (21, "GPIO4_C5", "SPI3_MISO_M1"), (22, "GPIO3_A6", "GPIO"),
    (23, "GPIO4_C2", "SPI3_CLK_M1"), (24, "GPIO4_C6", "SPI3_CS0_M1"), (26, "GPIO3_A7", "GPIO"),
    (27, "GPIO3_B6", "I2C3_SDA_M1"), (28, "GPIO3_B5", "I2C3_SCL_M1"), (29, "GPIO3_B0", "GPIO"),
    (31, "GPIO3_C2", "GPIO"), (32, "GPIO3_C5", "PWM15_IR_M0"), (33, "GPIO3_B1", "PWM8_M0"),
    (35, "GPIO3_B2", "PWM8_M0"), (37, "GPIO0_B7", "GPIO"), (38, "GPIO3_B3", "GPIO"),
    (40, "GPIO3_B4", "GPIO"),
)


class PinPanel(QWidget):
    def __init__(self, owner, label, button, card):
        super().__init__(); self.owner = owner; self.generation = 0; self._controls = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(14)
        top = QHBoxLayout(); top.addWidget(button('‹ 返回插件中心', owner.close_plugin)); top.addStretch()
        self.state = label('未读取硬件资源', 'subtle'); top.addWidget(self.state); root.addLayout(top)

        frame, box = card(); row = QHBoxLayout(); row.addWidget(label('引脚助手', 'section')); row.addStretch()
        self.refresh_btn = button('刷新硬件资源', self.refresh, 'primary', 'refresh'); row.addWidget(self.refresh_btn); box.addLayout(row)
        self.model = label('连接泰山派后读取 GPIO、I2C、SPI、PWM 节点。', 'subtle', True); box.addWidget(self.model)
        box.addWidget(label('输出和配置操作只作用于当前会话；请确认 3.3V 电平、外部供电和引脚复用，避免与 UART、调试口或其他外设冲突。', 'caption', True)); root.addWidget(frame)

        frame, box = card(); box.addWidget(label('GPIO', 'section')); box.addWidget(label('按 40 针排针物理脚位选择 GPIO，界面会显示 GPIO 名称和 Linux 编号。改变方向和输出值前会再次确认。', 'subtle', True))
        grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(9)
        self.gpio_number = QComboBox(); self.gpio_number.setAccessibleName('排针 GPIO'); self.gpio_number.setMinimumWidth(235)
        for physical, name, function in HEADER_GPIO_PINS:
            number = _linux_gpio(name); suffix = '' if function == 'GPIO' else f' · {function}'
            self.gpio_number.addItem(f'排针 {physical} · {name} · Linux {number}{suffix}', number)
        self.gpio_action = QComboBox(); self.gpio_action.addItem('读取', 'read'); self.gpio_action.addItem('设为输入', 'input'); self.gpio_action.addItem('设为输出', 'output'); self.gpio_action.addItem('写入电平', 'write')
        self.gpio_value = QComboBox(); self.gpio_value.addItem('低电平 0', 0); self.gpio_value.addItem('高电平 1', 1)
        self.gpio_btn = button('执行 GPIO 操作', self.run_gpio, 'primary'); self.gpio_result = label('尚未读取 GPIO 状态。', 'caption', True)
        grid.addWidget(label('排针脚位', 'caption'), 0, 0); grid.addWidget(self.gpio_number, 0, 1); grid.addWidget(label('操作', 'caption'), 0, 2); grid.addWidget(self.gpio_action, 0, 3); grid.addWidget(label('电平', 'caption'), 0, 4); grid.addWidget(self.gpio_value, 0, 5); grid.addWidget(self.gpio_btn, 0, 6); box.addLayout(grid); box.addWidget(self.gpio_result); root.addWidget(frame)

        frame, box = card(); box.addWidget(label('I2C', 'section')); box.addWidget(label('扫描当前设备上已发现的 I2C 总线，仅执行地址探测，不写入寄存器。', 'subtle', True))
        row = QHBoxLayout(); self.i2c_bus = QComboBox(); self.i2c_bus.setAccessibleName('排针 I2C 总线')
        for title, bus in HEADER_I2C_BUSES: self.i2c_bus.addItem(title, bus)
        self.i2c_scan_btn = button('扫描地址', self.scan_i2c, 'primary'); row.addWidget(self.i2c_bus, 1); row.addWidget(self.i2c_scan_btn); box.addLayout(row)
        self.i2c_output = self.console('扫描结果会显示在这里。'); self.i2c_output.setMinimumHeight(120); box.addWidget(self.i2c_output); root.addWidget(frame)

        frame, box = card(); box.addWidget(label('SPI / PWM', 'section')); box.addWidget(label('SPI 先提供设备节点查看；PWM 支持读取状态和设置周期、占空比、使能。', 'subtle', True))
        self.spi_output = label('SPI 排针：' + HEADER_SPI + ' · 尚未读取设备节点', 'caption', True); box.addWidget(self.spi_output)
        grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(9)
        self.pwm_chip = QComboBox(); self.pwm_chip.setAccessibleName('排针 PWM');
        for title, chip in HEADER_PWM: self.pwm_chip.addItem(title, chip)
        self.pwm_channel = QSpinBox(); self.pwm_channel.setRange(0, 31); self.pwm_period = QSpinBox(); self.pwm_period.setRange(1, 1_000_000_000); self.pwm_period.setValue(20_000_000); self.pwm_period.setSuffix(' ns'); self.pwm_duty = QSpinBox(); self.pwm_duty.setRange(0, 20_000_000); self.pwm_duty.setValue(10_000_000); self.pwm_duty.setSuffix(' ns'); self.pwm_enable = QCheckBox('启用'); self.pwm_btn = button('读取 PWM', self.read_pwm, 'secondary'); self.pwm_apply_btn = button('应用 PWM', self.apply_pwm, 'primary'); self.pwm_result = label('尚未读取 PWM 状态。', 'caption', True)
        for widget in (self.pwm_chip, self.pwm_channel, self.pwm_period, self.pwm_duty, self.pwm_enable, self.pwm_btn, self.pwm_apply_btn): self._controls.append(widget)
        grid.addWidget(label('芯片', 'caption'), 0, 0); grid.addWidget(self.pwm_chip, 0, 1); grid.addWidget(label('通道', 'caption'), 0, 2); grid.addWidget(self.pwm_channel, 0, 3); grid.addWidget(label('周期', 'caption'), 1, 0); grid.addWidget(self.pwm_period, 1, 1); grid.addWidget(label('占空比', 'caption'), 1, 2); grid.addWidget(self.pwm_duty, 1, 3); grid.addWidget(self.pwm_enable, 1, 4); grid.addWidget(self.pwm_btn, 0, 5); grid.addWidget(self.pwm_apply_btn, 1, 5); box.addLayout(grid); box.addWidget(self.pwm_result); root.addWidget(frame)

        frame, box = card(); box.addWidget(label('泰山派 40 针排针引脚图', 'section'))
        self.pinout_image = QLabel(); self.pinout_image.setAlignment(Qt.AlignmentFlag.AlignCenter); self.pinout_image.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred); self.pinout_image.setMinimumHeight(250)
        self.pinout_pixmap = QPixmap(str(PINOUT_IMAGE))
        if self.pinout_pixmap.isNull():
            self.pinout_image.setText('引脚图资源未找到')
        else:
            self.pinout_image.setToolTip('泰山派 40 针排针：供电、GPIO、I2C、SPI、PWM、UART3 等复用信息')
            QTimer.singleShot(0, self._scale_pinout)
        box.addWidget(self.pinout_image)
        box.addWidget(label('图中标注了 3.3V、5V、GND、GPIO、I2C、SPI、PWM 和 UART3 等复用信息。UART3_M1 对应物理 8 脚 TX、10 脚 RX。需要修改设备树或固定引脚复用时，请导出配置后在 WSL 的泰山派 SDK 中手动编译；本工具不会在线修改固件。', 'caption', True)); root.addWidget(frame); root.addStretch()
        self._controls += [self.refresh_btn, self.gpio_number, self.gpio_action, self.gpio_value, self.gpio_btn, self.i2c_bus, self.i2c_scan_btn]

    def _scale_pinout(self):
        if self.pinout_pixmap.isNull(): return
        width = max(220, self.pinout_image.width() - 8)
        scaled = self.pinout_pixmap.scaled(width, 1200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.pinout_image.setPixmap(scaled)
        self.pinout_image.setMinimumHeight(scaled.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'pinout_image'): self._scale_pinout()

    def console(self, placeholder):
        edit = QPlainTextEdit(); edit.setReadOnly(True); edit.setPlaceholderText(placeholder); edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap); return edit

    def controls(self):
        return list(dict.fromkeys(self._controls))

    def active(self):
        return False

    def refresh(self):
        if not self.owner.require_device() or self.owner.busy: return
        self.generation += 1; generation = self.generation; self.state.setText('正在读取硬件资源…')
        self.owner.work(lambda: self.owner.call('pin-inventory'), lambda data: self.render(data, generation), '正在读取 GPIO / I2C / SPI / PWM 资源…')

    def render(self, data, generation=None):
        if generation is not None and generation != self.generation: return
        self.model.setText(data.get('model', '未知设备'))
        available_i2c = set(data.get('i2cdev') or data.get('i2c') or [])
        buses = [bus for _, bus in HEADER_I2C_BUSES if not available_i2c or bus in available_i2c]
        self.i2c_bus.blockSignals(True); self.i2c_bus.clear()
        for title, bus in HEADER_I2C_BUSES:
            if bus in buses: self.i2c_bus.addItem(title, bus)
        if not buses: self.i2c_bus.addItem('图片中的 I2C 总线未在设备上发现', '')
        self.i2c_bus.blockSignals(False)
        available_pwm = set(data.get('pwm') or [])
        self.pwm_chip.blockSignals(True); self.pwm_chip.clear()
        for title, chip in HEADER_PWM:
            self.pwm_chip.addItem(title, chip)
            item = self.pwm_chip.model().item(self.pwm_chip.count() - 1)
            if '未发现' in title or (available_pwm and chip not in available_pwm): item.setEnabled(False)
        self.pwm_chip.blockSignals(False)
        spi = data.get('spi') or []
        self.spi_output.setText('SPI 排针：' + HEADER_SPI + ' · 设备：' + ('、'.join(spi) if spi else '未发现 /dev/spidev*'))
        self.state.setText(f'已读取 · GPIO 芯片 {len(data.get("gpiochips", []))} · I2C {len(buses)} · SPI {len(spi)} · PWM {len(available_pwm)}')

    def run_gpio(self):
        if not self.owner.require_device() or self.owner.busy: return
        action = self.gpio_action.currentData(); number = self.gpio_number.currentData(); value = self.gpio_value.currentData()
        if action in {'input', 'output', 'write'} and not self.owner.ask('确认 GPIO 操作', f'将操作 {self.gpio_number.currentText()}，可能改变引脚方向或电平。请确认没有连接冲突，且外部电路使用 3.3V 电平。'): return
        self.owner.work(lambda: self.owner.call('pin-gpio', {'number': number, 'action': action, 'value': value}), self.render_gpio, '正在执行 GPIO 操作…')

    def render_gpio(self, data):
        self.gpio_result.setText(f'{self.gpio_number.currentText()} · 方向：{data.get("direction", "未知")} · 电平：{data.get("value", "未知")}')

    def scan_i2c(self):
        if not self.owner.require_device() or self.owner.busy: return
        bus = self.i2c_bus.currentData() or self.i2c_bus.currentText()
        self.owner.work(lambda: self.owner.call('pin-i2c-scan', {'bus': bus}), lambda data: self.i2c_output.setPlainText(data.get('output', '') or '未返回扫描结果'), '正在扫描 I2C 地址…')

    def read_pwm(self):
        if not self.owner.require_device() or self.owner.busy: return
        self.owner.work(lambda: self.owner.call('pin-pwm', {'chip': self.pwm_chip.currentData() or self.pwm_chip.currentText(), 'channel': self.pwm_channel.value(), 'action': 'status'}), self.render_pwm, '正在读取 PWM 状态…')

    def apply_pwm(self):
        if not self.owner.require_device() or self.owner.busy: return
        period = self.pwm_period.value(); duty = self.pwm_duty.value()
        if duty > period: self.owner.notify('占空比不能大于周期。', True); return
        if not self.owner.ask('确认 PWM 配置', f'将配置 {self.pwm_chip.currentText()} 通道 {self.pwm_channel.value()}：周期 {period} ns，占空比 {duty} ns。请确认引脚复用和外部电路。'): return
        data = {'chip': self.pwm_chip.currentData() or self.pwm_chip.currentText(), 'channel': self.pwm_channel.value(), 'action': 'configure', 'period': period, 'duty': duty, 'enable': self.pwm_enable.isChecked()}
        self.owner.work(lambda: self.owner.call('pin-pwm', data), self.render_pwm, '正在应用 PWM 配置…')

    def render_pwm(self, data):
        self.pwm_result.setText(f'{data.get("chip")} 通道 {data.get("channel")} · 周期 {data.get("period")} ns · 占空比 {data.get("duty")} ns · '+('启用' if str(data.get('enable')) == '1' else '停用'))

    def reset(self):
        self.generation += 1; self.state.setText('未读取硬件资源'); self.model.setText('连接泰山派后读取 GPIO、I2C、SPI、PWM 节点。'); self.gpio_result.setText('尚未读取 GPIO 状态。'); self.i2c_output.clear(); self.spi_output.setText('SPI 排针：' + HEADER_SPI + ' · 尚未读取设备节点'); self.pwm_result.setText('尚未读取 PWM 状态。')
