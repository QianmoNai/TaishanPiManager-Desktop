"""Manual, anonymous release checks. Never download or execute update code."""
import json
import re
from urllib.parse import quote

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit

APP_VERSION = '2.65'
RELEASES_URL = 'https://gitee.com/qianmonai/TaishanPiManager-Desktop/releases'
API_URL = 'https://gitee.com/api/v5/repos/qianmonai/TaishanPiManager-Desktop/releases/latest'
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
    # Gitee public release responses may omit the GitHub-specific draft field.
    if not isinstance(data, dict) or data.get('draft', False) is not False or data.get('prerelease') is not False:
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
        self.redirect_count = 0
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
        notice = QLabel('仅在点击时连接 Gitee，不需要登录或连接开发板。不上传设备信息，不自动下载或安装。')
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
        self.redirect_count = 0
        self.release_url = RELEASES_URL
        self.notes.clear()
        self.status.setText('正在检查 Gitee 最新正式发布版…')
        self.check_button.setEnabled(False)
        self.deadline.start(15000)
        self.start_request(QUrl(API_URL))

    def start_request(self, url):
        request = QNetworkRequest(url)
        request.setRawHeader(b'Accept', b'application/json')
        request.setRawHeader(b'User-Agent', ('TaishanPiManager/' + APP_VERSION).encode('ascii'))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setTransferTimeout(12000)
        self.reply = self.manager.get(request)
        self.reply.setReadBufferSize(MAX_RESPONSE + 1)
        self.reply.readyRead.connect(self.read_data)
        self.reply.finished.connect(self.complete)

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
        self.reply = None
        code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        error = reply.error()
        error_name = error.name
        diagnostics = f'更新源：Gitee\nHTTP：{code if code is not None else "未收到响应"}\nQt 网络状态：{error_name}\n接口：{API_URL}'
        try:
            if self.closed:
                return
            if self.failure:
                raise ValueError(self.failure)
            if code in (301, 302, 303, 307, 308):
                target = reply.attribute(QNetworkRequest.Attribute.RedirectionTargetAttribute)
                target = reply.url().resolved(target) if isinstance(target, QUrl) else QUrl()
                if (not target.isValid() or target.scheme() != 'https' or target.host() != 'gitee.com'
                        or target.port(443) != 443 or target.userInfo()
                        or not target.path().startswith('/api/v5/repos/qianmonai/TaishanPiManager-Desktop/releases')
                        or target.hasQuery() or target.hasFragment()):
                    raise ValueError('接口跳转到非预期地址，已停止。请手动查看发布页。')
                if self.redirect_count >= 3:
                    raise ValueError('接口重定向次数过多，已停止。')
                self.redirect_count += 1
                self.payload.clear()
                self.start_request(target)
                return
            if code in (403, 429):
                raise ValueError('Gitee 请求被限制，请稍后重试或手动打开发布页。')
            if code == 404:
                raise ValueError('未找到可访问的正式发布版；仓库可能为私有或尚未发布，请手动查看发布页。')
            if error != QNetworkReply.NetworkError.NoError or code != 200:
                hints = {
                    'SslHandshakeFailedError': 'TLS 握手失败，请检查系统时间、证书或 HTTPS 代理；不要关闭证书校验。',
                    'HostNotFoundError': '无法解析 Gitee 域名，请检查 DNS。',
                    'ConnectionRefusedError': '连接被拒绝，请检查网络或代理。',
                    'ProxyConnectionRefusedError': '代理连接被拒绝，请检查系统代理是否可用。',
                    'ProxyAuthenticationRequiredError': '系统代理要求认证，请检查代理配置。',
                    'TimeoutError': '网络请求超时，请稍后重试。',
                    'RemoteHostClosedError': '远端提前关闭连接，请检查网络或代理后重试。',
                }
                raise ValueError(hints.get(error_name, '接口请求失败，具体状态见下方诊断信息。'))
            release = parse_release(bytes(self.payload))
            self.release_url = release['url']
            self.notes.setPlainText(release['notes'])
            self.status.setText(('发现新版本：' if release['newer'] else '未发现比当前程序更新的正式版本；线上版本：') + release['tag'])
        except ValueError as exc:
            self.status.setText('检查失败：' + str(exc))
            self.notes.setPlainText(diagnostics + '\n\n说明：' + str(exc))
        finally:
            reply.deleteLater()
            if self.reply is None:
                self.deadline.stop()
                self.check_button.setEnabled(True)

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
