"""Approved flat-vector logo, embedded for the standalone source edition."""

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

LOGO_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512" role="img" aria-labelledby="title desc">\n  <title id="title">VelvetStream</title>\n  <desc id="desc">A velvet purple V merging into a continuous cyan-to-magenta soundwave.</desc>\n  <defs>\n    <linearGradient id="velvet" x1="92" y1="116" x2="362" y2="390" gradientUnits="userSpaceOnUse">\n      <stop stop-color="#3B1E54"/>\n      <stop offset="1" stop-color="#33236B"/>\n    </linearGradient>\n    <linearGradient id="signal" x1="116" y1="136" x2="438" y2="212" gradientUnits="userSpaceOnUse">\n      <stop stop-color="#00F0FF"/>\n      <stop offset="0.5" stop-color="#7680FF"/>\n      <stop offset="1" stop-color="#FF007A"/>\n    </linearGradient>\n    <filter id="glow" x="-25%" y="-25%" width="150%" height="150%" color-interpolation-filters="sRGB">\n      <feGaussianBlur stdDeviation="5"/>\n    </filter>\n    <path id="wave" d="M116 136 L218 342 C226 358 237 358 245 342 L306 204 C313 188 321 188 328 207 L341 244 C348 263 357 263 364 240 L384 174 C391 151 400 151 407 174 L419 209 C423 220 430 226 438 226"/>\n  </defs>\n  <path fill="url(#velvet)" d="M75 110 Q69 99 83 99 H120 Q129 99 133 108 L232 311 L319 108 Q323 99 333 99 H374 Q387 99 381 112 L262 387 Q258 397 246 397 H219 Q207 397 203 387 Z"/>\n  <use href="#wave" fill="none" stroke="url(#signal)" stroke-width="16" stroke-linecap="round" stroke-linejoin="round" opacity="0.32" filter="url(#glow)"/>\n  <use href="#wave" fill="none" stroke="url(#signal)" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>\n</svg>\n'


def logo_pixmap(size: int = 72) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(LOGO_SVG.encode("utf-8")))
    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pixmap


def application_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(logo_pixmap(size))
    return icon
