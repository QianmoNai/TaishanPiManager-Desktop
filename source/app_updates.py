"""Manual, anonymous release checks. Never download or execute update code."""
import json
import re
from urllib.parse import quote

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit

APP_VERSION = '2.63'
RELEASES_URL = 'https://github.com/QianmoNai/TaishanPiManager-Desktop/releases'
API_URL = 'https://api.github.com/repos/QianmoNai/TaishanPiManager-Desktop/releases/latest'
MAX_RESPONSE = 512 * 1024


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'v?\d{1,6}\.\d{1,6}(?:\.\d{1,6})?(?:-release)?', value):
        raise ValueError('发布版本格式无法识别，请手动查看发布页。')
    parts = value.removeprefix('v').removesuffix('-release').split('.')
    return tuple(map(int, parts)) + (0,) * (3 - len(parts))


def parse_release(raw, current=APP_VERSION):
    if len(raw) > MAX_RESPONSE:
        raise ValueError('更新信息超过大小限制。')
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError('服务器返回的更新信息无效。') from exc
    if not isinstance(data, dict) or data.get('draft') is not False or data.get('prerelease') is not False:
        raise ValueError('未收到有效的正式发布信息，请手动查看发布页。')
    tag = data.get('tag_name')
    latest = version_tuple(tag)
    body = data.get('body') or '该版本未提供更新说明。'
    if not isinstance(body, str):
        raise ValueError('更新说明格式无效。')
    # Build a trusted URL rather than opening arbitrary server-provided links.
    return dict(tag=tag, newer=latest > version_tuple(current),
                notes=body[:32000], url=RELEASES_URL + '/tag/' + quote(tag, safe=''))


class UpdateDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('检查更新 · 泰山派设备管理')
        self.resize(620, 460)
        self.reply = None
        self.payload = bytearray()
        self.failure = ''
        self.release_url = RELEASES_URL
        self.closed = False
        self.manager = QNetworkAccessManager(self)
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(lambda: self.abort('请求超时，请检查网络后重试。'))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)
        version = QLabel('当前程序版本：v' + APP_VERSION)
        version.setObjectName('section')
        layout.addWidget(version)
        self.status = QLabel('点击“检查更新”查询最新正式发布版。')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        notice = QLabel('仅在点击时连接 GitHub，不需要登录或连接开发板。不上传设备信息，不自动下载或安装。')
        notice.setObjectName('caption')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setPlaceholderText('更新说明将以纯文本显示。')
        layout.addWidget(self.notes, 1)
        buttons = QHBoxLayout()
        self.check_button = QPushButton('检查更新')
        self.check_button.setProperty('kind', 'primary')
        self.check_button.clicked.connect(self.check)
        self.release_button = QPushButton('打开发布页')
        self.release_button.clicked.connect(self.open_release)
        close_button = QPushButton('关闭')
        close_button.clicked.connect(self.reject)
        for button in (self.check_button, self.release_button, close_button):
            button.setMinimumHeight(38)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.finished.connect(self.cancel)

    def check(self):
        if self.closed or self.reply is not None:
            return
        self.payload.clear()
        self.failure = ''
        self.release_url = RELEASES_URL
        self.notes.clear()
        self.status.setText('正在检查 GitHub 最新正式发布版…')
        self.check_button.setEnabled(False)
        request = QNetworkRequest(QUrl(API_URL))
        request.setRawHeader(b'Accept', b'application/vnd.github+json')
        request.setRawHeader(b'User-Agent', ('TaishanPiManager/' + APP_VERSION).encode('ascii'))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setTransferTimeout(12000)
        self.reply = self.manager.get(request)
        self.reply.setReadBufferSize(MAX_RESPONSE + 1)
        self.reply.readyRead.connect(self.read_data)
        self.reply.finished.connect(self.complete)
        self.deadline.start(15000)

    def abort(self, reason):
        if self.reply is not None:
            self.failure = reason
            self.reply.abort()

    def read_data(self):
        if self.reply is None or self.closed or self.failure:
            return
        self.payload.extend(bytes(self.reply.read(MAX_RESPONSE + 1 - len(self.payload))))
        if len(self.payload) > MAX_RESPONSE:
            self.abort('更新信息超过大小限制。')

    def complete(self):
        if self.reply is None:
            return
        reply = self.reply
        self.read_data()
        self.deadline.stop()
        self.reply = None
        self.check_button.setEnabled(True)
        code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        try:
            if self.closed:
                return
            if self.failure:
                raise ValueError(self.failure)
            if code in (403, 429):
                raise ValueError('GitHub 请求被限制，请稍后重试或手动打开发布页。')
            if code == 404:
                raise ValueError('未找到可访问的正式发布版；仓库可能为私有或尚未发布，请手动查看发布页。')
            if reply.error() != QNetworkReply.NetworkError.NoError or code != 200:
                raise ValueError('无法获取更新信息，请检查网络或手动打开发布页。')
            release = parse_release(bytes(self.payload))
            self.release_url = release['url']
            self.notes.setPlainText(release['notes'])
            self.status.setText(('发现新版本：' if release['newer'] else '未发现比当前程序更新的正式版本；线上版本：') + release['tag'])
        except ValueError as exc:
            self.status.setText('检查失败：' + str(exc))
        finally:
            reply.deleteLater()

    def open_release(self):
        if not QDesktopServices.openUrl(QUrl(self.release_url)):
            self.status.setText('无法打开浏览器，请手动访问：' + self.release_url)

    def cancel(self, *_):
        self.closed = True
        self.deadline.stop()
        if self.reply is not None:
            reply, self.reply = self.reply, None
            reply.finished.disconnect(self.complete)
            reply.readyRead.disconnect(self.read_data)
            reply.abort()
            reply.deleteLater()
