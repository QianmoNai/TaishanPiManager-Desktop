from PySide6.QtCore import QStandardPaths, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QPushButton,
                             QPlainTextEdit, QFileDialog, QMessageBox, QDialogButtonBox)
from external_plugins import PluginStore, read_package


class ExternalPlugins:
    def __init__(self, center, label, button, card):
        self.center = center
        self.owner = center.owner
        self.label, self.button, self.card = label, button, card
        self.store = PluginStore(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation) + '/third-party-plugins')

    def import_package(self):
        path, _ = QFileDialog.getOpenFileName(self.center, '导入第三方插件包', '', '插件包 (*.zip)')
        if not path:
            return
        try:
            manifest, _ = read_package(path)
            if not self.owner.ask('导入第三方插件', f"{manifest['name']} · {manifest['version']}\n作者（自述）：{manifest['author']}\nID：{manifest['id']}\n\n仅保存到电脑，不会自动执行。插件未经签名认证，运行时可能拥有设备 root 权限。是否导入？"):
                return
            self.store.install(path)
            self.reload()
        except Exception as exc:
            QMessageBox.warning(self.center, '导入失败', str(exc))

    def reload(self):
        for entry in self.center.cards[:]:
            if entry.get('external'):
                self.center.grid.removeWidget(entry['widget'])
                entry['widget'].deleteLater()
                self.center.cards.remove(entry)
        packages = self.store.packages()
        for path, manifest in packages:
            frame, box = self.card()
            for text, style in [(manifest['name'], 'section'),
                                (manifest['version'] + ' · ' + manifest['author'], 'caption'),
                                (manifest['description'], 'subtle'),
                                ('第三方 · 已导入本机 · 板端状态未检测', 'caption')]:
                widget = self.label(text, style, True)
                widget.setTextFormat(Qt.TextFormat.PlainText)
                box.addWidget(widget)
            box.addWidget(self.button('打开操作', lambda checked=False,p=path:self.open(p)))
            box.addWidget(self.button('移除本地插件包', lambda checked=False,p=path:self.remove(p)))
            self.center.cards.append(dict(key=manifest['id'], name=manifest['name'],
                                         description=manifest['description'], category='第三方',
                                         widget=frame, external=True))
        self.center.summary.setText(f'4 款可安装插件 · 7 项内置工具 · {len(packages)} 个第三方包')
        self.center.filter_cards()

    def remove(self, path):
        if self.owner.ask('移除本地插件包', '只移除电脑中的插件入口，包会归档以便恢复。不会卸载设备上的程序或停止服务；如需清理设备，请先运行作者提供的卸载操作。继续？'):
            try:
                self.store.remove(path)
                self.reload()
            except Exception as exc:
                QMessageBox.warning(self.center, '移除失败', str(exc))

    def open(self, path):
        try:
            manifest, scripts = read_package(path)
        except Exception as exc:
            QMessageBox.warning(self.center, '插件无法打开', str(exc))
            return
        dialog = QDialog(self.center)
        dialog.setWindowTitle(manifest['name'] + ' · 第三方插件')
        dialog.resize(720, 520)
        layout = QVBoxLayout(dialog)
        notice = QLabel('脚本在所选设备上以 ADB Shell 权限运行，不是沙箱。仅运行可信作者的插件。')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        output = QPlainTextEdit()
        output.setReadOnly(True)
        output.setMaximumBlockCount(2000)
        def execute(action):
            if self.owner.busy or not self.owner.require_device():
                return
            serial = self.owner.serial
            script = scripts[action['script']]
            confirm = QDialog(dialog)
            confirm.setWindowTitle('确认在设备上执行脚本')
            confirm.resize(700, 500)
            content = QVBoxLayout(confirm)
            target = QLabel('目标设备：' + serial + '\n操作：' + action['title'])
            target.setTextFormat(Qt.TextFormat.PlainText)
            content.addWidget(target)
            preview = QPlainTextEdit()
            preview.setReadOnly(True)
            preview.setPlainText(script)
            content.addWidget(preview)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(confirm.accept)
            buttons.rejected.connect(confirm.reject)
            content.addWidget(buttons)
            if confirm.exec() != QDialog.DialogCode.Accepted:
                return
            if self.owner.busy or serial != self.owner.serial:
                return
            output.setPlainText('执行中 · ' + serial)
            def done(result):
                data, error, code = result
                output.setPlainText(f'设备：{serial}\n退出码：{code}\n' + data.decode('utf-8', 'replace')[-65536:] + '\n' + error)
            def run():
                try:
                    return self.owner.api.adb.run(
                        ['-s', serial, 'shell', '-T', 'timeout 60 sh -s'],
                        input_data=script.encode('utf-8'), timeout=65, check=False)
                except Exception as exc:
                    return b'', str(exc), -1
            self.owner.work(run, done, '正在执行第三方插件…')
        for action in manifest['actions']:
            btn = QPushButton(action['title'])
            btn.clicked.connect(lambda checked=False,a=action:execute(a))
            layout.addWidget(btn)
        layout.addWidget(output, 1)
        dialog.exec()
