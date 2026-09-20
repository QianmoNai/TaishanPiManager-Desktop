"""Selection controls that leave mouse-wheel scrolling to their parent page."""
from PySide6.QtWidgets import QComboBox as QtComboBox


class QComboBox(QtComboBox):
    def wheelEvent(self, event):
        # Never change a closed selector, even when it has keyboard focus.
        # The popup list still handles its own scrolling when opened.
        event.ignore()
