"""RGB LED control panel."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QCheckBox, QSizePolicy


class RgbPanel(QWidget):
    PRESETS = [('关闭', (0, 0, 0)), ('红色', (1, 0, 0)), ('绿色', (0, 1, 0)), ('蓝色', (0, 0, 1)),
               ('黄色', (1, 1, 0)), ('青色', (0, 1, 1)), ('紫色', (1, 0, 1)), ('白色', (1, 1, 1))]

    def __init__(self, owner, label, button, card):
        super().__init__(); self.owner = owner; self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(14)
        top = QHBoxLayout(); top.addWidget(button('‹ 返回插件中心', owner.close_plugin)); top.addStretch(); self.state = label('未读取灯状态', 'subtle'); top.addWidget(self.state); root.addLayout(top)
        frame, box = card(); box.addWidget(label('RGB 灯控制', 'section')); box.addWidget(label('控制泰山派板载 RGB 指示灯。当前硬件每个颜色通道支持亮 / 灭两档。', 'subtle', True))
        self.checks = {}
        grid = QGridLayout(); grid.setHorizontalSpacing(16); grid.setVerticalSpacing(12)
        for col, (key, title, color) in enumerate((('r', '红色 R', '#e05248'), ('g', '绿色 G', '#31a866'), ('b', '蓝色 B', '#438bd8'))):
            check = QCheckBox(title); check.setProperty('rgbChannel', key); check.setStyleSheet(f'QCheckBox {{ color: {color}; font-weight: 600; }}'); self.checks[key] = check; grid.addWidget(check, 0, col)
        box.addLayout(grid); root.addWidget(frame)
        frame, box = card(); box.addWidget(label('颜色预设', 'section')); presets = QGridLayout()
        for i, (title, values) in enumerate(self.PRESETS):
            presets.addWidget(button(title, lambda checked=False, v=values: self.apply_values(v), 'primary' if title == '白色' else 'secondary'), i // 4, i % 4)
        box.addLayout(presets); row = QHBoxLayout(); row.addWidget(button('读取当前状态', self.refresh)); row.addStretch(); row.addWidget(button('应用当前选择', self.apply, 'primary')); box.addLayout(row); self.detail = label('红 R · 绿 G · 蓝 B', 'caption'); box.addWidget(self.detail); root.addWidget(frame)
        frame, box = card(); box.addWidget(label('使用说明', 'section')); box.addWidget(label('选择颜色通道后点击“应用当前选择”。预设颜色会立即写入板端 LED 节点；关闭软件不会改变灯的当前状态。', 'subtle', True)); root.addWidget(frame)

    def values(self): return tuple(int(self.checks[c].isChecked()) for c in 'rgb')

    def set_values(self, values):
        for c, value in zip('rgb', values): self.checks[c].setChecked(bool(value))
        self.detail.setText(f'红 R：{values[0]} · 绿 G：{values[1]} · 蓝 B：{values[2]}')

    def refresh(self):
        if not self.owner.require_device(): return
        self.owner.work(lambda: self.owner.call('rgb-status'), self.render, '正在读取 RGB 灯状态…')

    def render(self, data):
        values = tuple(data.get(c, 0) for c in 'rgb'); self.set_values(values); self.state.setText('状态已读取'); self.state.setProperty('online', True); self.state.style().unpolish(self.state); self.state.style().polish(self.state)

    def apply_values(self, values):
        self.set_values(values); self.apply()

    def apply(self):
        if not self.owner.require_device(): return
        values = self.values()
        self.owner.work(lambda: self.owner.call('rgb-set', dict(zip('rgb', values))), self.render_applied, '正在设置 RGB 灯…')

    def render_applied(self, data):
        self.render(data); self.state.setText('RGB 灯已更新')

    def reset(self):
        self.set_values((0, 0, 0)); self.state.setText('未读取灯状态')
